import asyncio
import collections
import logging
import math
import psutil
import os
import unicodedata
from enum import Enum
from datetime import datetime, timedelta

import tortoise.exceptions
from dateutil import relativedelta
from typing import Optional

from colour import Color
import discord
from discord.ext import commands, tasks
from discord_slash import cog_ext, SlashContext
from discord_slash.utils.manage_commands import create_option, create_choice
from tortoise import fields
from tortoise.models import Model

import cogs.cog_utils as utils
import cogs.db_utils as db_utils
from cogs.cog_utils import guild_ids, display_delta
from cogs.permissions import has_server_perms


class StatusType(Enum):
    BOT_STATUS = 1
    GUILD_STATUS = 2


class StatusMessage(Model):
    guild_id = fields.BigIntField()
    channel_id = fields.BigIntField()
    message_id = fields.BigIntField()
    status_type = fields.IntField()


logger = logging.getLogger(__name__)

categories = ["Ll", "Lo", "Lt", "Lu", "Nd", "Nl", "No", "Ps", "So"]


def check_blank(s, threshold=2):
    counter = 0
    for c in s.strip():
        if unicodedata.category(c) in categories:
            counter += 1
            if counter >= threshold:
                return True
    return False


def status_to_emoji(value):
    if value is None:
        return "ℹ"
    return "✅" if value else "❌"


FakeAuthorMessage = collections.namedtuple("FakeAuthorMessage", ["author"])
FakeGuildMessage = collections.namedtuple("FakeGuildMessage", ["guild"])


