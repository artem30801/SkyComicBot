import discord
from discord.ext import commands

from tortoise import Model
from tortoise.exceptions import DoesNotExist

import asyncio
import aiohttp

import json
import shlex
from dataclasses import dataclass, asdict

from cogs import db_utils
from cogs.cog_utils import fuzzy_search
# from cogs import comics


@dataclass
class FieldSchema:
    python_type: type
    unique: bool = False
    nullable: bool = False
    required: bool = False
    fk: bool = False
    converter: tuple = ()


class ModelParamConverter(ParamConverter):
    def __init__(self, model, fk_dict, check_required=False, add_file=False):
        self.model = model
        self.fk_dict = fk_dict

        self.check_required = check_required
        self.add_file = add_file

        self.file_fields = dict()
        super().__init__(self.get_conversion_schema())
        self.setup_converters()

    def get_conversion_schema(self):
        db_schema = self.model.describe(False)
        fields = db_schema["data_fields"]
        fields.extend(db_schema["fk_fields"])
        fk_fields = [field["name"] for field in db_schema["fk_fields"]]
        schema = dict()
        for field in fields:
            name = field["name"]
            if name.endswith("_id"):
                continue
            is_fk = name in fk_fields
            python_type = self.fk_dict[name] if is_fk else field["python_type"]
            nullable = field["nullable"]
            required = field["default"] is None and not nullable
            schema_field = FieldSchema(python_type, field["unique"], nullable, required, is_fk)
            schema[name] = schema_field

        file_fields = self.get_file_fields(self.model)
        for name, field in file_fields.items():
            schema_field = FieldSchema(db_utils.File, False, field.nullable, not field.nullable, False)
            schema[name] = schema_field
            self.file_fields[name] = schema_field

        # schema = {"path" if key.endswith("path") else key: value for key, value in schema.items()}
        return schema

    @classmethod
    def get_file_fields(cls, model):
        if "FileStorage" in model.__dict__:
            return {key: value for key, value in model.FileStorage.__dict__.items()
                    if not key.startswith("_") and isinstance(value, db_utils.FileStorageField)}
        else:
            return dict()

    def get_converter(self, field_name: str, field: FieldSchema):
        if field.converter:
            return field.converter

        if field.nullable:
            yield NoneConverter()

        if "url" in field_name:
            yield UrlConverter()
        elif field.python_type is db_utils.File:  # field_name.endswith("file_attached"):
            yield FileConverter()
        elif field.python_type is comics.Author:
            yield comics.AuthorConverter()
        elif field.python_type is discord.Colour:
            yield commands.ColourConverter()
        elif field.python_type is discord.User:
            yield commands.UserConverter()
        elif field.python_type is str:
            yield StrConverter()
        elif field.python_type is int:
            yield IntConverter()
        elif issubclass(field.python_type, Model):
            yield ModelConverter(self.fk_dict[field_name])
        else:
            raise ValueError(f"No converter found for {field_name}!")

        if field.unique:
            yield EnsureUniqueConverter(self.model, field_name)

    def setup_converters(self):
        for name, field in self.conversion_schema.items():
            converter = tuple(self.get_converter(name, field))
            field.converter = converter

        print(self.conversion_schema)

    @staticmethod
    def is_field(field: FieldSchema, field_type):
        # file_fields = {key: value for key, value in self.conversion_schema.items()
        #                if self.is_file_field(key, value)}

        for converter in field.converter:
            if isinstance(converter, field_type):
                return True
        return False

    def get_help(self):
        help_dict = dict()
        for key, field in self.conversion_schema.items():
            help_dict[key] = self._get_help_field(key, field)

        pre_help = "Specify params as one or more pairs of <field>=<value>\n" \
                   "Use quotes for space-separated names or strings\n" \
                   'Example of params: name="New name" color=#00FF00 group="Group name" number=3 archived=False'
        fields_help = "\n".join([f" Field '{key}': {value}" for key, value in help_dict.items()])
        post_help = f"When adding new {model_name(self.model)} you must include all required arguments \n" \
                    f"You can omit any not-required arguments. You can omit required arguments during editing"
        return "\n".join((pre_help, fields_help, post_help))

    def _get_help_field(self, key, field):
        extras = []
        field_type = field.model.__name__
        if "url" in key:  # self.is_field(field, UrlConverter) and not self.is_field(field, FileConverter):
            field_type = "Url"
            extras.append("must be a valid link (url address)")

        if field.required:
            extras.append("requited field")
        if field.unique:
            extras.append(f"must be unique (no repeats of this {key} "
                          f"among other {model_name(self.model)}s)")
        if key == "name":  # shortcut
            meta = getattr(self.model, "Meta", None)
            if meta is not None:
                unique = getattr(meta, "unique_together", None)
                if unique is not None:
                    extras.append(f"name of each {model_name(self.model)} must be unique "
                                  f"in its {unique[0][1]}")

        if field.nullable:
            extras.append("can be set to 'None'")
        if key in self.file_fields:
            extras.append(f"specify a link or attach a file "
                          f"(optionally specify argument as '{key}=file' "
                          f"when attaching a file to the command)")

        extra_text = " - " + ' | '.join(extras) if extras else ""
        return f"{field_type} {extra_text}"

    def _is_file_upload(self, key, field):
        # if len(self.file_fields) > 1:
        #     return False
        return key in self.file_fields

    async def convert(self, ctx, argument, parsed=None):
        parsed = parsed or dict()
        converted = await super().convert(ctx, argument, parsed)

        if self.check_required:
            for key, field in self.conversion_schema.items():
                if field.required and key not in converted:
                    raise commands.BadArgument(f"Missing required field {key}")

        return converted

    async def convert_value(self, ctx, raw_value, converter):
        value = None
        for single_converter in converter.converter:
            value = await super().convert_value(ctx, raw_value, single_converter)
            if value is None and raw_value is not None:
                break

        return value


