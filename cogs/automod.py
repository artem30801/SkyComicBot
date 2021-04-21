import logging
import unicodedata

import discord
from discord.ext import commands

import cogs.cog_utils as utils

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


# strs = [" ឵឵ ",
#         " ˞˞˞˞˞˞˞˞˞˞˞˞˞˞˞˞˞˞˞˞",
#         "҈҈҈",
#         "ник",
#         "ñ",
#         "(っ◔◡◔)っ",
#         "ꜱᴜɢᴀᴄᴏᴏᴋɪᴇᴄʟᴀʀɪᴢ ❁༺",
#         "°♡𝒩𝒾𝓍𝒾 𝒜𝓃𝑔𝑒𝓁♡", ]

# for s in strs:
#     print(s, check_nickname(s), [unicodedata.category(c) for c in s])


class AutoMod(utils.AutoLogCog, utils.StartupCog):
    def __init__(self, bot):
        utils.AutoLogCog.__init__(self, logger)
        utils.StartupCog.__init__(self)

        self.bot = bot

        self.rate = 10  # times
        self.per = 30  # per seconds
        self._spam_cooldown = commands.CooldownMapping.from_cooldown(
            self.rate, self.per, commands.BucketType.user)
        self._spam_notify_cooldown = commands.CooldownMapping.from_cooldown(
            1, 10, commands.BucketType.channel)
        self._spam_report_cooldown = commands.CooldownMapping.from_cooldown(
            1, 5 * 60, commands.BucketType.guild)

    def check_nick_blank(self, member):
        return check_blank(member.d)

    @staticmethod
    def ratelimit_check(cooldown, message):
        bucket = cooldown.get_bucket(message)
        return bucket.update_rate_limit()

    async def check_spam(self, message):
        retry_after = self.ratelimit_check(self._spam_cooldown, message)
        if retry_after is not None:
            if commands.has_permissions(manage_messages=True):
                await message.delete()
            else:
                logger.info(f"Can't delete spam message"
                            f"Don't have 'manage messages' permissions in '{message.guild}'")

            notify_after = self.ratelimit_check(self._spam_notify_cooldown, message)
            report_after = self.ratelimit_check(self._spam_report_cooldown, message)
            deleted = None

            if report_after is None:
                logger.important(f"Detected spam by {self.format_caller(message)}!")

                def same_author(m):
                    return m.author == message.author

                if commands.has_permissions(manage_messages=True):
                    deleted = await message.channel.purge(limit=self.rate, check=same_author)
                    logger.info('Deleted {} message(s)'.format(len(deleted)))

            if notify_after is None:
                delete_after = 10 if report_after is not None else (None if deleted is None else self.per + 10)  # None
                deleted_msg = "" if deleted is None else f"I deleted {len(deleted)} of your last messages. "

                await message.channel.send(f"ఠ_ఠ Slow down, {message.author.mention}! You are spamming! {deleted_msg}"
                                           f"You may send messages again in {round(retry_after)} seconds.",
                                           allowed_mentions=discord.AllowedMentions.none(),
                                           delete_after=delete_after)
            # todo send to moderation log channel

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author == message.guild.me:
            return

        await self.check_spam(message)


def setup(bot):
    bot.add_cog(AutoMod(bot))
