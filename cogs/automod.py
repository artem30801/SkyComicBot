import logging

import asyncio
import discord
from discord.ext import commands
from discord_slash import cog_ext, SlashContext
from discord_slash.utils.manage_commands import create_option, create_choice

import unicodedata
import datetime
from datetime import timedelta
from dateutil import relativedelta

import collections

import cogs.cog_utils as utils
import cogs.db_utils as db_utils
from cogs.cog_utils import guild_ids
from cogs.permissions import has_server_perms

from colour import Color

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


def bool_to_emoji(value):
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

    async def send_mod_log(self, guild, content="", **kwargs):
        channels = await self.bot.get_cog("Channels").get_mod_log_channels(guild)
        for channel in channels:
            await channel.send(content, **kwargs)
            # break

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
        embed = self.get_member_check_embed(member, {key: self.checks[key] for key in to_check})
        embed.title = "New member joined! Check results"
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
        embed = self.get_member_check_embed(member, {"fast leave": self.check_fast_leave})
        embed.title = "Member left!"
        field = embed.fields[0]
        lines = field.value.split("\n")
        lines.insert(-1, f"*Left at:* {datetime.datetime.utcnow().strftime(utils.time_format)} (UTC)")
        embed.set_field_at(0, name=field.name, value="\n".join(lines))
        await self.send_mod_log(member.guild, embed=embed)

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
        embed = self.get_member_check_embed(member, to_check)
        await ctx.send(embed=embed)

    def get_member_check_embed(self, member, checks: dict):
        embed = discord.Embed()
        embed.set_author(name=member.name, icon_url=member.avatar_url)

        failed_count = 0
        for check, function in checks.items():
            result = function(member)
            is_failed = result[0] is False
            addition = f"\n{utils.format_line(result[1])}" if result[1] else ""
            if is_failed:
                failed_count += 1

            embed.add_field(name=check.capitalize(),
                            value=f"{bool_to_emoji(result[0])} {'Failed' if is_failed else 'Passed'}"
                                  f"{addition}",
                            inline=False)

        embed.colour = self.get_check_color(failed_count, len(checks))
        embed.title = "User check results"
        if checks:
            embed.description = f"**{failed_count}/{len(checks)}** checks failed" if failed_count > 0 else "All checks passed!"

        embed.insert_field_at(0, name="Member",
                              value=f"*Mention:* {member.mention} "
                                    f"[*mobile link*](https://discordapp.com/users/{member.id}/)\n"
                                    f"*Roles:* {', '.join([role.mention for role in member.roles[1:]]) or 'None'}",
                              inline=False)
        embed.insert_field_at(1, name="User info",
                              value=utils.format_lines({
                                    "Name": member,
                                    "ID": member.id,
                                    "Registered at": f"{member.created_at.strftime(utils.time_format)} (UTC)",
                                    "Joined at": f"{member.joined_at.strftime(utils.time_format)} (UTC)",
                              }),
                              inline=False)
        return embed

    @cog_ext.cog_subcommand(base="check", name="server",
                            options=[
                                create_option(
                                    name="check",
                                    description="Check to perform (all by default)",
                                    option_type=str,
                                    required=False,
                                    choices=[]
                                ),
                            ],
                            guild_ids=guild_ids)
    async def check_server(self, ctx: SlashContext, check="all"):
        """Runs selected checks on all members of the server, shows server statistics"""
        await ctx.defer(hidden=False)
        checks = self.get_to_check(check)

        embed = discord.Embed()
        embed.set_author(name=ctx.guild.name, icon_url=ctx.guild.icon_url)
        total_failed = 0
        failed_members = set()
        for check, function in checks.items():
            failed = []
            for member in ctx.guild.members:
                if function(member)[0] is False:
                    mention = f"- {member.mention} {member} [*mobile link*](https://discordapp.com/users/{member.id}/)"
                    failed.append(mention)
                    failed_members.add(member)

            value = f"{bool_to_emoji(not failed)} "
            if not failed:
                value += "Passed"
            else:
                total_failed += 1
                failed_str = '\n'.join(failed)
                value += f"Failed **{len(failed)}/{ctx.guild.member_count}** members:\n {failed_str}"

            embed.add_field(name=check.capitalize(), value=value, inline=False)

        embed.colour = self.get_check_color(total_failed, len(checks), intolerance=0)
        embed.title = "Server check results"
        # embed.description =
        if checks:
            value = utils.format_lines({
                "Checks performed": f"{len(checks)}/{len(self.checks)}",
                "Checks failed": f"{total_failed}/{len(checks)}",
                "Members failed checks": f"{len(failed_members)}/{ctx.guild.member_count}"
            })
            embed.insert_field_at(0, name="Summary", value=value)

        embed.insert_field_at(0, name="Statistics",
                              value=utils.format_lines({
                                  "Members": ctx.guild.member_count,
                                  "Roles": len(ctx.guild.roles),
                                  "Emojis": f"{len(ctx.guild.emojis)}/{ctx.guild.emoji_limit}"
                              }))
        embed.set_footer(text="Use `/check member member: <member>` to check an individual member!",
                         icon_url=ctx.guild.me.avatar_url)
        await ctx.send(embed=embed)

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
                    after=message.created_at - datetime.timedelta(seconds=self.per),
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
        return self._check_recent(member.joined_at or datetime.datetime.utcnow(), "Joined:\n{} ago")

    def _check_recent(self, time, format_string="{}"):  # true = ok
        now = datetime.datetime.utcnow()
        delta = relativedelta.relativedelta(now, time)
        abs_delta = now - time
        return abs_delta >= self.recent_join, format_string.format(utils.display_delta(delta))

    def check_immediate_join(self, member):
        delta = relativedelta.relativedelta(member.joined_at, member.created_at)
        abs_delta = member.joined_at - member.created_at
        result = abs_delta >= self.immediately_join or (None if self.check_recently_joined(member)[0] else False)
        return result, "Between registration and joining:\n" + utils.display_delta(delta)

    def check_fast_leave(self, member):
        now = datetime.datetime.utcnow()
        delta = relativedelta.relativedelta(now, member.joined_at)
        abs_delta = now - member.joined_at
        return abs_delta >= self.immediately_join, "Between joining and leaving:\n" + utils.display_delta(delta)

    def check_member_spam(self, member):
        raise NotImplementedError


def setup(bot):
    bot.add_cog(AutoMod(bot))
