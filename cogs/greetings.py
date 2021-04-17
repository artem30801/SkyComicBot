import io
import logging
import math
import random
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import aiohttp
import discord
from dateutil import relativedelta
from discord.ext import commands
from discord.ext import tasks
from discord_slash import cog_ext, SlashContext
from discord_slash.utils.manage_commands import create_option
from tortoise import fields
from tortoise.models import Model

import cogs.cog_utils as utils
from cogs.cog_utils import guild_ids
from cogs.permissions import has_server_perms, has_bot_perms

logger = logging.getLogger(__name__)


def display_delta(delta):
    d = {"month": delta.months,
         "day": delta.days,
         "hour": delta.hours,
         "minute": delta.minutes,
         "second": delta.seconds,
         }
    return ", ".join([f"{value} {key + 's' if value > 1 else key}" for key, value in d.items() if value > 0])


class HomeChannels(Model):
    guild_id = fields.BigIntField()
    channel_id = fields.BigIntField(null=True)


class Greetings(utils.AutoLogCog, utils.StartupCog):
    """Simple greetings and welcome commands"""

    activity_time_format = "%H:%M %d.%m.%Y"

    def __init__(self, bot):
        utils.StartupCog.__init__(self)
        utils.AutoLogCog.__init__(self, logger)
        self.bot = bot
        self.activity_file_path = utils.abs_join(bot.current_dir, "last_activity")
        self._last_greeted_member = None
        self._started_at = None
        self._last_active_at = None

    async def on_startup(self):
        logger.info(f"Logged in as {self.bot.user}")

        guilds_list = [f"[{g.name}: {g.member_count} members]" for g in self.bot.guilds]
        logger.info(f"Current servers: {', '.join(guilds_list)}")

        last_activity = self.get_last_activity_time()
        self._started_at = datetime.utcnow()
        self.update_activity_time_loop.start()

        # check home channels
        for guild in self.bot.guilds:
            channel = await self.get_home_channel(guild)

            if not utils.can_bot_respond(self.bot, channel) and channel is not None:
                logger.warning(f"Bot can't send messages to home channel #{channel.name} at '{guild.name}'")
                continue

        # Don't send greetings if last activity was less than a 3 hours ago
        if last_activity is None or (self._last_active_at - last_activity > timedelta(hours=3)):
            await self.send_home_channels_message("Hello hello! I'm back online and ready to work!")

    @tasks.loop(hours=1)
    async def update_activity_time_loop(self):
        self._last_active_at = datetime.utcnow()
        self.update_last_activity_time()

    @commands.Cog.listener()
    async def on_member_join(self, member):
        channel = await self.get_home_channel(member.guild)
        if utils.can_bot_respond(self.bot, channel):
            await channel.send(f"{self.get_greeting(member)}\nWelcome!")
            logger.info(f"Greeted new guild member {member}")

    def get_last_activity_time(self) -> Optional[datetime]:
        try:
            with open(self.activity_file_path, 'r') as startup_time_file:
                return datetime.strptime(startup_time_file.read(), self.activity_time_format)
        except (ValueError, OSError):
            return None

    def update_last_activity_time(self):
        """writes to the startup file current _started_at file"""
        with open(self.activity_file_path, 'w') as startup_time_file:
            startup_time_file.write(self._last_active_at.strftime(self.activity_time_format))

    @cog_ext.cog_subcommand(base="home", name="notify",
                            options=[
                                create_option(
                                    name="message",
                                    description="Message to send",
                                    option_type=str,
                                    required=False,
                                ),
                                create_option(
                                    name="attachment_link",
                                    description="Link to attachment to send",
                                    option_type=str,
                                    required=False,
                                )

                            ],
                            guild_ids=guild_ids)
    @has_bot_perms()
    async def home_channel_notify(self, ctx: SlashContext, message="", attachment_link=None):
        """Sends message with the attachment to the home channels of the guilds from the bot"""
        file = None
        name = ""
        if attachment_link is not None:
            async with aiohttp.ClientSession() as session:
                async with session.get(attachment_link) as response:
                    if response.status == 200:
                        file = await response.read()
                        name = Path(urlparse(attachment_link).path).name

        await self.send_home_channels_message(message, file, name)
        await ctx.send("Notification sent")
        logger.important(
            f"{self.format_caller(ctx)} sent global notification {message} with attachment {attachment_link}")

    @cog_ext.cog_subcommand(base="home", name="where", guild_ids=guild_ids)
    async def home_channel_where(self, ctx: SlashContext):
        """Shows where current home of the bot in this server is."""
        current_home = await self.get_home_channel(ctx.guild)
        if current_home is None:
            await ctx.send("I'm homeless T_T", hidden=True)
        else:
            await ctx.send(f"My home is at {current_home.mention}", hidden=True)

    @cog_ext.cog_subcommand(base="home", name="set",
                            options=[
                                create_option(
                                    name="new_home",
                                    description="My new home channel for notifications",
                                    option_type=discord.TextChannel,
                                    required=False,
                                )
                            ],
                            guild_ids=guild_ids)
    @commands.guild_only()
    @has_server_perms()
    async def home_channel_set(self, ctx: SlashContext, new_home: discord.TextChannel = None):
        """Sets specified channel (or current by default) as a home channel for the bot"""
        if new_home is None:
            new_home = ctx.channel

        logger.db(f"{ctx.author} trying to set home to '{new_home}' at guild '{ctx.guild}'")

        if not isinstance(new_home, discord.TextChannel):
            logger.db(f"'{new_home}' is not a text channel")
            await ctx.send("Hey! That's not a text channel!", hidden=True)
            return

        current_home = await self.get_home_channel(ctx.guild)

        if current_home == new_home:
            logger.db(f"'{new_home}' is already a home channel")
            await ctx.send(f"I'm already living at {new_home.mention}, but hey, thanks for the invitation!",
                           hidden=True)
            return

        await self.set_home_channel(ctx.guild, new_home)
        logger.db(f"Home channel for '{ctx.guild}' set to '{new_home}'")

        cmd_response = f"Moving to the {new_home.mention}."
        if not utils.can_bot_respond(ctx.bot, new_home):
            logger.db("Bot is muted at the new home channel")
            cmd_response += ".. You know I'm muted there, right? -_-"

        await ctx.send(cmd_response, hidden=True)
        if ctx.channel != current_home and utils.can_bot_respond(ctx.bot, current_home):
            await current_home.send(cmd_response)

        if utils.can_bot_respond(ctx.bot, new_home):
            await new_home.send("From now I'm living here, yay!")

    @cog_ext.cog_subcommand(base="home", name="reset", guild_ids=guild_ids)
    @commands.guild_only()
    @has_server_perms()
    async def home_channel_remove(self, ctx: SlashContext):
        """Resets home channel for the bot"""
        logger.db(f"{self.format_caller(ctx)} trying to reset home channel at guild '{ctx.guild}'")

        old_home = await self.get_home_channel(ctx.guild)

        if old_home is None:
            logger.db(f"Bot already has no home channel at '{ctx.guild}'")
            await ctx.send("I'm already homeless T_T")
            return

        await HomeChannels.update_or_create(guild_id=ctx.guild.id, defaults={"channel_id": None})
        logger.db(f"Reset home channel at '{ctx.guild}'")

        if utils.can_bot_respond(ctx.bot, old_home):
            await old_home.send("Moved away in search of a better home")

        await ctx.send("I'm homeless now >_<")

    @staticmethod
    async def set_home_channel(guild: discord.Guild, channel: discord.TextChannel):
        """Sets bots home channel for the server"""
        channel_id = channel.id if channel is not None else None
        await HomeChannels.update_or_create(guild_id=guild.id, defaults={"channel_id": channel_id})

    @staticmethod
    async def get_home_channel(guild: discord.Guild) -> discord.TextChannel:
        home_channel = await HomeChannels.get_or_none(guild_id=guild.id)
        if home_channel is None:
            return guild.system_channel
        if home_channel.channel_id is None:
            return None
        return guild.get_channel(home_channel.channel_id)

    async def send_home_channels_message(self, message: str, attachment=None, attachment_name=""):
        for guild in self.bot.guilds:
            channel = await self.get_home_channel(guild)
            if utils.can_bot_respond(self.bot, channel):
                file = discord.File(io.BytesIO(attachment), attachment_name) if attachment is not None else None
                await channel.send(message, file=file)
            elif channel:
                logger.warning(f"Bot can't send messages to home channel #{channel.name} at '{guild.name}'")

    def get_greeting(self, member):
        greetings = \
            ["Hi, {}!",
             "Hello, {}~",
             "Yo, {}!",
             "Sup, {}",
             "{}! Good to see you!",
             "The Skybox waited for you, {}!",
             "Greetings, {} =)",
             "/-//- /--/ {}.",
             "Oh! Hello there, you must be {}.",
             "G'day, {}!",
             "Howdy, {}",
             "Arigato, {}-san",
             "Hoi, {}",
             ]
        message = random.choice(greetings).format(member.display_name)

        if self._last_greeted_member is not None and self._last_greeted_member.id == member.id:
            message = f"{message}\nThis feels oddly familiar..."

        self._last_greeted_member = member
        return message

    @cog_ext.cog_slash(options=[
        create_option(
            name="member",
            description="Member who bot should greet",
            option_type=discord.Member,
            required=False,
        )
    ],
        guild_ids=guild_ids)
    async def hello(self, ctx: SlashContext, member: discord.Member = None):
        """Says hello to you or mentioned member."""
        member = member or ctx.author
        await ctx.send(self.get_greeting(member))

    @cog_ext.cog_slash(guild_ids=guild_ids)
    async def uptime(self, ctx: SlashContext):
        """Shows how long the bot was running for."""
        now = datetime.utcnow()
        delta = relativedelta.relativedelta(now, self._started_at)
        await ctx.send(f"I was up and running since {self._started_at.strftime('%d/%m/%Y, %H:%M:%S')} (GMT) "
                       f"for {display_delta(delta)}")

    @cog_ext.cog_slash(guild_ids=guild_ids)
    async def latency(self, ctx: SlashContext):
        """Shows latency between bot and Discord servers. Use to check if there are network problems."""
        await ctx.send(f"Current latency: {math.ceil(self.bot.latency * 100)} ms")


def setup(bot):
    bot.add_cog(Greetings(bot))
