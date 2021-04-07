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
import cogs.cog_utils as utils
from cogs.permissions import has_server_perms, has_bot_perms

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


class Emotes(utils.AutoLogCog):
    """Emote pictures sending and managing"""

    # :griffin_hug:
    def __init__(self, bot):
        utils.AutoLogCog.__init__(self, logger)
        self.bot = bot
        self.emotes = dict()
        self.load_emotes()

    def load_emotes(self):
        logger.debug(f'Loading emotes from: {[abs_join("emotes", f"*{ext}") for ext in image_exts]}')
        files = multi_glob(*(abs_join("emotes", f"*{ext}") for ext in image_exts))

        self.emotes = {os.path.splitext(os.path.split(filename)[1])[0].replace("_", " ").strip().lower():
                           filename for filename in files}

        self.emote_pick.options[0]["choices"] = [create_choice(name=key, value=key) for key in self.emotes.keys()][:25]

        logger.debug(f"Loaded emotes: {self.emotes}")

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
        """Sends an emote image. Pick emote name from picker to send (only first 25 will be shown)"""
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
    @has_bot_perms()
    async def emote_add(self, ctx, name, attachment_link):
        """Adds new emote. Will replace old emote with same name."""
        logger.important(f"{self.format_caller(ctx)} trying to add emote '{name}' (image link - {attachment_link})")

        ext = Path(urlparse(attachment_link).path).suffix
        if ext not in image_exts:
            logger.error(f"Unsupported image extension '{ext}'")
            raise commands.BadArgument(f"File extension ({ext}) should be one of ({', '.join(image_exts)})")

        if not re.fullmatch("[A-z\\s]+", name):
            logger.error(f"Unsupported image name '{name}'")
            raise commands.BadArgument(
                "Emote name should contain only english letters, whitespaces and underscores!")
        filename = f"{name.strip().replace(' ', '_')}{ext}"

        # Create directory for emotes, if it not exists, attachment.save won't do it
        utils.ensure_dir(os.path.abspath("emotes"))

        async with aiohttp.ClientSession() as session:
            async with session.get(attachment_link) as response:
                if response.status == 200:
                    attachment = await response.read()

        with open(abs_join("emotes", filename), 'wb') as f:
            f.write(attachment)
        logger.important(f"Saved emote '{name}' as '{filename}'")

        self.load_emotes()
        await ctx.send(f"Successfully added emote **{fuzzy_search(filename, self.emotes.keys())}**.")

    @cog_ext.cog_subcommand(base="emote", name="delete",
                            options=[
                                create_option(
                                    name="name",
                                    description="Name of emote to delete",
                                    option_type=str,
                                    required=True,
                                ),
                            ],
                            guild_ids=guild_ids)
    @has_bot_perms()
    async def emote_remove(self, ctx, name):
        """Deletes existing emote."""
        logger.important(f"{self.format_caller(ctx)} trying to remove emote '{name}'")

        ctx.cog = self
        emote = await EmoteConverter().convert(ctx, name)
        os.remove(self.emotes[emote])
        logger.important(f"Removed emote '{emote}' file '{self.emotes[emote]}'")

        self.load_emotes()
        await ctx.send(f"Successfully removed emote **{emote}**.")


def setup(bot):
    bot.add_cog(Emotes(bot))
