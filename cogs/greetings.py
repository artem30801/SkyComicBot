import math
import random
import logging
import psutil

from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from datetime import datetime, timedelta
from dateutil import relativedelta

import os
import io
import aiohttp
import discord
from discord.ext import tasks
from discord_slash import cog_ext, SlashContext
from discord_slash.utils.manage_commands import create_option, create_choice

import cogs.cog_utils as utils
import cogs.db_utils as db_utils
from cogs.cog_utils import guild_ids, display_delta
from cogs.permissions import has_server_perms, has_bot_perms

logger = logging.getLogger(__name__)

activities = {
    "YouTube Together": "755600276941176913",
    "Poker Night": "755827207812677713",
    "Betrayal.io": "773336526917861400",
    "Fishington.io": "814288819477020702"
}


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
        self._last_active_at = datetime.utcnow()
        self.update_activity_time_loop.start()

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

    @cog_ext.cog_subcommand(base="check", name="bot", guild_ids=guild_ids)
    @has_bot_perms()
    async def bot_info(self, ctx: SlashContext):
        """Shows bot information, statistics and status"""
        now = datetime.utcnow()
        delta = relativedelta.relativedelta(now, self._started_at)
        embed = discord.Embed(colour=utils.embed_color)
        embed.title = "Bot check results"
        embed.set_author(name=self.bot.user.name, icon_url=self.bot.user.avatar_url)

        no = "Not available"
        git_hash = (await utils.run(f"(cd {self.bot.current_dir}; git describe --always)"))[0] or no
        commits_behind = (await utils.run(f"(cd {self.bot.current_dir}; git fetch; "
                                          f"git rev-list HEAD...origin/master --count)"))[0]
        commits_behind = commits_behind.strip()
        commits_behind = int(commits_behind) or "Up to date" if commits_behind else no
        embed.add_field(name="Version",
                        value=utils.format_lines({
                            "Version number": self.bot.version,
                            "Commit Hash": git_hash.strip(),
                            "Commits Behind": commits_behind,
                        }))

        embed.add_field(name="Statistics",
                        value=utils.format_lines({
                            "Servers": len(self.bot.guilds),
                            "Users": len(self.bot.users),
                            "Admins": len(await self.bot.get_cog("Permissions").get_permissions_list())
                        }))

        embed.add_field(name="Running",
                        value=utils.format_lines({
                            "Since": f"{self._started_at.strftime(utils.time_format)} (GMT)",
                            "For": display_delta(delta),
                            "Last check": f"{self._last_active_at.strftime(utils.time_format)} (GMT)",
                        }), inline=False)

        cogs = '\n'.join([f"+ {key}" for key in self.bot.cogs.keys()])
        embed.add_field(name=f"Loaded cogs ({len(self.bot.cogs.keys())} total)",
                        value=f"```diff\n{cogs}\n```")

        process = psutil.Process(os.getpid())
        with process.oneshot():
            memory = process.memory_info().rss
            memory_p = process.memory_percent()
            cpu_p = process.cpu_percent()

            disk_info = psutil.disk_usage(self.bot.current_dir)

        bot_used, _ = await utils.run(f"du -sh {self.bot.current_dir}")
        if bot_used:
            bot_used = int(bot_used.split("\t")[0].strip())
            bot_used = utils.format_size(bot_used)
        else:
            bot_used = no

        embed.add_field(name="Resource consumption",
                        value=utils.format_lines({
                            "CPU": f"{cpu_p:.1%}",
                            "RAM": f"{utils.format_size(memory)} ({memory_p:.1%})",
                            "Disk": f"{utils.format_size(disk_info.used)} "
                                    f"({disk_info.percent:.1f}%)",
                            "Storage": bot_used,
                            "Latency": f"{math.ceil(self.bot.latency * 100)} ms"
                        }))

        await ctx.send(embed=embed)

    async def get_activity__code(self, voice, application_id):
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
                           option_type=9,
                           required=True,
                           # choices=[create_choice(name=name, value=name) for name in activities.keys()]
                       )],
                       connector={"type": "activity_type"},
                       guild_ids=guild_ids)
    async def start_activity(self, ctx: SlashContext, activity_type):
        print(activity_type)
        """Creates an activity invite for voice channel you are in"""
        if not ctx.author.voice:
            await ctx.send(hidden=True, content="You need to be in a voice channel to start an activity!")
            return

        await ctx.defer()
        voice = ctx.author.voice
        application_id = activities[activity_type]
        code = await self.get_activity__code(voice, application_id)
        invite = f"https://discord.gg/{code}"
        icon = await self.get_application_icon(application_id)

        embed = discord.Embed(title="New voice channel activity started!", colour=utils.embed_color)
        embed.set_author(name=activity_type, icon_url=icon, url=invite)
        embed.set_thumbnail(url=icon)
        embed.description = f"**{activity_type}** activity just started in {voice.channel.mention}\n" \
                            f"[Click this link and join!]({invite})"

        await ctx.send(embed=embed)


def setup(bot):
    bot.add_cog(Greetings(bot))