class AutoMod(utils.AutoLogCog, utils.StartupCog):
    def __init__(self, bot):
        utils.AutoLogCog.__init__(self, logger)
        utils.StartupCog.__init__(self)

        self.bot = bot

        self.blank_threshold = 2
        self.recent_join = timedelta(days=3)
        self.immediately_join = timedelta(minutes=60)

        self.rate = 10  # times
        self.per = 30  # per seconds
        self._spam_cooldown = commands.CooldownMapping.from_cooldown(
            self.rate, self.per, commands.BucketType.user)
        self._spam_notify_cooldown = commands.CooldownMapping.from_cooldown(
            1, 10, commands.BucketType.channel)
        self._spam_report_cooldown = commands.CooldownMapping.from_cooldown(
            1, 5 * 60, commands.BucketType.guild)

        self._join_cooldown = commands.CooldownMapping.from_cooldown(
            2, 60 * 60, commands.BucketType.user)
        self._join_report_cooldown = commands.CooldownMapping.from_cooldown(
            1, 15 * 60, commands.BucketType.guild)

        self.checks = {"blank nick": self.check_nick_blank,
                       "fresh account": self.check_fresh_account,
                       "recently joined": self.check_recently_joined,
                       "immediately joined": self.check_immediate_join,
                       }
        self.update_options()

    def update_options(self):
        choices = [create_choice(name=check.capitalize(), value=check) for check in self.checks.keys()]
        choices = [create_choice(name="All", value="all")] + choices + \
                  [create_choice(name="None (stats and info only)", value="none")]

        self.check_member.options[1]["choices"] = choices
        self.check_server.options[0]["choices"] = choices

    async def on_startup(self):
        await self.try_start_bot_status_update()
        await self.try_start_guilds_status_update()

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author == message.guild.me:
            return

        await self.check_spam(message)

    @commands.Cog.listener()
    async def on_member_join(self, member):
        fake_msg = FakeAuthorMessage(member)
        join_after = self.ratelimit_check(self._join_cooldown, fake_msg)
        if join_after is not None:
            await self.report_join_spam(member)
            return

        logger.info(f"Member {member} joined guild {member.guild}")
        to_check = ["blank nick", "fresh account", "immediately joined"]
        embed = self.make_basic_member_embed(member)
        embed.title = "New member joined! Check results"
        self.add_checks_fields(embed, member, {key: self.checks[key] for key in to_check})
        await self.send_mod_log(member.guild, embed=embed)

        blank = self.check_nick_blank(member)[0]
        if not blank:
            await self.notify_nick_blank(member)

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        # todo detect kick/ban
        # await member.guild.fetch_ban(member)
        fake_msg = FakeAuthorMessage(member)
        join_after = self.ratelimit_check(self._join_cooldown, fake_msg)
        if join_after is not None:
            await self.report_join_spam(member)
            return

        logger.info(f"Member {member} left guild {member.guild}")
        embed = self.make_basic_member_embed(member, additional_info={
            "Left at": f"{datetime.utcnow().strftime(utils.time_format)} (UTC)"
        })
        embed.title = "Member left!"
        self.add_checks_fields(embed, member, {"fast leave": self.check_fast_leave})
        await self.send_mod_log(member.guild, embed=embed)

    async def try_start_bot_status_update(self):
        if self.update_bot_status.is_running():
            return
        if not await StatusMessage.exists(status_type=StatusType.BOT_STATUS.value):
            return
        self.update_bot_status.start()

    @tasks.loop(minutes=5)
    async def update_bot_status(self):
        logger.info("Updating bot status messages")
        try:
            messages = await StatusMessage.filter(status_type=StatusType.BOT_STATUS.value)
        except tortoise.exceptions.OperationalError:
            # try next time
            return
        embed = await self.make_bot_status_embed()
        self.add_update_info(embed, utils.display_task_period(self.update_bot_status))
        for message in messages:
            channel = self.bot.get_channel(message.channel_id)
            message = channel.get_partial_message(message.message_id)
            await message.edit(embed=embed)

    async def try_start_guilds_status_update(self):
        if self.update_guilds_status.is_running():
            return
        if not await StatusMessage.exists(status_type=StatusType.GUILD_STATUS.value):
            return
        self.update_guilds_status.start()

    @tasks.loop(hours=1)
    async def update_guilds_status(self):
        logger.info("Updating guild status messages")
        guild_embeds = {}
        try:
            messages = await StatusMessage.filter(status_type=StatusType.GUILD_STATUS.value)
        except tortoise.exceptions.OperationalError:
            # try next time
            return
        for message in messages:
            if message.guild_id in guild_embeds:
                embed = guild_embeds[message.guild_id]
            else:
                guild = self.bot.get_guild(message.guild_id)
                embed = await self.make_guild_status_embed(guild, self.checks)
                self.add_update_info(embed, utils.display_task_period(self.update_guilds_status))
                guild_embeds[message.guild_id] = embed
            channel = self.bot.get_channel(message.channel_id)
            message = channel.get_partial_message(message.message_id)
            await message.edit(embed=embed)

    async def send_mod_log(self, guild, content="", **kwargs):
        channels = await self.bot.get_cog("Channels").get_mod_log_channels(guild)
        for channel in channels:
            await channel.send(content, **kwargs)
            # break

    async def report_join_spam(self, member):
        fake_msg = FakeGuildMessage(member.guild)
        report_after = self.ratelimit_check(self._join_report_cooldown, fake_msg)
        if report_after is not None:
            return

        logger.important(f"Detected join spam by {self.format_caller(member)}!")

        embed = discord.Embed()
        embed.set_author(name=member.name, icon_url=member.avatar_url)
        embed.title = ":warning: Warning! Join spam!"
        embed.description = f"{member.mention} {member} (ID {member.id}) " \
                            f"[*mobile link*](https://discordapp.com/users/{member.id}/)\n" \
                            f"Joined more than 1 time in 60 minutes span."
        embed.colour = discord.Colour.red()

        await self.send_mod_log(member.guild, embed=embed)

    def get_to_check(self, check):
        if check == "none":
            return {}
        if check == "all":
            return self.checks
        return {check: self.checks[check]}

    @staticmethod
    def get_check_color(failed_count, total, intolerance=1):
        red = Color("#e74c3c")  # red
        colors = list(Color("#2ecc71").range_to(red, max(total - intolerance, 2)))
        colors += [red] * (intolerance + 1)

        return db_utils.convert_color(colors[failed_count].hex_l)

    async def make_bot_status_embed(self) -> discord.Embed:
        now = datetime.utcnow()
        started_at = self.bot.get_cog("Greetings").get_start_time()
        last_active = self.bot.get_cog("Greetings").get_last_activity_time()
        delta = relativedelta.relativedelta(now, started_at)
        embed = utils.bot_embed(self.bot)
        embed.title = "Bot check results"

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
                            "Since": f"{started_at.strftime(utils.time_format)} (GMT)",
                            "For": display_delta(delta),
                            "Last activity check": f"{last_active.strftime(utils.time_format)} (GMT)",
                        }), inline=False)

        extensions = {}
        total_loaded = 0
        for key in self.bot.initial_extensions:
            loaded = key in self.bot.extensions
            extensions[f"{'+' if loaded else '-'} {key}"] = "Online" if loaded else "Offline"
            total_loaded += loaded

        embed.add_field(name=f"Loaded extensions "
                             f"({total_loaded:02d}/{len(self.bot.initial_extensions):02d} online)",
                        value=utils.format_lines(extensions, lang="diff", delimiter=" :"))

        process = psutil.Process(os.getpid())
        with process.oneshot():
            memory = process.memory_info().rss
            memory_p = process.memory_percent()
            cpu_p = process.cpu_percent()

            disk_info = psutil.disk_usage(self.bot.current_dir)

        bot_used, _ = await utils.run(f"du -s {self.bot.current_dir}")
        if bot_used:
            bot_used = int(bot_used.split("\t")[0].strip())
            bot_used = utils.format_size(bot_used * 1024)
        else:
            bot_used = no

        embed.add_field(name="Resource consumption",
                        value=utils.format_lines({
                            "CPU": f"{cpu_p:.1%}",
                            "RAM": f"{utils.format_size(memory)} ({memory_p:.1f}%)",
                            "Disk": f"{utils.format_size(disk_info.used)} "
                                    f"({disk_info.percent:.1f}%)",
                            "Storage": bot_used,
                            "Latency": f"{math.ceil(self.bot.latency * 100)} ms"
                        }))

        return embed

    async def make_guild_status_embed(self, guild: discord.Guild, checks: dict) -> discord.Embed:
        embed = discord.Embed()
        embed.set_author(name=guild.name, icon_url=guild.icon_url)
        total_failed = 0
        failed_members = set()
        for check, function in checks.items():
            failed = []
            for member in guild.members:
                if function(member)[0] is False:
                    mention = f"- {member.mention} {member} [*mobile link*](https://discordapp.com/users/{member.id}/)"
                    failed.append(mention)
                    failed_members.add(member)

            value = f"{status_to_emoji(not failed)} "
            if not failed:
                value += "Passed"
            else:
                total_failed += 1
                failed_str = '\n'.join(failed)
                value += f"Failed **{len(failed)}/{guild.member_count}** members:\n {failed_str}"

            embed.add_field(name=check.capitalize(), value=value, inline=False)

        embed.colour = self.get_check_color(total_failed, len(checks), intolerance=0)
        embed.title = "Server check results"
        # embed.description =
        if checks:
            value = utils.format_lines({
                "Checks performed": f"{len(checks)}/{len(self.checks)}",
                "Checks failed": f"{total_failed}/{len(checks)}",
                "Members failed checks": f"{len(failed_members)}/{guild.member_count}"
            })
            embed.insert_field_at(0, name="Summary", value=value)

        embed.insert_field_at(0, name="Statistics",
                              value=utils.format_lines({
                                  "Members": guild.member_count,
                                  "Roles": len(guild.roles),
                                  "Emojis": f"{len(guild.emojis)}/{guild.emoji_limit}"
                              }))
        embed.set_footer(text="Use `/check member member: <member>` to check an individual member!",
                         icon_url=guild.me.avatar_url)
        return embed

    @staticmethod
    def make_basic_member_embed(member: discord.Member, additional_info: Optional[dict] = None) -> discord.Embed:
        embed = discord.Embed()
        embed.set_author(name=member.name, icon_url=member.avatar_url)

        embed.add_field(name="Member",
                        value=f"*Mention:* {member.mention} "
                              f"[*mobile link*](https://discordapp.com/users/{member.id}/)\n"
                              f"*Roles:* {', '.join([role.mention for role in member.roles[1:]]) or 'None'}",
                        inline=False)

        user_info = {
            "Name": member,
            "ID": member.id,
            "Registered at": f"{member.created_at.strftime(utils.time_format)} (UTC)",
            "Joined at": f"{member.joined_at.strftime(utils.time_format)} (UTC)",
        }
        if additional_info:
            user_info = user_info | additional_info
        embed.add_field(name="User info", value=utils.format_lines(user_info))

        return embed

    def add_checks_fields(self, embed: discord.Embed, member: discord.Member, checks: dict):
        failed_count = 0

        for check, function in checks.items():
            status, info = function(member)
            addition = f"\n{utils.format_line(info)}" if info else ""
            if status is False:
                failed_count += 1

            embed.add_field(name=check.capitalize(),
                            value=f"{status_to_emoji(status)} {'Failed' if status is False else 'Passed'}"
                                  f"{addition}",
                            inline=False)

        embed.colour = self.get_check_color(failed_count, len(checks))
        if not embed.title:
            embed.title = "User check results"
        if checks and not embed.description:
            embed.description = f"**{failed_count}/{len(checks)}** checks failed" if failed_count > 0 else "All checks passed!"

    @staticmethod
    def add_update_info(embed: discord.Embed, update_period: str):
        embed.timestamp = datetime.utcnow()
        embed.set_footer(text=f"Updates every {update_period}")

    @cog_ext.cog_subcommand(base="check", name="member",
                            options=[
                                create_option(
                                    name="member",
                                    description="Member to perform check on",
                                    option_type=discord.Member,
                                    required=True,
                                ),
                                create_option(
                                    name="check",
                                    description="Check to perform",
                                    option_type=str,
                                    required=False,
                                    choices=[]
                                ),
                            ],
                            guild_ids=guild_ids)
    @has_server_perms()
    async def check_member(self, ctx: SlashContext, member: discord.Member, check="all"):
        """Performs specified (or all) security checks on given member"""
        await ctx.defer(hidden=False)
        to_check = self.get_to_check(check)
        embed = self.make_basic_member_embed(member)
        self.add_checks_fields(embed, member, to_check)
        await ctx.send(embed=embed)

    @cog_ext.cog_subcommand(base="check", name="server",
                            options=[
                                create_option(
                                    name="check",
                                    description="Check to perform (all by default or if auto-update enabled)",
                                    option_type=str,
                                    required=False,
                                    choices=[]
                                ),
                                create_option(
                                    name="auto-update",
                                    description="Enables auto-update for this message (one per channel)",
                                    option_type=bool,
                                    required=False
                                ),
                            ],
                            connector={"auto-update": "auto_update"},
                            guild_ids=guild_ids)
    @has_server_perms()
    async def check_server(self, ctx: SlashContext, check="all", auto_update=False):
        """Runs selected checks on all members of the server, shows server statistics"""
        await ctx.defer(hidden=False)
        checks = self.checks if auto_update else self.get_to_check(check)
        embed = await self.make_guild_status_embed(ctx.guild, checks)
        message = await ctx.send(embed=embed)
        if auto_update:
            await self.save_status_message(message, StatusType.GUILD_STATUS)
            # Add update info after saving message to DB in case DB errors to prevent misleading info in message
            self.add_update_info(embed, utils.display_task_period(self.update_guilds_status))
            await message.edit(embed=embed)
            await self.try_start_guilds_status_update()

    @cog_ext.cog_subcommand(base="check", name="bot",
                            options=[
                                create_option(
                                    name="auto-update",
                                    description="Enables auto-update for this message (one per channel)",
                                    option_type=bool,
                                    required=False
                                ),
                            ],
                            connector={"auto-update": "auto_update"},
                            guild_ids=guild_ids)
    async def check_bot(self, ctx: SlashContext, auto_update=False):
        """Shows bot information, statistics and status"""
        if auto_update:
            # Only server admins can create updatable messages
            await has_server_perms().predicate(ctx)
        await ctx.defer(hidden=False)
        embed = await self.make_bot_status_embed()
        message = await ctx.send(embed=embed)
        if auto_update:
            await self.save_status_message(message, StatusType.BOT_STATUS)
            # Add update info after saving message to DB in case DB errors to prevent misleading info in message
            self.add_update_info(embed, utils.display_task_period(self.update_bot_status))
            await message.edit(embed=embed)
            await self.try_start_bot_status_update()

    @cog_ext.cog_subcommand(base="check", name="refresh", guild_ids=guild_ids)
    async def refresh_status(self, ctx: SlashContext):
        """Forcefully refreshes all status messages with auto-update"""
        await ctx.defer(hidden=True)
        if self.update_bot_status.is_running():
            self.update_bot_status.restart()
        if self.update_guilds_status.is_running():
            self.update_guilds_status.restart()
        await ctx.send("Refreshed!", hidden=True)

    @staticmethod
    async def save_status_message(message: discord.Message, status_type: StatusType):
        status_message = await StatusMessage.get_or_none(
            guild_id=message.guild.id,
            channel_id=message.channel.id,
            status_type=status_type.value
        )
        if not status_message:
            # Can't use get_or_create since message_id is mandatory and I don't want to make it not mandatory
            status_message = StatusMessage(
                guild_id=message.guild.id,
                channel_id=message.channel.id,
                status_type=status_type.value
            )
        status_message.message_id = message.id
        await status_message.save()

    @staticmethod
    def ratelimit_check(cooldown, message):
        bucket = cooldown.get_bucket(message)
        return bucket.update_rate_limit()

    async def _purge(self, message):
        def same_author(m):
            return m.author == message.author

        if commands.has_permissions(manage_messages=True):
            try:
                deleted = await message.channel.purge(  # limit=self.rate,
                    after=message.created_at - timedelta(seconds=self.per),
                    check=same_author, bulk=True)
            except discord.NotFound:
                return None
            else:
                logger.info('Deleted {} message(s)'.format(len(deleted)))
            return deleted
        return None

    async def check_spam(self, message):
        retry_after = self.ratelimit_check(self._spam_cooldown, message)
        if retry_after is None:
            return

        notify_after = self.ratelimit_check(self._spam_notify_cooldown, message)
        report_after = self.ratelimit_check(self._spam_report_cooldown, message)
        deleted = None

        if report_after is None:
            deleted = await self._purge(message)
            await self.report_spam(message, deleted)
        else:
            if commands.has_permissions(manage_messages=True):
                try:
                    await message.delete()
                except discord.NotFound:
                    pass
            else:
                logger.info(f"Can't delete spam message"
                            f"Don't have 'manage messages' permissions in '{message.guild}'")

        if notify_after is None:
            delete_after = 10 if report_after is not None else (None if deleted is None else self.per + 10)  # None
            deleted_msg = "" if deleted is None else f"I deleted {len(deleted)} of your last messages. "

            await message.channel.send(f"ఠ_ఠ Slow down, {message.author.mention}! You are spamming! {deleted_msg}"
                                       f"You may send messages again in {round(retry_after)} seconds.",
                                       allowed_mentions=discord.AllowedMentions.none(),
                                       delete_after=delete_after)

        if report_after is None:
            await asyncio.sleep(self.per + 1)
            await self._purge(message)

    async def report_spam(self, message, deleted=()):
        logger.important(f"Detected spam by {self.format_caller(message)}!")

        member = message.author
        embed = discord.Embed()
        embed.set_author(name=member.name, icon_url=member.avatar_url)
        embed.title = ":warning: Warning! Message spam!"
        embed.description = f"{member.mention} {member} (ID {member.id}) " \
                            f"[*mobile link*](https://discordapp.com/users/{member.id}/)\n" \
                            f"Sending messages faster than {self.rate} messages per {self.per} seconds\n" \
                            f"Deleted **{len(deleted)}** messages automatically.\n"

        embed.colour = discord.Colour.red()
        await self.send_mod_log(member.guild, embed=embed)

    def check_nick_blank(self, member):
        return check_blank(member.display_name, self.blank_threshold), None

    async def notify_nick_blank(self, member: discord.Member):
        logger.important(f"Member {self.format_caller(member)} has blank nickname ({member.display_name})")

        channels = await self.bot.get_cog("Channels").get_home_channels(member.guild)
        for channel in channels:
            await channel.send(f"Hey, {member.mention}, you have a blank or hard-readable username!\n"
                               f"Please change it so it has at least {self.blank_threshold + 1} "
                               f"letters, numbers or some meaningful symbols.\n Thank you (*^_^)／")
            return  # No need to send few notification, if there is a few home channels

    def check_fresh_account(self, member: discord.Member):
        return self._check_recent(member.created_at, "Account created:\n{} ago")

    def check_recently_joined(self, member: discord.Member):
        return self._check_recent(member.joined_at or datetime.utcnow(), "Joined:\n{} ago")

    def _check_recent(self, time, format_string="{}"):  # true = ok
        now = datetime.utcnow()
        delta = relativedelta.relativedelta(now, time)
        abs_delta = now - time
        return abs_delta >= self.recent_join, format_string.format(utils.display_delta(delta))

    def check_immediate_join(self, member):
        delta = relativedelta.relativedelta(member.joined_at, member.created_at)
        abs_delta = member.joined_at - member.created_at
        result = abs_delta >= self.immediately_join or (None if self.check_recently_joined(member)[0] else False)
        return result, "Between registration and joining:\n" + utils.display_delta(delta)

    def check_fast_leave(self, member):
        now = datetime.utcnow()
        delta = relativedelta.relativedelta(now, member.joined_at)
        abs_delta = now - member.joined_at
        return abs_delta >= self.immediately_join, "Between joining and leaving:\n" + utils.display_delta(delta)

    def check_member_spam(self, member):
        raise NotImplementedError


def setup(bot):
    bot.add_cog(AutoMod(bot))
