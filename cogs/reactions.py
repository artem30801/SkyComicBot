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


def re_contains(words: [str]) -> re.Pattern:
    """Returns true if there is given word in the message"""
    re_string = r"|".join([rf"\b{word}\b" for word in words])
    return re.compile(re_string, flags=re.IGNORECASE)


class Reactions(commands.Cog):
    """Automatic context reactions"""

    def __init__(self, bot):
        self.bot = bot
        self.x_emojis = None
        reactions = {("telling",): self.telling,
                     ("wrong layer",): self.wrong_layer,
                     ("hug", "hugs",): self.hug,
                     ("suselle",): self.suselle,
                     ("krusele", "kruselle",): self.kruselle,
                     ("krusie",): self.krusie,
                     ("krusielle",): self.krusielle,
                     ("kralsei",): self.kralsei,
                     ("krisusei",): self.krisusei,
                     ("rainbow ralsei", "hyperfloof", "hyperfluff", "polyralsei",): self.hyperfloof,
                     ("shebus", "shaebus", "phanti",): self.phoebus_shanti,
                     ("soriel",): self.soriel,
                     }
        self._reactions = [(re_contains(words), react) for words, react in reactions.items()]

    @commands.Cog.listener()
    async def on_message(self, message: Message):
        if message.author.bot:
            return

        if message.guild and message.channel:
            if self.bot.get_cog("Channels").is_no_reactions_channel(message.channel):
                return

            if self.bot.get_cog("Channels").is_update_monitor_channel(message.channel):
                await self.notify_update(message)

        to_react = []
        for re_expression, react_func in self._reactions:
            if (match := re_expression.search(message.content)) is not None:
                logger.debug(f"Matched reaction to '{message.content}' message (matches '{re_expression.pattern}')")
                to_react.append((match.start(), re_expression, react_func))

        if not to_react:
            return

        self.reset_x_emojis()
        to_react.sort(key=lambda x: x[0])
        for _, re_expression, react_func in to_react:
            try:
                await react_func(message)
            except commands.EmojiNotFound as e:
                logger.warning(e)
            except RuntimeError:
                logger.debug("Ran out of x's to separate ships with")

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

    def reset_x_emojis(self):
        emojis = ["🇽", "❌", "❎", ]
        random.shuffle(emojis)
        self.x_emojis = iter(emojis)

    def get_x_emoji(self):
        return next(self.x_emojis)

    def get_emoji(self, emoji_name):
        # emoji = commands.EmojiConverter().convert(ctx, emoji_name)
        emoji = discord.utils.get(self.bot.emojis, name=emoji_name)
        if emoji:
            return emoji
        else:
            raise commands.EmojiNotFound(emoji_name)

    @staticmethod
    async def add_emojis(message, *emojis):
        for emoji in emojis:
            await message.add_reaction(emoji)

    async def hug(self, message):
        await message.add_reaction(self.get_emoji("griffin_hug"))

    async def suselle(self, message):
        await self.add_emojis(message,
                              self.get_emoji("PT_armless_babies"),
                              self.get_x_emoji(),
                              self.get_emoji("PT_excited_noelle")
                              )

    async def kruselle(self, message):
        await self.add_emojis(message,
                              self.get_emoji("PT_kris_shrug"),
                              self.get_x_emoji(),
                              self.get_emoji("PT_excited_noelle")
                              )

    async def krusie(self, message):
        await self.add_emojis(message,
                              self.get_emoji("PT_kris_shrug"),
                              self.get_x_emoji(),
                              self.get_emoji("PT_armless_babies")
                              )

    async def krusielle(self, message):
        await self.add_emojis(message,
                              self.get_emoji("PT_kris_shrug"),
                              self.get_x_emoji(),
                              self.get_emoji("PT_armless_babies"),
                              self.get_x_emoji(),
                              self.get_emoji("PT_excited_noelle")
                              )

    async def kralsei(self, message):
        await self.add_emojis(message,
                              self.get_emoji("PT_kris_shrug"),
                              self.get_x_emoji(),
                              self.get_emoji("PT_RalseiIdea")
                              )

    async def krisusei(self, message):
        await self.add_emojis(message,
                              self.get_emoji("PT_kris_shrug"),
                              self.get_x_emoji(),
                              self.get_emoji("PT_armless_babies"),
                              self.get_x_emoji(),
                              self.get_emoji("PT_RalseiIdea")
                              )

    async def hyperfloof(self, message):
        await self.add_emojis(message,
                              self.get_emoji("PT_RalseiIdea"),
                              self.get_emoji("PT_EsliraFreakout"),
                              self.get_emoji("PT_Fried_Aelsir"),
                              self.get_emoji("PT_Irales_Face"),
                              self.get_emoji("PT_ScrewItSareli"),
                              '🍋',  # Yep, that's lemon
                              )

    async def phoebus_shanti(self, message):
        await self.add_emojis(message,
                              self.get_emoji("shantisdone"),
                              self.get_x_emoji(),
                              self.get_emoji("ShantiWTF")
                              )

    async def soriel(self, message):
        await self.add_emojis(message,
                              "🐐",
                              self.get_x_emoji(),
                              "💀"
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
