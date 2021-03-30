import discord
from discord.ext import commands

from tortoise.models import Model
from tortoise.functions import Max
from tortoise import fields
from tortoise import exceptions
from tortoise.transactions import atomic
from tortoise.signals import pre_delete

import re
import os
import PIL.Image
import io
import asyncio
import aiohttp
from concurrent.futures import ThreadPoolExecutor

from dataclasses import dataclass
import typing

import param_coverter as converters
from cog_utils import ensure_dir, abs_join, CommandsAliases
import cog_utils as utils
from cogs import db_utils


def next_number(model_name, field="number"):
    def inner():
        loop = asyncio.get_event_loop()
        model = globals()[model_name]
        max_number = loop.run_until_complete(model.annotate(m=Max(field)).values_list("m", flat=True))[0]
        return max_number + 1 if max_number is not None else 1

    return inner


class Comic(Model):
    id = fields.IntField(pk=True)
    name = fields.CharField(max_length=250, unique=True)
    number = fields.IntField(default=next_number("Comic"))
    url = fields.TextField(null=True)
    description = fields.TextField(null=True)
    author = fields.ForeignKeyField("models.Author", related_name="comics")
    # cover_path = fields.TextField(null=True)
    embed_color = db_utils.ColorField(default=discord.Colour(0))
    arcs: fields.ReverseRelation["Arc"]

    class Meta:
        ordering = ["number"]

    class FileStorage:
        cover = db_utils.FileStorageField(nullable=True)
        thumbnail = db_utils.FileStorageField(nullable=True)
    # language = fields.TextField(default="english")


class Author(Model):
    id = fields.IntField(pk=True)
    name = fields.CharField(max_length=250, unique=True)
    number = fields.IntField(default=next_number("Author"))
    url = fields.TextField(null=True)
    discord_id_member = db_utils.UserField(null=True)
    # avatar_path = fields.TextField(null=True)
    embed_color = db_utils.ColorField(default=discord.Colour(0))
    comics: fields.ReverseRelation["Comic"]

    class Meta:
        ordering = ["number"]

    class FileStorage:
        avatar = db_utils.FileStorageField(nullable=True)


class Arc(Model):
    id = fields.IntField(pk=True)
    name = fields.CharField(max_length=250)
    number = fields.IntField(default=next_number("Arc"))
    comic = fields.ForeignKeyField("models.Comic", related_name="arcs")
    parts: fields.ReverseRelation["Part"]

    class Meta:
        ordering = ["number"]
        unique_together = (("name", "comic"),)

    class FileStorage:
        cover = db_utils.FileStorageField(nullable=True)


class Part(Model):
    id = fields.IntField(pk=True)
    name = fields.CharField(max_length=250, null=True)
    number = fields.IntField(default=next_number("Part"))
    text = fields.TextField(null=True)
    arc = fields.ForeignKeyField("models.Arc", related_name="parts")
    pages: fields.ReverseRelation["Page"]

    class Meta:
        ordering = ["number"]
        unique_together = (("name", "arc"),)


class Page(Model):
    id = fields.IntField(pk=True)
    number = fields.IntField(default=next_number("Page"))
    # path = fields.TextField()
    page = fields.ForeignKeyField("models.Part", related_name="pages")

    class Meta:
        ordering = ["number"]


@pre_delete(Author, Comic, Page)
async def signal_pre_delete(sender, instance, using_db) -> None:
    for name in converters.ModelParamConverter.get_file_fields(sender).keys():
        await Comics.remove_img(instance, name)


class AuthorConverter(converters.ModelConverter):
    def __init__(self):
        super().__init__(Author)

    async def convert(self, ctx, argument):
        try:
            discord_author = await commands.MemberConverter().convert(ctx, argument)
            return await Author.get(discord_id_member=discord_author.id)
        except (commands.MemberNotFound, exceptions.DoesNotExist):
            pass

        return await super().convert(ctx, argument)