class NoneConverter(commands.Converter):
    none = ("none", "null")

    async def convert(self, ctx, argument):
        if argument is not None and not isinstance(argument, str):
            return argument
        if argument is None or fuzzy_search(argument, self.none, score_cutoff=75) is not None:
            return None
        return argument


class StrConverter(commands.Converter):
    async def convert(self, ctx, argument):
        return str(argument)


class BoolConverter(commands.Converter):
    true = ('yes', 'y', 'true', 't', '1', 'enable', 'on')
    false = ('no', 'n', 'false', 'f', '0', 'disable', 'off')

    async def convert(self, ctx, argument):
        lowered = argument.lower()
        if lowered in self.true:
            return True
        elif lowered in self.false:
            return False
        else:
            raise commands.BadBoolArgument(lowered)


class IntConverter(commands.Converter):
    async def convert(self, ctx, argument):
        try:
            return int(argument)
        except ValueError:
            raise commands.BadArgument("This argument should be an integer")


class EnsureUniqueConverter(commands.Converter):
    def __init__(self, model, field="name"):
        self.model = model
        self.field = field

    async def convert(self, ctx, argument):
        if await self.model.exists(**{self.field: argument}):
            raise commands.BadArgument(f"*{self.model.__name__}* **{argument}'** already exists")
        return argument


class UrlConverter(commands.Converter):
    async def convert(self, ctx, argument):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(argument) as response:
                    if response.status != 200:
                        raise commands.BadArgument("Invalid URL")
                    else:
                        return argument
        except aiohttp.ClientError:
            raise commands.BadArgument("Invalid URL, can't reach")


