import discord
from discord.ext import commands

import re
import os

def contains_word(message: str, word: str) -> bool:
    """Returns true if there is given word in the message"""
    # \A - start of the string, \Z - end of the string, \W - not a word character
    check_result = re.search(f"(\\A|\\W){word}(\\Z|\\W)", message, re.IGNORECASE)
    return check_result is not None


class Reactions(commands.Cog):
    """Automatic context reactions"""
    # :griffin_hug:
    def __init__(self, bot):
        self.bot = bot
        self._reactions = {("telling", ): self.telling,
                           ("wrong layer", "wrong\\s[\\w]+\\slayer"): self.wrong_layer,
                           ("hug", "hugs"): self.hug}

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.guild is not None and message.channel.name == "real-life-talk":
            return

        for keys, react in self._reactions.items():
            if any(contains_word(message.content, key) for key in keys):
                await react(message)

    async def send_file(self, message, path, filename=None):
        file = discord.File(os.path.abspath(path), filename=filename or path)
        await message.channel.send(" ", file=file)

    async def telling(self, message):
        await self.send_file(message, os.path.join("reactions", "telling.gif"), "thatwouldbetelling.gif")

    async def wrong_layer(self, message):
        await self.send_file(message, os.path.join("reactions", "wrong_layer.gif"), "wronglayersong.gif")

    async def hug(self, message):
        collection = self.bot.emojis
        emoji = discord.utils.get(collection, name='griffin_hug')
        await message.add_reaction(emoji)

