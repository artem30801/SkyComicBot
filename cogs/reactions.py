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
    
    real_life_channel_id = 689301702096191537
    updates_channel_id = 329098775228448769

    def __init__(self, bot):
        self.bot = bot
        self._reactions = {("telling", ): self.telling,
                           ("wrong layer", "wrong\\s[\\w]+\\slayer"): self.wrong_layer,
                           ("hug", "hugs"): self.hug}

    @commands.Cog.listener()
    async def on_message(self, message: Message):
        if message.author.bot:
            return

        if message.guild and message.channel:
            if await self.bot.get_cog("Channels").is_no_reactions_channel(message.channel):
                return
            
            if await self.bot.get_cog("Channels").is_update_monitor_channel(message.channel):
                logger.info("Reacted on update")
                try:
                    role = await commands.RoleConverter().convert(message, utils.update_crew_role)
                except RoleNotFound:
                    role = None
                notify_message = self.get_update_message(role.mention if role else "Folks", message.channel.mention)
                notify_channels = await self.bot.get_cog("Channels").get_update_notify_channels(message.guild)
                for channel in notify_channels:
                    await channel.send(notify_message)

        for keys, react in self._reactions.items():
            if any(contains_word(message.content, key) for key in keys):
                logger.debug(f"Reacted to '{message.content}' message (contains {keys})")
                await react(message)
                break

    async def telling(self, message):
        await send_file(message.channel, abs_join(self.bot.current_dir, "reactions", "telling.gif"),
                        "thatwouldbetelling.gif")

    async def wrong_layer(self, message):
        await send_file(message.channel, abs_join(self.bot.current_dir, "reactions", "wrong_layer.gif"),
                        "wronglayersong.gif")

    async def hug(self, message):
        collection = self.bot.emojis
        emoji = discord.utils.get(collection, name='griffin_hug')
        if emoji:
            await message.add_reaction(emoji)
        else:
            logger.warning("Failed to get hug emoji!")
    
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
