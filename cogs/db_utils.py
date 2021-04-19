import asyncio
import typing
from dataclasses import dataclass

import discord
from discord.ext import commands
from discord_slash.utils.manage_commands import create_option, create_choice
from tortoise import exceptions as t_exceptions
from tortoise import fields
from tortoise import queryset
from tortoise.functions import Max
from tortoise.transactions import atomic

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


async def get_max_number(model, number=None):
    number = number if number is not None else float('inf')
    number = max(number, 1)
    max_number = (await model.annotate(m=Max("number")).values_list("m", flat=True))[0]
    number = min(number, (max_number or 0) + 1)
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


def model_name(model):
    return model.__name__.lower()


def instance_name(instance):
    use_name = instance.processor.use_name
    return instance.name if use_name else f"number {instance.number}"


async def format_instance(instance, show_name=False):
    out_fields = {}
    for field_name, value in instance:
        if "id" in field_name:
            continue
        if field_name == "name" and not show_name:
            continue
        if isinstance(value, queryset.QuerySet):
            fk_instance = await value
            value = instance_name(fk_instance)
        if isinstance(value, fields.ReverseRelation):
            value = f"{len(await value)} {field_name}"

        out_fields[field_name] = str(value)
    return format_dict(out_fields)


def chunks_split(string_list, maxchars=2000, add_each=1):
    count = 0
    temp_slice = []
    for item in string_list:
        count += len(item) + add_each
        if count <= int(maxchars):
            temp_slice.append(item)
        else:
            yield temp_slice
            temp_slice = [item]
            count = len(item) + add_each
    yield temp_slice


def format_dict(d):
    return ", ".join([f"*{key}*={value}" for key, value in d.items()])


async def generate_choices(self):
    if not self.use_choices:
        return None
    return await ModelProcessor.generate_model_choices(self.model_type)


def is_updated(updated_fields, field):
    return updated_fields is None or field in updated_fields


@dataclass
class Field:
    description: str
    required: bool
    unique: list
    if_fk: bool
    use_choices: bool
    model_type: typing.Union[type, any]


@dataclass
class Options:
    add: list
    edit: list


class ModelProcessor:
    def __init__(self, model, fk_dict=None):
        self.model = model
        self.model.processor = self

        self.fk_dict = fk_dict or {}

        self.name = next((name for name, fk_model in self.fk_dict.items() if fk_model is model), None) \
                    or model_name(self.model)
        self.fields = dict(self.process_fields(self.model))
        # self.fk_fields = {name: field for name, field in self.fields if field.if_fk}

        self.use_name = "name" in self.fields.keys()
        self.use_choices = self.is_uses_choices(self.model)

        self.options = self.get_options()
        self._choices = {}

        choices_ids = {option["name"]: i for i, option in enumerate(self.options.add, start=1)}
        self._choices_ids = {name: i for name, i in choices_ids.items() if self.fields[name].use_choices}
        # self.choices_names = [name for field, name in self.fields if field.use_choices]

        if self.use_choices:
            self._choices_ids[self.name] = 0
        self.fields[self.name] = self

    @property
    def model_type(self):
        return self.model

    @property
    def if_fk(self):
        return True

    @property
    def choices(self):
        return self._choices[self._choices_ids[self.name]]

    @classmethod
    def process_fields(cls, model):
        schema = model.describe(False)

        for field in schema["data_fields"]:
            if field["name"].endswith("_id"):  # skip id fields that duplicate fk fields
                continue
            yield cls.process_field(field, is_fk=False)

        for field in schema["fk_fields"]:
            yield cls.process_field(field, is_fk=True)

    @classmethod
    def process_field(cls, field, is_fk=False):
        name = field["name"]
        if is_fk:
            name += "_name"

        required = field["default"] is None and not field["nullable"]
        python_type = field["python_type"]
        unique = field["unique"]

        use_choices = cls.is_uses_choices(python_type) if is_fk else False

        processed_field = Field(description=field["description"] or name,
                                required=required,
                                if_fk=is_fk,
                                use_choices=use_choices,
                                model_type=python_type,
                                unique=unique,
                                )

        return name, processed_field

    @staticmethod
    def is_uses_choices(model):
        try:
            use_choices = model.Meta.__dict__["use_choices"]
        except (AttributeError, KeyError):
            use_choices = False
        return use_choices

    @staticmethod
    async def generate_model_choices(model):
        return [create_choice(name=instance_name(instance), value=instance.id)
                async for instance in model.all()][:25]

    def generate_options(self):
        self_option = create_option(name=self.name,
                                    description=f"{self.name} to edit",
                                    option_type=int if self.use_choices else str,
                                    required=True,
                                    choices=None)
        yield self_option

        for name, field in self.fields.items():
            if field.if_fk:
                option_type = int if field.use_choices else str
            else:
                option_type = field.model_type

            if option_type is discord.Colour:
                option_type = str

            option = create_option(name=name,
                                   description=field.description,
                                   option_type=option_type,
                                   required=field.required,
                                   choices=None)
            yield option

    def get_options(self):
        raw_options = sorted(list(self.generate_options()), key=lambda x: x['required'], reverse=True)
        add_options = raw_options[1:]
        edit_options = [raw_options[0]] + list(self._options_edit(add_options))

        return Options(add_options, edit_options)

    @staticmethod
    def _options_edit(options):
        for option in options:
            option = option.copy()
            option["required"] = False
            yield option

    def _filter_choices(self, choices=None):
        if not choices:
            return self._choices_ids.items()
        else:
            return ((name, i) for name, i in self._choices_ids.items() if name in choices)

    async def update_choices(self, choices=None):
        updated = list(self._filter_choices(choices))
        for name, i in updated:
            self._choices[i] = await generate_choices(self.fields[name])

        self.set_choices(self.options.add, updated, False)
        self.set_choices(self.options.edit, updated, True)

    def set_choices(self, option, updated, edit=False):
        for name, i in updated:
            option_i = i - (not edit)
            if option_i >= 0:  # to exclude self-choice on add func
                option[option_i]["choices"] = self._choices[i]

    def get_parent(self, fk_dict):
        pass

    async def __call__(self, params):
        return await self.process(params)

    async def process(self, params):
        fk_params = {name: field for name, field in self.fields.items()
                     if field.if_fk and name in params}

        for fk_param, field in fk_params.items():
            fk_model = field.model_type
            value = params.pop(fk_param)
            if self.fields.get(fk_param, self).use_choices:  # use self as it could be only field not in fields
                instance = await fk_model.get(id=value)
            else:
                instance = await ModelConverter(fk_model).convert(None, value)
            fk_param = fk_param.removesuffix("_name")
            params[fk_param] = instance

        instance = params.get(self.name, None)
        # query = type(instance)
        # query = self.model
        # fk_fields = instance.descriptor.fk_names
        # if fk_fields and fk_fields[0] in fk_dict:
        #     parent_name = fk_fields[0]
        #     query = query.filter(**{parent_name: await getattr(instance, parent_name)})
        #
        #     await reshuffle(query, number, instance)

        if "name" in params and self.fields["name"].unique:  # todo unique together
            name = params["name"]
            if await self.model.exists(name=name):
                raise commands.BadArgument(f"{self.name} **{name}** already exists!")

        if "number" in params:
            params["number"] = await get_max_number(self.model, params["number"])
            await reshuffle(self.model, params["number"], instance)

        return params


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
