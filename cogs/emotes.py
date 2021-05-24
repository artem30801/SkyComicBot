import glob
import io
import itertools
import logging
import os
import re
import zipfile

from itertools import islice
from math import ceil
from pathlib import Path
from urllib.parse import urlparse

import aiohttp
import discord
from PIL import Image, ImageDraw, ImageFont
from discord.ext import commands
from discord_slash import cog_ext, SlashContext
from discord_slash.utils.manage_commands import create_option, create_choice

import cogs.cog_utils as utils
from cogs.cog_utils import fuzzy_search, abs_join, send_file, guild_ids
from cogs.permissions import has_bot_perms

logger = logging.getLogger(__name__)


def multi_glob(*patterns):
    return itertools.chain.from_iterable(glob.iglob(pattern) for pattern in patterns)


def grouper(n, iterable):
    it = iter(iterable)
    return iter(lambda: tuple(islice(it, n)), ())


def text_to_lines(text, max_width, draw, font):
    lines = []
    line = ""
    for word in text.split():
        temp_line = (line + " " + word).strip()
        # print(temp_line, draw.textsize(temp_line, font=font)[0])
        if draw.textsize(temp_line, font=font)[0] >= max_width and line:
            lines.append(line.strip())
            line = word
        else:
            line = temp_line
    lines.append(line)
    return lines


image_exts = (".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff")


class EmoteConverter(commands.Converter):
    async def convert(self, ctx, argument):
        key = fuzzy_search(argument, ctx.cog.emotes.keys(), score_cutoff=30)
        if key is None:
            raise commands.BadArgument(f"Sorry, I cant find emote **{argument}**. "
                                       f"Try */emote list* command to see available emotes")
        return key