class FileConverter(UrlConverter):
    file_attached = ("file", "files", "attachment", "attached")

    def __init__(self, single=True):
        self.single = single

    @classmethod
    def is_attachment_arg(cls, argument):
        return argument is None or argument.lower() in cls.file_attached

    async def convert(self, ctx, argument):
        # if isinstance(argument, list):
        #     files = [argument[i] for i in range(len(argument))] # argument.pop(i)
        if self.is_attachment_arg(argument):
            files = [attachment.proxy_url for attachment in ctx.message.attachments]
        else:
            if argument.startswith("[") and argument.endswith("]"):
                try:
                    files = json.loads(argument)
                except json.JSONDecodeError:
                    raise commands.BadArgument("Incorrect JSON list format")
            else:
                files = [argument]

            for file in files:
                await super().convert(ctx, file)

        if self.single and len(files) > 1:
            raise commands.BadArgument("Too many files included! Only one file supported for this field")

        if not files:
            if argument is not None:
                raise commands.BadArgument("No links or attachments were supplied")
            return None

        return files


class ModelConverter(commands.Converter):
    def __init__(self, model, query=None):
        self.model = model
        self.query = query or self.model
        self.use_name = "name" in model.__dict__

    async def convert_name(self, argument):
        names = await self.query.all().values_list("name", flat=True)
        names = [name for name in names if name is not None]
        if not names:
            raise commands.BadArgument(f"No names for {model_name(self.model)}s "
                                       f"are currently available")
        name = fuzzy_search(argument, names)
        if name is None:
            raise commands.BadArgument(f"Can't find {model_name(self.model)} "
                                       f"with name {argument}")

        return await self.query.get(name=name)

    async def convert_number(self, value: int):
        try:
            return await self.query.get(number=value)
        except DoesNotExist:
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


class ChildModelConverter:
    def __init__(self, model, fk_dict):
        self.model = model
        self.fk_dict = fk_dict
        # self.context =

    def expected_keys(self):
        name = model_name(self.model)
        for key in self.fk_dict.keys():
            yield key
            if key == name:
                break

    async def args_convert(self, ctx, context_dict, *args):
        context_dict = asdict(context_dict)
        expected_keys = list(self.expected_keys())

        if len(expected_keys) > 1:  # If not an author
            expected_keys = expected_keys[1:]  # remove author from list

        args_len = len(args)
        min_len = sum(value is None for key, value in context_dict.items() if key in expected_keys)
        if args_len < min_len:
            raise commands.BadArgument("Not enough arguments, can not infer them from context")

        max_len = len(expected_keys)
        if args_len > max_len:
            raise commands.BadArgument("Too many arguments, idk what you mean >_<")

        arg_dict = dict()
        for key, arg in zip(reversed(expected_keys), reversed(args)):
            if arg is not None:
                arg_dict[key] = arg

        instance_dict = dict()
        previous_key = None
        previous_instance = None
        for key in expected_keys:
            model = self.fk_dict[key]
            try:
                arg = arg_dict[key]
            except KeyError:
                db_id = context_dict[key]
                instance = await model.get(id=db_id)
            else:
                if previous_key is None:
                    query = model
                else:
                    query = model.filter(**{previous_key: previous_instance})
                converter = ModelConverter(model, query)
                instance = await converter.convert(ctx, arg)
            instance_dict[key] = instance

        print(instance_dict, "instance dict")
        return instance_dict

    async def convert(self, ctx, context_dict, argument):
        args = shlex.split(argument)
        return await self.args_convert(ctx, context_dict, *args)

        # if isinstance(self.model, comics.Author) or isinstance(self.model, comics.Comic):
        #     pass


def model_name(instance):
    return type(instance).__name__.lower()


def instance_name(instance, converter):
    use_name = "name" in converter.conversion_schema
    return instance.name if use_name else f"number {instance.number}"


def format_dict(d):
    return ", ".join([f"*{key}*={value}" for key, value in d.items()])


def format_converted(converted):
    return format_dict(converted)


async def format_fields(instance, converter, fk_keys):
    d = dict()
    for key, field in converter.conversion_schema.items():
        if key in converter.file_fields:
            continue
        value = getattr(instance, key)  # TODO FK
        if field.fk:
            print(value)
            fk_instance = await value
            fk_converter = ModelParamConverter(type(fk_instance), fk_keys)
            value = instance_name(fk_instance, fk_converter)
            print(value)

        d[key] = value
    return format_dict(d)
