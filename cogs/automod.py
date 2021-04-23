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

import cogs.cog_utils as utils
import cogs.db_utils as db_utils
from cogs.cog_utils import guild_ids
from cogs.permissions import has_server_perms

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


class AutoMod(utils.AutoLogCog, utils.StartupCog):
    def __init__(self, bot):
        utils.AutoLogCog.__init__(self, logger)
        utils.StartupCog.__init__(self)

        self.bot = bot

        self.blank_threshold = 2
        self.recent_join = timedelta(days=3)
        self.immediatly_join = timedelta(minutes=30)

        self.rate = 10  # times
        self.per = 30  # per seconds
        self._spam_cooldown = commands.CooldownMapping.from_cooldown(
            self.rate, self.per, commands.BucketType.user)
        self._spam_notify_cooldown = commands.CooldownMapping.from_cooldown(
            1, 10, commands.BucketType.channel)
        self._spam_report_cooldown = commands.CooldownMapping.from_cooldown(
            1, 5 * 60, commands.BucketType.guild)

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author == message.guild.me:
            return

        await self.check_spam(message)

    # @commands.Cog.listener()
    # async def on_member_join(self, member):
    #     blank = self.check_nick_blank(member)[0]
    #     if not blank:
    #         await self.notify_nick_blank(member)

    @cog_ext.cog_subcommand(base="check", name="members",
                            options=[
                                create_option(
                                    name="member",
                                    description="Member to perform check on (or all members)",
                                    option_type=discord.Member,
                                    required=False,
                                ),
                                create_option(
                                    name="check",
                                    description="Check to perform",
                                    option_type=str,
                                    required=False,
                                    choices=[
                                        create_choice("all", "all"),
                                        create_choice("blank nick", "blank nick"),
                                        create_choice("fresh account", "fresh account"),
                                        create_choice("recently joined", "recently joined"),
                                        create_choice("immediately joined", "immediately joined"),
                                    ]
                                ),
                            ],
                            guild_ids=guild_ids)
    @has_server_perms()
    async def manual_check(self, ctx: SlashContext, check="all", member=None):
        """Performs specified (or all) security checks on given (or all) members"""
        hidden = False  # todo hidden=false only in mod-logs channel
        await ctx.defer(hidden=hidden)

        checks = {"blank nick": self.check_nick_blank,
                  "fresh account": self.check_fresh_account,
                  "recently joined": self.check_recently_joined,
                  "immediately joined": self.check_immidiate_join,
                  }
        check_all = check == "all"
        to_check = list(checks.keys()) if check_all else [check]
        max_len = len(max(to_check, key=len))
        if member is None:
            results = [f"Check results for *{len(ctx.guild.members)} members*:"]
            for check in to_check:
                failed = []
                for member in ctx.guild.members:
                    if checks[check](member)[0] is False:
                        mention = f"{member.mention} (*{member}*)"
                        failed.append(mention)
                results.append(f"{bool_to_emoji(not failed)} "
                               f"`{check: <{max_len}} ({len(failed):03d}/{len(ctx.guild.members):03d} members)`: "
                               f"{' | '.join(failed) or '**nobody**'}")
            for chunk in db_utils.chunks_split(results):
                await ctx.send("\n".join(chunk),
                               allowed_mentions=discord.AllowedMentions.none(),
                               hidden=hidden)
        else:
            bools = []
            results = []
            for check in to_check:
                result = checks[check](member)
                bools.append(result[0] is False)
                addition = f" *({result[1]})*" if result[1] else ""
                results.append(f"{bool_to_emoji(result[0])} "
                               f"`{check: <{max_len}}`: **{str(not result[0]): <5}**{addition}")

            await ctx.send(f"Check results for {member.mention} (*{member}*) "
                           f"**({sum(bools)}/{len(to_check)} checks failed)**: \n" + "\n".join(results),
                           allowed_mentions=discord.AllowedMentions.none(),
                           hidden=hidden)

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
            logger.important(f"Detected spam by {self.format_caller(message)}!")
            deleted = await self._purge(message)
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

        # todo send to moderation log channel

    def check_nick_blank(self, member):
        return check_blank(member.display_name, self.blank_threshold), None

    async def notify_nick_blank(self, member):
        logger.important(f"Member {self.format_caller(member)} has blank nickname ({member.display_name})")
        channel = await utils.get_home_channel(member.guild)
        if channel is None or utils.can_bot_respond(member.guild.me, channel):
            return
        await channel.send(f"Hey, {member.mention}, you have a blank or hard-readable username!"
                           f"Please change it so it has at least {self.blank_threshold} "
                           f"letters, numbers or some meaningful symbols. Thank you (*^_^)／")

    def check_fresh_account(self, member: discord.Member):
        return self._check_recent(member.created_at, " old")

    def check_recently_joined(self, member: discord.Member):
        return self._check_recent(member.joined_at or datetime.datetime.utcnow(), " ago")

    def _check_recent(self, time, extra=""):  # true = ok
        now = datetime.datetime.utcnow()
        delta = relativedelta.relativedelta(now, time)
        abs_delta = now - time
        return abs_delta >= self.recent_join, utils.display_delta(delta) + extra

    def check_immidiate_join(self, member):
        delta = relativedelta.relativedelta(member.joined_at, member.created_at)
        abs_delta = member.joined_at - member.created_at
        result = abs_delta >= self.immediatly_join or (None if self.check_recently_joined(member)[0] else False)
        return result, utils.display_delta(delta) + " between registration and joining"


def setup(bot):
    bot.add_cog(AutoMod(bot))