class Emotes(utils.AutoLogCog, utils.StartupCog):
    """Emote pictures sending and managing"""

    # :griffin_hug:
    def __init__(self, bot):
        utils.AutoLogCog.__init__(self, logger)
        utils.StartupCog.__init__(self)

        self.bot = bot
        self.emotes = dict()

        self.emotes_thumbnail = abs_join("emotes", "tmp", "thumbnail.png")
        self.has_thumbnail = False

        utils.ensure_path_dirs(self.emotes_thumbnail)

    async def on_startup(self):
        await self.load_emotes()

    async def load_emotes(self):
        files = multi_glob(*(abs_join("emotes", f"*{ext}") for ext in image_exts))

        self.emotes = {os.path.splitext(os.path.split(filename)[1])[0].replace("_", " ").strip().lower():
                           filename for filename in files}

        self.emote_pick.options[0]["choices"] = [create_choice(name=key, value=key) for key in self.emotes.keys()][:25]

        if self.emotes:
            self.generate_thumbnail_image()
            self.has_thumbnail = True
        else:
            os.remove(self.emotes_thumbnail)
            self.has_thumbnail = False

        logger.debug(f"Loaded emotes: {self.emotes}")
        if not self._first_ready:
            await self.bot.slash.sync_all_commands()

    def generate_thumbnail_image(self):
        logger.debug("Constructing thumbnail mosaic image...")
        frame_width = 1920
        images_per_row = min(6, len(self.emotes))
        padding = 15
        v_padding = 100

        max_width = (frame_width - (images_per_row - 1) * padding) / images_per_row

        images = {name: Image.open(path) for name, path in self.emotes.items()}
        images = {k: v for k, v in sorted(images.items(), key=lambda x: (x[1].width / x[1].height, x[0]))}
        image_rows = [dict(row) for row in grouper(images_per_row, images.items())]

        row_heights = []
        for row_num, row in enumerate(image_rows):
            max_height = 0
            for col_num, item in enumerate(row.items()):
                name, image = item

                scale = image.width / max_width
                new_width = ceil(image.width / scale)
                new_height = ceil(image.height / scale)

                image = image.resize((new_width, new_height), Image.ANTIALIAS)
                row[name] = image

                max_height = max(max_height, image.height)

            row_heights.append(max_height)

        total_height = sum(row_heights) + (padding + v_padding) * len(image_rows)
        canvas = Image.new('RGBA', (frame_width, total_height))
        draw = ImageDraw.Draw(canvas)
        font = ImageFont.truetype(utils.abs_join("v_ComicGeek_v1.0.ttf"), size=48)
        y = 0
        for row_num, row in enumerate(image_rows):
            for col_num, item in enumerate(row.items()):
                name, image = item
                x = (padding + max_width) * col_num
                width_diff = (max_width - image.width) / 2
                height_diff = (row_heights[row_num] - image.height) / 2

                x_p = ceil(x + width_diff)
                y_p = ceil(y + height_diff)

                canvas.paste(image, (x_p, y_p))
                # draw.rectangle([(x_p, y_p), (x_p+image.width, y_p+image.height)], outline=(255, 0, 0, 255), width=5)
                # draw.rectangle([(x, y), (x+max_width, y+row_heights[row_num])], outline=(0, 255, 0, 255), width=5)
                if Path(self.emotes[name]).suffix == ".gif":
                    name += " [GIF]"

                text = "\n".join(text_to_lines(name, max_width, draw, font))
                draw.text((ceil(x_p + max_width / 2), y + row_heights[row_num] + padding),
                          text, anchor="ma", align="center", font=font)

            y += row_heights[row_num] + padding + v_padding
        logger.info("Constructed thumbnail mosaic image")

        with open(self.emotes_thumbnail, "wb") as image_file:
            canvas.save(image_file, "PNG")

        logger.debug("Saved thumbnail mosaic image")

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
    async def emote_send(self, ctx: SlashContext, name: str):
        """Sends an emote image. Type incomplete name to send"""
        await ctx.defer(hidden=True)
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
    async def emote_pick(self, ctx: SlashContext, emote: str):
        """Sends an emote image. Pick emote name from picker to send (only first 25 will be shown)"""
        await ctx.defer(hidden=True)
        await self._send_emote(ctx, emote)

    async def _send_emote(self, ctx, emote):
        await send_file(ctx.channel, self.emotes[emote])
        await ctx.send(f"Sent '{emote}' emote!", hidden=True)

    @cog_ext.cog_subcommand(base="emote", name="list", guild_ids=guild_ids)
    async def emote_list(self, ctx):
        """Shows list of available emotes."""
        if not self.has_thumbnail:
            await ctx.send("There is no available emotes. "
                           "`Add them with /emote add name:<name> attachment_link:<link>`")
            return

        await ctx.defer()

        embed = utils.bot_embed(self.bot)
        embed.title = f"Available emotes ({len(self.emotes)} total)"
        embed.description = "Click on the image to enlarge"
        embed.set_footer(text="Use `/emote pick <emote>` to send those emotes!",
                         icon_url=self.bot.user.avatar_url)

        embed.set_image(url="attachment://emotes.png")
        await ctx.send(embed=embed, file=discord.File(self.emotes_thumbnail, filename="emotes.png"))

    @cog_ext.cog_subcommand(base="emote", name="archive", guild_ids=guild_ids)
    async def emote_archive(self, ctx: SlashContext):
        """Sends zip archive containing all emote images"""
        await ctx.defer()

        with io.BytesIO() as zip_binary:
            with zipfile.ZipFile(zip_binary, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
                for path in self.emotes.values():
                    zf.write(path, os.path.basename(path))
            zip_binary.seek(0)

            embed = utils.bot_embed(self.bot)
            embed.title = f"Compressed emotes archive ({len(self.emotes)} total)"
            embed.description = f"Here, I prepared a compressed archive of all the emotes in my storage!"

            await ctx.send(embed=embed, file=discord.File(zip_binary, filename="emotes.zip"))

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
    async def emote_add(self, ctx: SlashContext, name: str, attachment_link: str):
        """Adds new emote. Will replace old emote with same name."""
        await ctx.defer(hidden=False)
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
                if response.ok:
                    attachment = await response.read()

        with open(abs_join("emotes", filename), 'wb') as f:
            f.write(attachment)
        logger.important(f"Saved emote '{name}' as '{filename}'")

        await self.load_emotes()
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
    async def emote_remove(self, ctx: SlashContext, name: str):
        """Deletes existing emote."""
        await ctx.defer(hidden=False)
        logger.important(f"{self.format_caller(ctx)} trying to remove emote '{name}'")

        ctx.cog = self
        emote = await EmoteConverter().convert(ctx, name)
        os.remove(self.emotes[emote])
        logger.important(f"Removed emote '{emote}' file '{self.emotes[emote]}'")

        await self.load_emotes()
        await ctx.send(f"Successfully removed emote **{emote}**.")


def setup(bot):
    bot.add_cog(Emotes(bot))
