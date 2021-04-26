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
from discord.ext import tasks
from discord_slash import cog_ext, SlashContext
from discord_slash.utils.manage_commands import create_option

import cogs.cog_utils as utils
from cogs.cog_utils import guild_ids, display_delta
from cogs.permissions import has_server_perms, has_bot_perms

logger = logging.getLogger(__name__)


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
            channels = await self.bot.get_cog("Channels").get_home_channels(guild)
            for channel in channels:
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

    # @commands.Cog.listener()
    # async def on_member_join(self, member):
    #     channel = await self.get_home_channel(member.guild)
    #     if utils.can_bot_respond(self.bot, channel):
    #         await channel.send(f"{self.get_greeting(member)}\nWelcome!")
    #         logger.info(f"Greeted new guild member {member}")

    def get_last_activity_time(self) -> Optional[datetime]:
        try:
            with open(self.activity_file_path, 'r') as startup_time_file:
                return datetime.strptime(startup_time_file.read(), self.activity_time_format)
        except (ValueError, OSError):
            return None

    def update_last_activity_time(self):
        """writes to the startup file current _started_at file"""
        last_activity = self._last_active_at.strftime(self.activity_time_format)
        with open(self.activity_file_path, 'w') as activity_time_file:
            activity_time_file.write(last_activity)
            logger.info(f"Updated last activity time file: {last_activity}")

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
        if not message and not attachment_link:
            await ctx.send("Can't send an empty message", hidden=True)
            return

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
        current_homes = await self.bot.get_cog("Channels").get_home_channels(ctx.guild)
        if current_homes:
            if len(current_homes) == 1:
                await ctx.send(f"My home channel is {current_homes[0].mention}", hidden=True)
            else:
                current_homes = ", ".join([channel.mention for channel in current_homes])
                await ctx.send(f"My home channels are {current_homes}", hidden=True)
        else:
            await ctx.send("I'm homeless T_T", hidden=True)

    async def send_home_channels_message(self, message: str, attachment=None, attachment_name=""):
        channels = await self.bot.get_cog("Channels").get_home_channels()
        for channel in channels:
            if utils.can_bot_respond(self.bot, channel):
                file = discord.File(io.BytesIO(attachment), attachment_name) if attachment is not None else None
                await channel.send(message, file=file)
            elif channel:
                logger.warning(f"Bot can't send messages to home channel #{channel.name} at '{channel.guild.name}'")

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
