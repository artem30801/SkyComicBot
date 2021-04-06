import asyncio
from tortoise import fields
from tortoise import exceptions as t_exceptions
from tortoise.functions import Max
from tortoise.transactions import atomic

import discord
from discord.ext import commands
from discord_slash.utils.manage_commands import create_option, create_choice

import typing
from dataclasses import dataclass

import cogs.cog_utils as utils


class NextNumber:
    def __init__(self, field="number", model=None):
        self.field = field
        self.model = model

    def set_model(self, model):
        self.model = model

    def __call__(self, *args, **kwargs):
        loop = asyncio.get_event_loop()
        max_number = loop.run_until_complete(self.model.annotate(m=Max(self.field)).values_list("m", flat=True))[0]
        return (max_number or 0) + 1


async def get_max_number(model, number):
    number = number if number is not None else float('inf')
    number = max(number, 1)
    max_number = (await model.annotate(m=Max("number")).values_list("m", flat=True))[0]
    number = min(number, (max_number or 0)+1)
    return number


@atomic()
async def reshuffle(model, number, exclude_instance=None):
    query = model.filter(number__gte=number)
    if exclude_instance is not None:
        query = query.exclude(id=exclude_instance.id)

    async for instance in query:
        number += 1
        instance.number = number
        await instance.save()


async def process(model, params, fk_dict=None, instance=None):
    fk_dict = fk_dict or {}

    if "name" in params:
        db_schema = model.describe(False)["data_fields"]
        field = next(filter(lambda x: x["name"] == "name", db_schema))
        if field["unique"]:
            name = params["name"]
            if await model.exists(name=name):
                raise commands.BadArgument(f"{model.__name__} **{name}** already exists!")

    if "number" in params:
        params["number"] = await get_max_number(model, params["number"])
        await reshuffle(model, params["number"], instance)

    fk_params = {k: v for k, v in fk_dict.items() if k in params}
    for fk_param, fk_model in fk_params.items():
        choices = await generate_db_choices(fk_model)
        if choices is not None:
            instance = await fk_model.get(id=params[fk_param])
        else:
            instance = await ModelConverter(fk_model).convert(None, params[fk_param])
        params[fk_param] = instance

    return params


async def generate_db_choices(fk_model):
    try:
        use_choices = fk_model.Meta.__dict__["use_choices"]
    except (AttributeError, KeyError):
        use_choices = False
    if use_choices:
        choices = [create_choice(name=instance.name, value=instance.id)
                   for instance in await fk_model.all()]
        return choices
    return None


async def generate_db_options(model, edit=None):
    db_schema = model.describe(False)
    data_fields = db_schema["data_fields"]
    fk_fields = db_schema["fk_fields"]
    fk_names = [field["name"] for field in fk_fields]
    options = []
    for field in data_fields + fk_fields:
        name = field["name"]
        if name.endswith("_id"):
            continue

        is_fk = name in fk_names
        python_type = int if is_fk else field["python_type"]
        if python_type is discord.Colour:
            python_type = str

        required = field["default"] is None and not field["nullable"] and not edit
        choices = await generate_db_choices(field["python_type"]) if is_fk else None

        option = create_option(name=name,
                               description=field["description"] or name,
                               option_type=python_type,
                               required=required,
                               choices=choices)

        options.append(option)

    if edit is not None:
        choices = await generate_db_choices(model)

        option = create_option(name=edit,
                               description=f"{model.__name__} to edit",
                               option_type=int if choices else str,
                               required=True,
                               choices=choices)
        options.insert(0, option)
    options.sort(key=lambda x: x['required'], reverse=True)
    return options


def model_name(model):
    return model.__name__.lower()


class ModelConverter(commands.Converter):
    def __init__(self, model, query=None):
        self.model = model
        self.query = query or self.model

        db_schema = model.describe(False)["data_fields"]
        is_name = list((filter(lambda x: x["name"] == "name", db_schema)))
        self.use_name = bool(is_name)

    async def convert_name(self, argument):
        names = await self.query.all().values_list("name", flat=True)
        names = [name for name in names if name is not None]
        if not names:
            raise commands.BadArgument(f"No names for {model_name(self.model)}s "
                                       f"are currently available")
        name = utils.fuzzy_search(argument, names)
        if name is None:
            raise commands.BadArgument(f"Can't find {model_name(self.model)} "
                                       f"with name {argument}")

        return await self.query.get(name=name)

    async def convert_number(self, value: int):
        try:
            return await self.query.get(number=value)
        except t_exceptions.DoesNotExist:
            raise commands.BadArgument(f"Can't find {model_name(self.model)} "
                                       f"with number {value}")

    async def convert(self, ctx, argument):
        try:
            value = int(argument)
        except ValueError:
            if self.use_name:
                return await self.convert_name(argument)
        else:
            return await self.convert_number(value)
        raise commands.BadArgument(f"Cannot convert {argument} to {model_name(self.model)}")


@dataclass
class FileStorageField:
    # required: bool = False
    nullable: bool = True


class File:
    pass


def convert_color(value: str):
    loop = asyncio.get_event_loop()
    color = loop.run_until_complete(commands.ColorConverter().convert(None, value))
    return color


class ColorField(fields.IntField):
    field_type = discord.Colour

    def to_db_value(self, value: discord.Color, instance) -> int:
        return value.value

    def to_python_value(self, value) -> discord.Color:
        if value is None:
            return None
        if isinstance(value, str):
            return convert_color(value)
        return discord.Colour(value)


class UserField(fields.BigIntField):
    field_type = discord.User
    bot = None

    def to_db_value(self, value: typing.Union[int, discord.User], instance) -> int:
        if isinstance(value, int) or value is None:
            return value
        return value.id

    def to_python_value(self, value: int) -> discord.User:
        if isinstance(value, discord.User):
            return value

        return self.bot.get_user(value)