class ComicConverter(converters.ModelConverter):
    def __init__(self):
        super().__init__(Comic)


class ArcConverter(converters.ModelConverter):
    def __init__(self):
        super().__init__(Arc)


class PartConverter(converters.ModelConverter):
    def __init__(self):
        super().__init__(Part)


class PageConverter(converters.ModelConverter):
    def __init__(self):
        super().__init__(Page)


fk_dict = {"author": Author, "comic": Comic, "arc": Arc, "part": Part, "page": Page}


@dataclass
class CurrentlyViewing:
    author: int = None
    comic: int = None
    arc: int = None
    part: int = None
    page: int = None


@typing.no_type_check
class Comics(commands.Cog):
    """Comic reading an management commands"""

    def __init__(self, bot):
        self.bot: commands.Bot = bot
        db_utils.UserField.bot = bot

        self.currently_viewing = dict()
        self.executor = ThreadPoolExecutor()
        ensure_dir(abs_join("comic_storage"))

        for command in filter(lambda c: c.name in ("new",), self.__cog_commands__):
            command.help = f"Adds new {command.parent.name} to internal DB"

        for command in filter(lambda c: c.name in ("edit",), self.__cog_commands__):
            command.help = f"Edits existing {command.parent.name} in internal DB"

        for command in filter(lambda c: c.name in ("remove",), self.__cog_commands__):
            command.help = f"Removes existing {command.parent.name} from internal DB"

        for command in filter(lambda c: c.name in ("new", "edit"), self.__cog_commands__):
            model = fk_dict[command.parent.name]
            command.help = (command.help or '') + '\n' + converters.ModelParamConverter(model, fk_dict).get_help()

    def get_current(self, ctx):
        return self.currently_viewing.setdefault(ctx.channel.id, CurrentlyViewing())

    @staticmethod
    async def reshuffle(instance, params_dict, converter):
        if "number" in params_dict:
            query = type(instance)
            fk_field = [key for key, field in converter.conversion_schema.items() if field.fk]
            if fk_field and fk_field[0] in fk_dict:
                parent_name = fk_field[0]
                query = query.filter(**{parent_name: await getattr(instance, parent_name)})

            await db_utils.reshuffle(query, instance, params_dict["number"])

    async def store_images(self, instance, params_dict, converter):
        for key, field in converter.file_fields.items():
            await self.store_image(instance, params_dict, key)

    async def store_image(self, instance, params_dict, field_name="file"):
        try:
            files = params_dict.pop(field_name)
        except KeyError:
            return

        path = await self.get_path(instance, field_name)
        if files is None:
            await self.remove_img(instance, field_name)
            return

        file_url = files[0]
        await self.save_img_async(await self.download(file_url), path)

    @classmethod
    async def remove_img(cls, instance, field_name=None):
        path = await cls.get_path(instance, field_name)
        if os.path.isfile(path):
            os.remove(path)

        directory = os.path.dirname(path)
        if os.path.isdir(directory) and not os.listdir(directory):
            os.rmdir(directory)

    @staticmethod
    async def download(url):
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    buffer = io.BytesIO(await response.read())
                else:
                    raise commands.BadArgument(f"Can not download file from {url}")
        return buffer

    @staticmethod
    def save_img(f, path):
        original = PIL.Image.open(f)
        original.save(path, format="png")

    async def save_img_async(self, f, path):
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(self.executor, self.save_img, f, path)

    @staticmethod
    async def get_path(instance, field_name=None):
        model = type(instance)
        path = ["comic_storage"]
        if model is Author:
            author = instance
            current_path = ["authors", f"{author.id}.png"]

        elif model is Comic:
            comic = instance
            current_path = ["comics", str(comic.id),
                            "cover.png" if field_name == "cover" else "thumbnail.png"]
        elif model is Arc:
            arc = instance
            comic = await arc.comic
            current_path = ["comics", str(comic.id), str(arc.id), "cover.png"]

        elif model is Page:
            part = instance
            page = await part.page
            arc = await page.arc
            comic = await arc.comic
            current_path = ["comics", str(comic.id), str(arc.id), f"{part.id}.png"]
        else:
            raise ValueError

        path.extend(current_path)
        dirs = []
        for element in path[:-1]:
            dirs.append(element)
            ensure_dir(abs_join(*dirs))

        return abs_join(*path)

    @atomic()
    async def generic_add(self, ctx, model, name, params, parsed=None):
        converter = converters.ModelParamConverter(model, fk_dict, check_required=True)
        parsed = parsed or dict()
        parsed["name"] = name
        params_dict = await converter.convert(ctx, params, parsed)
        display_params = params_dict.copy()

        instance = await model.create(**params_dict)
        await self.reshuffle(instance, params_dict, converter)
        await self.store_images(instance, params_dict, converter)

        print(instance.name, )
        await ctx.send(f"Added **{converters.instance_name(instance, converter)}** "
                       f"to *{converters.model_name(instance)}s* "
                       f"with params: {converters.format_converted(display_params)} \n"
                       f"Resulting data: {await converters.format_fields(instance, converter, fk_dict)}"
                       )

    @atomic()
    async def generic_edit(self, ctx, instance, params, parsed=None):
        converter = converters.ModelParamConverter(type(instance), fk_dict)
        parsed = parsed or dict()
        params_dict = await converter.convert(ctx, params, parsed)
        display_params = params_dict.copy()
        print(display_params)

        await self.reshuffle(instance, params_dict, converter)
        instance.update_from_dict(params_dict)
        await instance.save()

        await self.store_images(instance, params_dict, converter)

        await ctx.send(f"Edited **{converters.instance_name(instance, converter)}** "
                       f"in *{converters.model_name(instance)}s* "
                       f"with params: {converters.format_converted(display_params)} \n"
                       f"Resulting data: {await converters.format_fields(instance, converter, fk_dict)}"
                       )

    async def generic_remove(self, ctx, instance):
        converter = converters.ModelParamConverter(type(instance), fk_dict)
        await instance.delete()
        await ctx.send(f"Removed **{converters.instance_name(instance, converter)}** "
                       f"from *{converters.model_name(instance)}s*"
                       )

    async def set_context(self, ctx, instance):
        current = self.get_current(ctx)
        # kwargs.update({key: None for key in fk_dict if key not in kwargs})
        current_dict = {key: None for key in fk_dict}
        name = type(instance).__name__.lower()
        current_dict[name] = instance.id
        keys_list = list(fk_dict.keys())

        for key in reversed(keys_list[:keys_list.index(name)]):
            instance = await getattr(instance, key)
            current_dict[key] = instance.id

        print("current=", current_dict)
        current.__dict__.update(current_dict)

    async def get_context_instance(self, ctx, instance, name):
        if instance is not None:
            return instance

        current = self.get_current(ctx)
        print(current)
        instance_id = getattr(current, name)
        print(instance_id, name)
        if instance_id is None:
            raise commands.BadArgument(f"Cannot infer {name} from context of this channel!")

        return await fk_dict[name].get(id=instance_id)

    # async def get_context_params(self, ctx, *args):
    #     current = self.get_current(ctx)
    #     context_params = dict()
    #     for arg in args:  # TODODOOOOOOOOOOOO
    #         instance_id = getattr(current, arg)
    #         if instance_id is not None:
    #             instance = await fk_dict[arg].get(id=instance_id)
    #             context_params[arg] = instance
    #     return context_params

    @staticmethod
    def format_author_links(author):
        links = f"Link: [{utils.url_hostname(author.url)}]({author.url})" if author.url else author.name
        if author.discord_id_member:
            links = f"{links} (Discord: {author.discord_id_member.mention})"
        return links

    @staticmethod
    async def format_author_comics(author):
        comic_list = "\n".join([f"***{comic.name}*** *(number: {comic.number})*" async for comic in author.comics])
        return comic_list or "No comics by this author published yet"

    @staticmethod
    async def format_children(query, f_string):
        comic_list = "\n".join([f_string.format(child) async for child in query])
        return comic_list

    @typing.no_type_check
    @commands.group(aliases=["authors", "artist", "artists", "creator", "creators"], invoke_without_command=True)
    @typing.no_type_check
    async def author(self, ctx, author: typing.Optional[AuthorConverter] = None):
        author = await self.get_context_instance(ctx, author, "author")
        await self.set_context(ctx, author)
        embed = discord.Embed(title=f"Author: {author.name}", color=author.embed_color)

        files = list()
        path = await self.get_path(author)
        if os.path.isfile(path):
            file = discord.File(path, filename="avatar.png")
            embed.set_thumbnail(url="attachment://avatar.png")
            files.append(file)

        formatter = "***{0.name}*** *(number: {0.number})*"
        embed.add_field(name="Comics by this author:",
                        value=await self.format_children(author.comics, formatter) or
                              "No comics by this author published yet",
                        inline=True)

        embed.description = f"**{self.format_author_links(author)}**"
        await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none(), files=files)

    @author.command(name="list", aliases=CommandsAliases.list_aliases)
    async def author_list(self, ctx):
        embed = discord.Embed(title=f"Authors:", color=utils.embed_color)

        formatter = "***{0.name}*** *(number: {0.number})*"
        async for author in Author.all():
            embed.add_field(name=f"{author.name}",
                            value=f"**{self.format_author_links(author)}**\n    "
                                  "*Comics by this author:*\n" +
                                  await self.format_children(author.comics, formatter) or
                                  "No comics by this author published yet",
                            inline=False)
        if not embed.fields:
            embed.description = "Woe is me, there are no authors!"

        await ctx.send(embed=embed)

    @author.command(name="new", aliases=CommandsAliases.new_aliases)
    async def author_new(self, ctx, name, *, params=""):
        await self.generic_add(ctx, Author, name, params)

    @author.command(name="edit", aliases=CommandsAliases.edit_aliases)
    @typing.no_type_check
    async def author_edit(self, ctx, author: typing.Optional[AuthorConverter] = None, *, params=""):
        author = await self.get_context_instance(ctx, author, "author")
        await self.generic_edit(ctx, author, params)

    @author.command(name="remove", aliases=CommandsAliases.remove_aliases)
    @typing.no_type_check
    async def author_remove(self, ctx, author: typing.Optional[AuthorConverter] = None):
        author = await self.get_context_instance(ctx, author, "author")
        await self.generic_remove(ctx, author)

    @commands.group(aliases=["comics", "c"], invoke_without_command=True)
    @typing.no_type_check
    async def comic(self, ctx, comic: typing.Optional[ComicConverter] = None):
        comic = await self.get_context_instance(ctx, comic, "comic")
        await self.set_context(ctx, comic)

        embed = discord.Embed(title=f"Comic: {comic.name}", color=comic.embed_color)
        files = list()

        author = await comic.author
        author_kwargs = dict()
        if author.url is not None:
            author_kwargs["url"] = author.url

        author_path = await self.get_path(author)
        if os.path.isfile(author_path):
            file = discord.File(author_path, filename="avatar.png")
            author_kwargs["icon_url"] = "attachment://avatar.png"
            files.append(file)
        embed.set_author(name=f"Author: {author.name}", **author_kwargs)

        path = await self.get_path(comic, "cover")
        if os.path.isfile(path):
            file = discord.File(path, filename="cover.png")
            embed.set_image(url="attachment://cover.png")
            files.append(file)

        path = await self.get_path(comic, "thumbnail")
        if os.path.isfile(path):
            file = discord.File(path, filename="thumbnail.png")
            embed.set_thumbnail(url="attachment://thumbnail.png")
            files.append(file)

        embed.description = comic.description or ""

        formatter = "Arc {0.number} - {0.name}"
        embed.add_field(name="Arcs:",
                        value=await self.format_children(comic.arcs, formatter) or
                              "No arcs in this comic yet",
                        inline=True)

        await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none(), files=files)

    @comic.command(name="list", aliases=CommandsAliases.list_aliases)
    async def comic_list(self, ctx, author: typing.Optional[AuthorConverter] = None):
        if author is not None:
            await self.author(ctx, author)
            return
        print(db_utils.UserField.bot)

    @comic.command(name="new", aliases=CommandsAliases.new_aliases)
    async def comic_new(self, ctx, name, *, params=""):
        await self.generic_add(ctx, Comic, name, params)

    @comic.command(name="edit", aliases=CommandsAliases.edit_aliases)
    async def comic_edit(self, ctx, comic: typing.Optional[ComicConverter], *, params=""):
        comic = await self.get_context_instance(ctx, comic, "comic")
        await self.generic_edit(ctx, comic, params)

    @comic.command(name="remove", aliases=CommandsAliases.remove_aliases)
    async def comic_remove(self, ctx, comic: typing.Optional[ComicConverter]):
        comic = await self.get_context_instance(ctx, comic, "comic")
        await self.generic_remove(ctx, comic)

    @commands.group(aliases=["arcs", ], invoke_without_command=True)
    async def arc(self, ctx, arc=None, comic: typing.Optional[str]=None):
        converter = converters.ChildModelConverter(Arc, fk_dict)
        arc, comic = converter.args_convert(ctx, )
        # arc = await self.get_context_instance(ctx, arc, "arc")
        # comic = await self.get_context_instance(ctx, comic, "comic")
        #
        # await self.set_context(ctx, comic)

        self.get_current(ctx).arc_id = arc.id

        comic = await arc.comic
        embed = discord.Embed(title=f"Arc {arc.number}: {arc.name}", color=comic.embed_color)
        files = list()

        author = await comic.author
        author_kwargs = dict()
        if author.url is not None:
            author_kwargs["url"] = author.url

        author_path = await self.get_path(author)
        if os.path.isfile(author_path):
            file = discord.File(author_path, filename="avatar.png")
            author_kwargs["icon_url"] = "attachment://avatar.png"
            files.append(file)
        embed.set_author(name=f"Author: {author.name}", **author_kwargs)

        path = await self.get_path(arc, "cover")
        if os.path.isfile(path):
            file = discord.File(path, filename="cover.png")
            embed.set_image(url="attachment://cover.png")
            files.append(file)

        path = await self.get_path(comic, "thumbnail")
        if os.path.isfile(path):
            file = discord.File(path, filename="thumbnail.png")
            embed.set_thumbnail(url="attachment://thumbnail.png")
            files.append(file)

        embed.description = comic.description or ""
        # TODO parts

        formatter = "Part {0.number}{' - ' + 0.name if 0.name else ''}"
        # embed.add_field(name="Parts:",
        #                 value=await self.format_children(comic.arcs, formatter) or
        #                       "No arcs in this comic yet",
        #                 inline=True)

        await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none(), files=files)

    @arc.command(name="new", aliases=CommandsAliases.new_aliases)
    async def arc_new(self, ctx, name, *, params=""):
        context_params = self.get_context_params(ctx, "comic")

        await self.generic_add(ctx, Arc, name, params, context_params)

    @arc.command(name="edit", aliases=CommandsAliases.edit_aliases)
    async def arc_edit(self, ctx, arc: typing.Optional[ArcConverter], *, params=""):
        context_params = self.get_context_params(ctx, "comic")

        await self.generic_edit(ctx, arc, params, context_params)

    @arc.command(name="remove", aliases=CommandsAliases.remove_aliases)
    async def arc_remove(self, ctx, arc: typing.Optional[ArcConverter]):
        await self.generic_remove(ctx, comic)

    @commands.command()
    async def test(self, ctx, *, args):
        """Some test"""
        converter = converters.ChildModelConverter(Arc, fk_dict)
        await converter.convert(ctx, self.get_current(ctx), args)
