import logging
import random
import re

import discord
from discord import Message
from discord.ext import commands
from discord.ext.commands.errors import RoleNotFound

from cogs.cog_utils import abs_join, send_file
import cogs.cog_utils as utils


logger = logging.getLogger(__name__)


def contains_word(message: str, word: str) -> bool:
    """Returns true if there is given word in the message"""
    # \A - start of the string, \Z - end of the string, \W - not a word character
    check_result = re.search(f"(\\A|\\W){word}(\\Z|\\W)", message, re.IGNORECASE)
    return check_result is not None


class Reactions(commands.Cog):
    """Automatic context reactions"""

    def __init__(self, bot):
        self.bot = bot
        self._reactions = {("telling", ): self.telling,
                           ("wrong layer", "wrong\\s[\\w]+\\slayer"): self.wrong_layer,
                           ("hug", "hugs"): self.hug,
                           ("suselle", ): self.suselle,
                           ("soriel", ): self.soriel,
                           }

    @commands.Cog.listener()
    async def on_message(self, message: Message):
        if message.author.bot:
            return

        if message.guild and message.channel:
            if self.bot.get_cog("Channels").is_no_reactions_channel(message.channel):
                return

            if self.bot.get_cog("Channels").is_update_monitor_channel(message.channel):
                await self.notify_update(message)

        for keys, react in self._reactions.items():
            if any(contains_word(message.content, key) for key in keys):
                logger.debug(f"Reacted to '{message.content}' message (contains {keys})")
                try:
                    await react(message)
                except commands.EmojiNotFound as e:
                    logger.warning(e)
                break

    async def notify_update(self, message):
        logger.info("Reacted on update")
        try:
            role = discord.utils.get(message.guild.roles, name=utils.update_crew_role)
        except RoleNotFound:
            role = None
        notify_message = self.get_update_message(role.mention if role else "Folks", message.channel.mention)
        notify_channels = await self.bot.get_cog("Channels").get_update_notify_channels(message.guild)
        for channel in notify_channels:
            await channel.send(notify_message)

    async def telling(self, message):
        await send_file(message.channel, abs_join(self.bot.current_dir, "reactions", "telling.gif"),
                        "thatwouldbetelling.gif")

    async def wrong_layer(self, message):
        await send_file(message.channel, abs_join(self.bot.current_dir, "reactions", "wrong_layer.gif"),
                        "wronglayersong.gif")

    def get_emoji(self, emoji_name):
        # emoji = commands.EmojiConverter().convert(ctx, emoji_name)
        emoji = discord.utils.get(self.bot.emojis, name=emoji_name)
        if emoji:
            return emoji
        else:
            raise commands.EmojiNotFound(emoji_name)

    async def add_emojis(self, message, *emojis):
        for emoji in emojis:
            await message.add_reaction(emoji)

    async def hug(self, message):
        await message.add_reaction(self.get_emoji("griffin_hug"))

    async def suselle(self, message):
        await self.add_emojis(message,
                              self.get_emoji("PT_armless_babies"),
                              "🇽",  # Note! That's 🇽, not x
                              self.get_emoji("PT_excited_noelle")
                              )

    async def soriel(self, message):
        await self.add_emojis(message,
                              "🐐", "🇽", "💀"  # Note! That's 🇽, not x
                              )

    @staticmethod
    def get_update_message(update_role, update_channel) -> str:
        notification = [
            "{}, update is here! Check {}",
            "{}, check the {}, there is an update!",
            "{}, cool stuff arrived to {}!",
            "{}, all to the {}, new update!",
            "{}, hey-hey-hey, new part in {}!",
            "{}, there is an update in {}!",
            "{}, update has arrived! Check {}",
        ]
        return random.choice(notification).format(update_role, update_channel)


def setup(bot):
    bot.add_cog(Reactions(bot))
