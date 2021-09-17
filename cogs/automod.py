import asyncio
import collections
import logging
import math
from typing import Union

import psutil
import os
import re
import unicodedata
from enum import Enum
from datetime import datetime, timedelta

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
from cogs.permissions import has_server_perms, has_server_perms_from_ctx


class StatusType(Enum):
    BOT_STATUS = 1, None
    GUILD_STATUS = 2, commands.BucketType.guild

    def __new__(cls, *args, **kwargs):
        obj = object.__new__(cls)
        obj._value_ = args[0]
        return obj

    # ignore the first param since it's already set by __new__
    def __init__(self, _, bucket_type=None):
        self._bucket_type = bucket_type

    @property
    def bucket_type(self):
        return self._bucket_type

    def get_id(self, obj):
        if self.bucket_type is None:
            return None
        if self.bucket_type is commands.BucketType.guild:
            return (obj.guild or obj.author).id

        return NotImplementedError


class StatusMessage(Model):
    id = fields.IntField(pk=True)
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
        return utils.info_emote
    return utils.check_emote if value else utils.fail_emote


FakeAuthorMessage = collections.namedtuple("FakeAuthorMessage", ["author"])
FakeGuildMessage = collections.namedtuple("FakeGuildMessage", ["guild"])
FakeCheckContext = collections.namedtuple("FakeCheckContext", [
    "guild",
    "channel",
    "author",
    "bot",
])


