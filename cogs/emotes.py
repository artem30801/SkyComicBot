import logging

import discord
from discord.ext import commands

import os
import re
import glob
import typing
import itertools

from cog_utils import fuzzy_search, abs_join, send_file

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


def multi_glob(*patterns):
    return itertools.chain.from_iterable(glob.iglob(pattern) for pattern in patterns)


image_exts = (".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff")


class EmoteConverter(commands.Converter):
    async def convert(self, ctx, argument):
        key = fuzzy_search(argument, ctx.cog.emotes.keys())
        if key is None:
            raise commands.BadArgument(f"Sorry, I cant find emote '{emote_name}'. "
                                       f"Try *!emote list* command to see available emotes")
        return key


class Emotes(commands.Cog):
    """Emote pictures sending and managing"""

    # :griffin_hug:
    def __init__(self, bot):
        self.bot = bot
        self.emotes = dict()
        self.load_emotes()

    def load_emotes(self):

        files = multi_glob(*(abs_join("emotes", f"*{ext}") for ext in image_exts))

        self.emotes = {os.path.splitext(os.path.split(filename)[1])[0].replace("_", " ").strip().lower():
                           filename for filename in files}
        logging.debug(f"Loaded emotes: {self.emotes}")

    @commands.group(aliases=["emotes", "e", ], case_insensitive=True, invoke_without_command=True)
    async def emote(self, ctx, *, emote_name: EmoteConverter):
        """
        Sends an emote image.
        Use subcommands to manage emotes."""

        await send_file(ctx.channel, self.emotes[emote_name])

    @emote.command(aliases=["all", "available", "view", ])
    async def list(self, ctx):
        """Shows list of available emotes."""
        await ctx.send("Available emotes: \n" + "\n".join(self.emotes.keys()))

    @emote.command(aliases=["new", ])
    async def add(self, ctx, *, name: typing.Optional[str]):
        """
        Adds new emote.
        Attach image file to your message.
        """
        attachments = ctx.message.attachments
        if not attachments:
            raise commands.BadArgument("Missing attached image")

        attachment = attachments[0]
        filename = attachment.filename
        ext = os.path.splitext(filename)[1]
        if ext not in image_exts:
            raise commands.BadArgument(f"File extension ({ext}) be one of {', '.join(image_exts)}")

        if name is not None:
            if not re.fullmatch("[A-z\\s]+", name):
                raise commands.BadArgument(
                    "Emote name should contain only english letters, whitespaces and underscores")
            filename = f"{name.strip().replace(' ', '_')}{ext}"
        await attachment.save(abs_join("emotes", filename))
        self.load_emotes()
        file = discord.File(abs_join("emotes", filename), filename=filename)
        await ctx.send(f"Successfully added emote {fuzzy_search(filename, self.emotes.keys())}", file=file)

    @emote.command(aliases=["delete", ])
    async def remove(self, ctx, emote_name: EmoteConverter):
        """
        Removes existing emote.
        """
        os.remove(self.emotes[emote_name])
        self.load_emotes()
        await ctx.send(f"Successfully removed emote {emote_name}")
