import logging

import aiohttp
import discord
from discord.ext import commands
from discord_slash import cog_ext, SlashContext
from discord_slash.utils.manage_commands import create_option, create_choice

import os
import io
import re
import glob
import typing
import itertools
from urllib.parse import urlparse
from pathlib import Path

from cogs.cog_utils import fuzzy_search, abs_join, send_file
import cogs.permissions as permissions

logger = logging.getLogger(__name__)


def multi_glob(*patterns):
    return itertools.chain.from_iterable(glob.iglob(pattern) for pattern in patterns)


image_exts = (".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff")
guild_ids = [570257083040137237, 568072142843936778]  # TODO REMOVE


class EmoteConverter(commands.Converter):
    async def convert(self, ctx, argument):
        key = fuzzy_search(argument, ctx.cog.emotes.keys(), score_cutoff=30)
        if key is None:
            raise commands.BadArgument(f"Sorry, I cant find emote **{argument}**. "
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

        self.emote_pick.options[0]["choices"] = [create_choice(name=key, value=key) for key in self.emotes.keys()][:25]

        logging.debug(f"Loaded emotes: {self.emotes}")

    @cog_ext.cog_subcommand(base="emote", name="send",
                            options=[
                                create_option(
                                    name="name",
                                    description="Incomplete emote name to send",
                                    option_type=str,
                                    required=True,
                                ),
                            ],
                            guild_ids=guild_ids)
    async def emote_send(self, ctx, name):
        """Sends an emote image. Type incomplete name to send"""
        ctx.cog = self
        emote = await EmoteConverter().convert(ctx, name)
        await self._send_emote(ctx, emote)

    @cog_ext.cog_subcommand(base="emote", name="pick",
                            options=[
                                create_option(
                                    name="emote",
                                    description="Pick emote name to send",
                                    option_type=str,
                                    required=True,
                                ),
                            ],
                            guild_ids=guild_ids)
    async def emote_pick(self, ctx, emote):
        """Sends an emote image. Pick emote name from picker to send"""
        await self._send_emote(ctx, emote)

    async def _send_emote(self, ctx, emote):
        await send_file(ctx.channel, self.emotes[emote])
        await ctx.send(f"Sent '{emote}' emote!", hidden=True)

    @cog_ext.cog_subcommand(base="emote", name="list", guild_ids=guild_ids)
    async def emote_list(self, ctx):
        """Shows list of available emotes."""
        if len(self.emotes) > 0:
            await ctx.send("Available emotes: \n" + "\n".join([f"**{emote}**" for emote in self.emotes.keys()]))
        else:
            await ctx.send("There is no available emotes. Add them with !emote add emote_name")

    @cog_ext.cog_subcommand(base="emote", name="add",
                            options=[
                                create_option(
                                    name="name",
                                    description="Name of emote to add",
                                    option_type=str,
                                    required=True,
                                ),
                                create_option(
                                    name="attachment_link",
                                    description="Link to emote image",
                                    option_type=str,
                                    required=True,
                                ),

                            ],
                            guild_ids=guild_ids)
    @permissions.has_bot_perms()
    async def emote_add(self, ctx, name, attachment_link):
        """Adds new emote. Will replace old emote with same name."""
        ext = Path(urlparse(attachment_link).path).suffix
        if ext not in image_exts:
            raise commands.BadArgument(f"File extension ({ext}) should be one of ({', '.join(image_exts)})")

        if not re.fullmatch("[A-z\\s]+", name):
            raise commands.BadArgument(
                "Emote name should contain only english letters, whitespaces and underscores!")
        filename = f"{name.strip().replace(' ', '_')}{ext}"

        # Create directory for emotes, if it not exists, attachment.save won't do it
        if not os.path.exists("emotes"):
            os.mkdir("emotes")

        async with aiohttp.ClientSession() as session:
            async with session.get(attachment_link) as response:
                if response.status == 200:
                    attachment = await response.read()

        with open(abs_join("emotes", filename), 'wb') as f:
            f.write(attachment)

        self.load_emotes()
        await ctx.send(f"Successfully added emote **{fuzzy_search(filename, self.emotes.keys())}**.")

    @cog_ext.cog_subcommand(base="emote", name="remove",
                            options=[
                                create_option(
                                    name="name",
                                    description="Name of emote to remove",
                                    option_type=str,
                                    required=True,
                                ),
                            ],
                            guild_ids=guild_ids)
    @permissions.has_bot_perms()
    async def emote_remove(self, ctx, name):
        """
        Removes existing emote.
        """
        ctx.cog = self
        emote = await EmoteConverter().convert(ctx, name)
        os.remove(self.emotes[emote])
        self.load_emotes()
        await ctx.send(f"Successfully removed emote **{emote}**.")


def setup(bot):
    bot.add_cog(Emotes(bot))