class AutoMod(utils.AutoLogCog, utils.StartupCog):
    def __init__(self, bot):
        utils.AutoLogCog.__init__(self, logger)
        utils.StartupCog.__init__(self)

        self.bot = bot

        self.blank_threshold = 2
        self.recent_join = timedelta(days=3)
        self.immediately_join = timedelta(minutes=60)
        self.resume_update_timeout = 30  # seconds

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

        self.status_messages = {status_type: {} for status_type in StatusType}
        self.status_error_backoff = {status_type: utils.BackoffStrategyBase(max_attempts=5)
                                     for status_type in StatusType}

        self.messages_to_stop = set()

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
        for status_type in StatusType:
            await self.update_status_tasks(status_type)

    @commands.Cog.listener()
    async def on_message(self, message):
        if not message.guild:
            return
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

    async def get_status_embed(self, embed_id, status_type):
        if status_type is StatusType.GUILD_STATUS:
            return await self.make_guild_status_embed(self.bot.get_guild(embed_id))
        elif status_type is StatusType.BOT_STATUS:
            return await self.make_bot_status_embed()

        raise NotImplementedError

    def get_status_update_task(self, status_type):
        if status_type is StatusType.GUILD_STATUS:
            return self.update_guilds_status
        elif status_type is StatusType.BOT_STATUS:
            return self.update_bot_status

        raise NotImplementedError

    async def update_status(self, status_type):
        logger.debug(f"Updating {status_type} status messages")

        updated_count = 0
        status_messages = self.status_messages[status_type]
        embeds = dict()
        for message_id, channel_id in status_messages.items():
            if message_id in self.messages_to_stop:
                continue

            channel = self.bot.get_channel(channel_id)
            message = channel.get_partial_message(message_id)

            embed_id = status_type.get_id(message)
            if embed_id not in embeds:
                embeds[embed_id] = await self.get_status_embed(embed_id, status_type)
            embed = embeds[embed_id]

            if await self.update_message_footer_reactions(message, status_type, embed):
                updated_count += 1

        logger.debug(f"Updated {updated_count} {status_type} status messages")
        self.status_error_backoff[status_type].reset()

    @tasks.loop(minutes=5)
    async def update_bot_status(self):
        await self.update_status(StatusType.BOT_STATUS)

    @tasks.loop(hours=1)
    async def update_guilds_status(self):
        await self.update_status(StatusType.GUILD_STATUS)

    async def update_status_error(self, exception, status_type):
        logger.error(f"Caught exception while updating {status_type} status:", exc_info=exception)
        try:
            delay = next(self.status_error_backoff[status_type])
        except StopIteration:
            logger.warning("Too many fails, stopping restart attempts")
            return

        logger.info(f"Waiting for {delay} seconds before restart")
        await asyncio.sleep(delay)
        task = self.get_status_update_task(status_type)
        task.restart()

    @update_bot_status.error
    async def update_bot_status_error(self, exception):
        await self.update_status_error(exception, StatusType.BOT_STATUS)

    @update_guilds_status.error
    async def update_guilds_status_error(self, exception):
        await self.update_status_error(exception, StatusType.GUILD_STATUS)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, reaction_payload: discord.RawReactionActionEvent):
        if reaction_payload.member.bot:
            return
        if str(reaction_payload.emoji) != utils.fail_emote:
            return

        for status_type, messages in self.status_messages.items():
            if reaction_payload.message_id in messages:
                await self.try_stop_status_update(reaction_payload, status_type)
                break

    async def try_stop_status_update(self, reaction: discord.RawReactionActionEvent, status_type: StatusType):
        guild = self.bot.get_guild(reaction.guild_id)
        channel = guild.get_channel(reaction.channel_id)
        message = await channel.fetch_message(reaction.message_id)

        logger.info(f"{reaction.member} trying to stop {status_type} status updates"
                    f"in {channel} in {guild}. Message ID: {message.id}")

        ctx = FakeCheckContext(guild=guild, channel=channel, author=reaction.member, bot=self.bot)
        if not await has_server_perms_from_ctx(ctx):
            logger.info(f"Prevented {reaction.member} from stopping status updates. No server permissions")
            if utils.can_bot_manage_messages(channel):
                await message.remove_reaction(reaction.emoji, reaction.member)
            else:
                logger.info("Can't remove member reaction from message")
            return

        self.messages_to_stop.add(reaction.message_id)
        await self.update_message_footer_reactions(message, status_type)

        if await self.try_stop_message_update(message, reaction.member):
            await self.update_status_tasks(status_type)

        try:
            self.messages_to_stop.remove(reaction.message_id)
        except KeyError:
            pass

        await self.update_message_footer_reactions(message, status_type)
        logger.info("Finished message update stop attempt")

    async def try_stop_message_update(self, message: discord.Message, requester: discord.Member):
        def is_cancel_reaction(reaction_payload: discord.RawReactionActionEvent):
            if reaction_payload.message_id != message.id:
                return False
            if reaction_payload.member != requester:
                return False
            return str(reaction_payload.emoji) == utils.refresh_emote

        try:
            await self.bot.wait_for('raw_reaction_add', timeout=self.resume_update_timeout, check=is_cancel_reaction)
        except asyncio.TimeoutError:
            # There was no reaction, removing status message
            status_message = await utils.OrmBackoffStrategy().run_task(StatusMessage.get_or_none, message_id=message.id)
            if status_message:
                await status_message.delete()

            logger.info(f"Stopped status updates for message {message.id}")
            return True
        else:
            logger.info(f"Aborting stop attempt for message {message.id}")
            return False

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
        git_hash = (await utils.run(f"git describe --always"))[0] or no
        commits_behind = (await utils.run(f"git fetch; "
                                          f"git rev-list HEAD...origin/master --count"))[0]
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

            disk_info = psutil.disk_usage(os.getcwd())

        storage_size, _ = await utils.run(f"du -s {os.getcwd()}")
        if storage_size:
            storage_size = int(storage_size.split("\t")[0].strip())
            storage_size = utils.format_size(storage_size * 1024)
        else:
            storage_size = no

        embed.add_field(name="Resource consumption",
                        value=utils.format_lines({
                            "CPU": f"{cpu_p:.1%}",
                            "RAM": f"{utils.format_size(memory)} ({memory_p:.1f}%)",
                            "Disk": f"{utils.format_size(disk_info.used)} "
                                    f"({disk_info.percent:.1f}%)",
                            "Storage": storage_size,
                            "Latency": f"{math.ceil(self.bot.latency * 100)} ms"
                        }))

        embed.set_footer(text="Yours truly!", icon_url=self.bot.user.avatar_url)
        return embed

    async def make_guild_status_embed(self, guild: discord.Guild, checks: dict = None) -> discord.Embed:
        checks = checks or self.checks
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
            "Status": str(member.activity or "None set"),
            "Online status": str(member.status or "Not available"),
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
            embed.description = f"**{failed_count}/{len(checks)}** checks failed" if failed_count > 0 \
                else "All checks passed!"

    def update_message_footer_text(self, message_id: int, embed: discord.Embed, status_type: StatusType):
        icon_url = embed.footer.icon_url
        if message_id in self.messages_to_stop:
            embed.set_footer(text=f"Press {utils.refresh_emote} in next"
                                  f" {self.resume_update_timeout} seconds to resume updates",
                             icon_url=icon_url)
            return

        status_messages = self.status_messages[status_type]
        if message_id in status_messages:
            task = self.get_status_update_task(status_type)
            update_period = utils.display_task_period(task)
            embed.set_footer(text=f"Updates every {update_period}. Press {utils.fail_emote} to stop updates",
                             icon_url=icon_url)
        else:
            embed.set_footer(text="Updates stopped", icon_url=icon_url)

    async def update_message_footer_reactions(self, message, status_type, embed=None):
        embed = embed or message.embeds[0]
        embed.timestamp = datetime.utcnow()
        self.update_message_footer_text(message.id, embed, status_type)

        try:
            await message.edit(embed=embed)
        except discord.Forbidden:
            logger.warning("Cannot edit status message sent by other user")
            return False
        else:
            await self.ensure_message_reactions(message, status_type)
            return True

    async def _remove_bot_reaction(self, message, emote):
        if utils.can_bot_manage_messages(message.channel):
            await message.clear_reaction(emote)
        else:
            await message.remove_reaction(emote, self.bot.user)

    async def ensure_message_reactions(self, message: Union[discord.Message, discord.PartialMessage],
                                       status_type: StatusType):
        """Makes sure that status message has correct reactions for the current state"""
        if message.id in self.messages_to_stop:
            await message.add_reaction(utils.refresh_emote)
            await self._remove_bot_reaction(message, utils.fail_emote)
        else:
            status_messages = self.status_messages[status_type]
            if message.id in status_messages:
                await message.add_reaction(utils.fail_emote)
            else:
                await self._remove_bot_reaction(message, utils.fail_emote)

            await self._remove_bot_reaction(message, utils.refresh_emote)

    async def update_status_tasks(self, status_type: StatusType):
        messages = await utils.OrmBackoffStrategy().run_task(StatusMessage.filter, status_type=status_type.value)
        messages = {message.message_id: message.channel_id for message in messages}

        self.status_messages[status_type] = messages

        status_tasks = [self.get_status_update_task(status_type)]
        if messages:
            utils.ensure_tasks_running(status_tasks)
        else:
            utils.ensure_tasks_stopped(status_tasks)

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
        if not isinstance(member, discord.Member):
            raise commands.BadArgument(f"Failed to get member '{member}' info!")

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
        if auto_update and not ctx.channel:
            await ctx.send("Messages with auto-update are not supported outside of normal channels", hidden=True)
            return

        await ctx.defer(hidden=False)
        checks = self.checks if auto_update else self.get_to_check(check)
        embed = await self.make_guild_status_embed(ctx.guild, checks)
        message = await ctx.send(embed=embed)
        if auto_update:
            logger.info(f"{ctx.author} adding auto-updated check")

            await self.save_status_message(message, StatusType.GUILD_STATUS)
            await self.update_status_tasks(StatusType.GUILD_STATUS)

            # Add update info after saving message to DB in case DB errors to prevent misleading info in message
            await self.update_message_footer_reactions(message, StatusType.GUILD_STATUS)

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
        if auto_update and not ctx.channel:
            await ctx.send("Messages with auto-update are not supported outside of normal channels", hidden=True)
            return

        if auto_update:
            # Only server admins can create updatable messages
            await has_server_perms().predicate(ctx)
        await ctx.defer(hidden=False)
        embed = await self.make_bot_status_embed()
        message = await ctx.send(embed=embed)
        if auto_update:
            logger.info(f"{ctx.author} adding auto-updated check")

            await self.save_status_message(message, StatusType.BOT_STATUS)
            await self.update_status_tasks(StatusType.BOT_STATUS)

            # Add update info after saving message to DB in case DB errors to prevent misleading info in message
            await self.update_message_footer_reactions(message, StatusType.BOT_STATUS)

    @cog_ext.cog_subcommand(base="auto-updates", name="stop",
                            options=[
                                create_option(
                                    name="id",
                                    description="Message ID in the database",
                                    option_type=int,
                                    required=False
                                ),
                                create_option(
                                    name="link",
                                    description="Link to the update message",
                                    option_type=str,
                                    required=False
                                ),
                            ],
                            connector={
                                "id": "db_id",
                                "link": "msg_link",
                            },
                            guild_ids=guild_ids)
    @has_server_perms()
    async def stop_status(self, ctx: SlashContext, db_id: int = None, msg_link: str = None):
        """Stops update for the status message by DB id or message link"""
        await ctx.defer(hidden=True)
        logger.info(f"{ctx.author} trying to stop status update by id '{db_id}' or link '{msg_link}'")
        if db_id is None and msg_link is None:
            raise commands.BadArgument("Please provide message link or ID")

        db_message = None
        if db_id is not None:
            db_message = await utils.OrmBackoffStrategy().run_task(StatusMessage.get_or_none, id=db_id)
            if db_message is None:
                raise commands.BadArgument(f"There is no status message with id {db_id}")

        if not db_message and msg_link:
            # TODO: precompile match on start
            match = re.match(
                pattern=r"(\A|\W)https://discord.com/channels/(?P<guild_id>\d+)/(?P<channel_id>\d+)/(?P<msg_id>\d+)(\Z|\W)",
                string=msg_link
            )
            if not match:
                raise commands.BadArgument(f"Looks like '{msg_link}' is not a link to discord message")
            ids = match.groupdict()
            db_message = await utils.OrmBackoffStrategy().run_task(StatusMessage.get_or_none,
                                                                   guild_id=ids['guild_id'],
                                                                   channel_id=ids['channel_id'],
                                                                   message_id=ids['msg_id'])
            if db_message is None:
                raise commands.BadArgument("Looks like linked message is not a status message with auto-updates")

        await utils.OrmBackoffStrategy().run_task(db_message.delete)
        status_type = StatusType(db_message.status_type)
        await self.update_status_tasks(status_type)

        guild = self.bot.get_guild(db_message.guild_id)
        channel = guild.get_channel(db_message.channel_id)
        message = await channel.fetch_message(db_message.message_id)

        await self.update_message_footer_reactions(message, status_type)

        await ctx.send("Removed message from auto-updates", hidden=True)

    @cog_ext.cog_subcommand(base="auto-updates", name="refresh", guild_ids=guild_ids)
    @has_server_perms()
    async def refresh_status(self, ctx: SlashContext):
        """Forcefully refreshes all status messages with auto-update"""
        await ctx.defer(hidden=True)
        if self.update_bot_status.is_running():
            self.update_bot_status.restart()
        if self.update_guilds_status.is_running():
            self.update_guilds_status.restart()
        await ctx.send("Refreshed!", hidden=True)

    @cog_ext.cog_subcommand(base="auto-updates", name="list", guild_ids=guild_ids)
    @has_server_perms()
    async def list_status(self, ctx: SlashContext):
        """Lists all messages with auto-update in database"""
        await ctx.defer(hidden=True)
        messages = await StatusMessage.all()
        if not messages:
            await ctx.send("No messages with auto-update", hidden=True)
            return

        embed = discord.Embed()
        embed.title = "Messages with auto-updates"

        bot_status_messages = [message for message in messages if message.status_type == StatusType.BOT_STATUS.value]
        messages_info = []
        for message in bot_status_messages:
            guild = self.bot.get_guild(message.guild_id)
            channel = guild.get_channel(message.channel_id)
            messages_info.append(
                f"[Message](https://discord.com/channels/{guild.id}/{channel.id}/{message.message_id}) "
                f"in {channel.mention} in {guild} (ID {message.id})")
        if messages_info:
            embed.add_field(name="Bot status messages", value="\n".join(messages_info), inline=False)

        guilds_messages = {}
        for message in messages:
            if message.status_type != StatusType.GUILD_STATUS.value:
                continue
            if message.guild_id not in guilds_messages:
                guilds_messages[message.guild_id] = []
            guilds_messages[message.guild_id].append(message)
        for guild_id, messages in guilds_messages.items():  # todo make them like bot status messages
            guild = self.bot.get_guild(guild_id)
            messages_info = []
            for message in messages:
                channel = guild.get_channel(message.channel_id)
                messages_info.append(
                    f"[Message](https://discord.com/channels/{guild.id}/{channel.id}/{message.message_id}) "
                    f"in {channel.mention} (ID {message.id})")
            embed.add_field(name=f"{guild} status messages", value="\n".join(messages_info), inline=False)

        await ctx.send(embed=embed, hidden=True)

    @staticmethod
    async def save_status_message(message: discord.Message, status_type: StatusType):
        status_message = await utils.OrmBackoffStrategy().run_task(StatusMessage.get_or_none,
                                                                   guild_id=message.guild.id,
                                                                   channel_id=message.channel.id,
                                                                   status_type=status_type.value)
        if not status_message:
            # Can't use get_or_create since message_id is mandatory and I don't want to make it not mandatory
            status_message = StatusMessage(
                guild_id=message.guild.id,
                channel_id=message.channel.id,
                status_type=status_type.value
            )
        status_message.message_id = message.id
        await utils.OrmBackoffStrategy().run_task(status_message.save)

    @staticmethod
    def ratelimit_check(cooldown, message):
        bucket = cooldown.get_bucket(message)
        return bucket.update_rate_limit()

    async def _purge(self, message):
        def same_author(m):
            return m.author == message.author

        if utils.can_bot_manage_messages(message.channel):
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
            if utils.can_bot_manage_messages(message.channel):
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

    async def report_spam(self, message, deleted):
        logger.important(f"Detected spam by {self.format_caller(message)}!")

        if deleted is None:
            deleted = ()

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
