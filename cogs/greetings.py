import random
import logging

from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from datetime import datetime, timedelta

import io
import aiohttp
import discord
from discord.ext import tasks, commands
from discord_slash import cog_ext, SlashContext
from discord_slash.utils.manage_commands import create_option, create_choice
from tortoise import fields
from tortoise.models import Model

import cogs.cog_utils as utils
from cogs.cog_utils import guild_ids
from cogs.permissions import has_bot_perms, has_server_perms

logger = logging.getLogger(__name__)

activities = {
    "YouTube Together": "755600276941176913",
    "Poker Night": "755827207812677713",
    "Betrayal.io": "773336526917861400",
    "Fishington.io": "814288819477020702"
}


class GuildGreetings(Model):
    guild_id = fields.BigIntField(unique=True)
    greeting_text = fields.TextField(default="")


class Greetings(utils.AutoLogCog, utils.StartupCog):
    """Simple greetings and welcome commands"""

    activity_time_format = "%H:%M %d.%m.%Y"

    def __init__(self, bot):
        utils.StartupCog.__init__(self)
        utils.AutoLogCog.__init__(self, logger)
        self.bot = bot
        self.activity_file_path = utils.abs_join("last_activity")
        self._last_greeted_member = None
        self._started_at = None
        self._last_active_at = None

    async def on_startup(self):
        logger.info(f"Logged in as {self.bot.user}")

        guilds_list = [f"[{g.name}: {g.member_count} members]" for g in self.bot.guilds]
        logger.info(f"Current servers: {', '.join(guilds_list)}")

        last_activity = self.get_file_activity_time()
        self._started_at = datetime.utcnow()
        self._last_active_at = datetime.utcnow()
        self.update_activity_time_loop.start()

        # Don't send greetings if last activity was less than a 3 hours ago
        if last_activity is None or (self._last_active_at - last_activity > timedelta(hours=3)):
            await self.send_home_channels_message("Hello hello! I'm back online and ready to work!")

    @tasks.loop(hours=1)
    async def update_activity_time_loop(self):
        self._last_active_at = datetime.utcnow()
        self.update_file_activity_time()

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot:
            return
        channels = await self.bot.get_cog("Channels").get_welcome_channels(member.guild)
        if not channels:
            return

        message = await self.get_welcome_message(member)
        if not message:
            logger.info("Member greeting is disabled")
            return
        for greeting_channel in channels:
            if utils.can_bot_respond(greeting_channel):
                await greeting_channel.send(message, allowed_mentions=discord.AllowedMentions.none())
        logger.info(f"Greeted new guild member {member}")

    def get_file_activity_time(self) -> Optional[datetime]:
        try:
            with open(self.activity_file_path, 'r') as startup_time_file:
                return datetime.strptime(startup_time_file.read(), self.activity_time_format)
        except (ValueError, OSError):
            return None

    def update_file_activity_time(self):
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

        await ctx.defer()

        file = None
        name = ""
        if attachment_link is not None:
            async with aiohttp.ClientSession() as session:
                async with session.get(attachment_link) as response:
                    if response.ok:
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
            if channel.guild not in self.bot.guilds:
                continue

            file = discord.File(io.BytesIO(attachment), attachment_name) if attachment is not None else None
            await channel.send(message, file=file)

    def get_start_time(self) -> datetime:
        return self._started_at

    def get_last_activity_time(self) -> datetime:
        return self._last_active_at

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

    @staticmethod
    def get_member_welcome_message(member: discord.Member) -> str:
        """Returns personal part of the welcome message"""
        greetings = \
            ["Welcome, {}!",
             "Glad to see you here, {}!",
             "Welcome here, {}!",
             "{} just joined, welcome!",
             "Welcome, {}. We're not Discord, we demand pizza!",
             "Hello, {}. Have a good time here!",
             ]
        return random.choice(greetings).format(member.mention)

    @staticmethod
    async def get_welcome_message(member: discord.Member) -> Optional[str]:
        """Returns full welcome message for specific server and person"""
        guild_greeting = await GuildGreetings.get_or_none(guild_id=member.guild.id)
        if not guild_greeting:
            return None
        member_greeting = Greetings.get_member_welcome_message(member)
        return "\n".join([member_greeting, guild_greeting.greeting_text]) if guild_greeting.greeting_text else member_greeting

    @staticmethod
    async def set_guild_greeting_text(guild: discord.Guild, greeting_text: Optional[str]):
        (greeting, _) = await GuildGreetings.get_or_create(guild_id=guild.id)
        greeting.greeting_text = greeting_text
        await greeting.save()

    @staticmethod
    async def delete_welcome_message(guild: discord.Guild):
        greeting = await GuildGreetings.get_or_none(guild_id=guild.id)
        if greeting:
            await greeting.delete()

    @cog_ext.cog_subcommand(base="greeting", name="set",
                            description="Enables welcome message and sets server specific part of the this message",
                            options=[
                                create_option(
                                    name="message",
                                    description="Server specific part of the welcome message",
                                    option_type=str,
                                    required=False,
                                ),
                            ],
                            guild_ids=guild_ids)
    @has_server_perms()
    async def set_greeting(self, ctx: SlashContext, message: str = ""):
        await ctx.defer(hidden=True)
        await self.set_guild_greeting_text(ctx.guild, message)
        message = f"'{message}'" if message else message
        logger.db(f"{ctx.author} set greeting for guild {ctx.guild} to {message}")
        await ctx.send(f"Successfully set message to personal greeting {'and ' + message if message else 'only'}", hidden=True)

    @cog_ext.cog_subcommand(base="greeting", name="delete",
                            description="Disables welcome message for new people",
                            guild_ids=guild_ids)
    @has_server_perms()
    async def delete_greeting(self, ctx: SlashContext):
        await ctx.defer(hidden=True)
        await self.delete_welcome_message(ctx.guild)
        logger.db(f"{ctx.author} deleted greeting for guild {ctx.guild}'")
        await ctx.send(f"Successfully deleted welcome message! Bot won't send welcome message when new people joins", hidden=True)

    @cog_ext.cog_subcommand(base="greeting", name="greet",
                            description="Sends greeting for you or specific person",
                            options=[
                                create_option(
                                    name="member",
                                    description="Person to greet",
                                    option_type=discord.Member,
                                    required=False,
                                ),
                                create_option(
                                    name="hidden",
                                    description="If enabled, it'll be only you who see the greeting",
                                    option_type=bool,
                                    required=False,
                                ),
                            ],
                            guild_ids=guild_ids)
    async def greet(self, ctx: SlashContext, member: discord.Member = None, hidden: bool = False):
        member = member or ctx.author
        message = await self.get_welcome_message(member)
        if not message:
            await ctx.send("Welcome message is disabled for the server", hidden=True)
            return

        await ctx.send(message, hidden=hidden, allowed_mentions=discord.AllowedMentions.none())

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
        if member and not isinstance(member, discord.Member):
            raise commands.BadArgument(f"Failed to get member '{member}' info!")

        if not utils.can_bot_respond(ctx.channel):
            raise commands.BadArgument(f"I can't send messages to this channel")  # This will be shown as hidden response, so we can do that

        member = member or ctx.author
        await ctx.send(self.get_greeting(member))

    async def get_activity_code(self, voice, application_id):
        url = f"https://discord.com/api/v8/channels/{voice.channel.id}/invites"
        api_json = {
            "max_age": 86400,
            "max_uses": 0,
            "target_application_id": f"{application_id}",
            "target_type": 2,
            "temporary": False,
            "validate": None
        }
        headers = {"Authorization": f"Bot {self.bot.token}",
                   "Content-Type": "application/json"
                   }

        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=api_json, headers=headers) as response:
                data = await response.json()
                code = data["code"]
                return code

    @staticmethod
    async def get_application_icon(application_id):
        async with aiohttp.ClientSession() as session:
            api_url = f"https://discord.com/api/v9/applications/{application_id}/rpc"
            async with session.get(api_url) as response:
                data = await response.json()
                icon_code = data["icon"]

            icon_url = f"https://cdn.discordapp.com/app-icons/{application_id}/{icon_code}.png"
            return icon_url

    @cog_ext.cog_slash(name="activity",
                       options=[create_option(
                           name="type",
                           description="Type of activity",
                           option_type=str,
                           required=True,
                           choices=[create_choice(name=name, value=name) for name in activities.keys()]
                       )],
                       connector={"type": "activity_type"},
                       guild_ids=guild_ids)
    async def start_activity(self, ctx: SlashContext, activity_type):
        """Creates an activity invite for voice channel you are in"""
        if not ctx.author.voice:
            await ctx.send(hidden=True, content="You need to be in a voice channel to start an activity!")
            return

        await ctx.defer()
        voice = ctx.author.voice
        application_id = activities[activity_type]
        code = await self.get_activity_code(voice, application_id)
        invite = f"https://discord.gg/{code}"
        icon = await self.get_application_icon(application_id)

        embed = discord.Embed(title="New voice channel activity started!", colour=utils.embed_color)
        embed.set_author(name=activity_type, icon_url=icon, url=invite)
        embed.set_thumbnail(url=icon)
        embed.description = f"**{activity_type}** activity just started in {voice.channel.mention}\n" \
                            f"[Click this link and join!]({invite})\n" \
                            f"This invite will expire after 1 day!"

        await ctx.send(embed=embed)


def setup(bot):
    bot.add_cog(Greetings(bot))
