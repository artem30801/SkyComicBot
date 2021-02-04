from discord.ext import commands

import asyncio
import re


class ParamConverter(commands.Converter):
    def __init__(self, conversion_schema):
        self.conversion_schema = conversion_schema

    @staticmethod
    def parse_params(params: str) -> dict:
        param_list = re.findall(r'(?:[^\s,"]|"(?:\\.|[^"])*")+', params)
        param_dict = dict(param.split('=', 1) for param in param_list)
        param_dict = {key: value.replace('"', '') for key, value in param_dict.items()}
        return param_dict

    async def convert(self, ctx, argument):
        params_dict = self.parse_params(argument)

        converted = dict()
        for key, converter in self.conversion_schema.items():
            if key in params_dict:
                raw_value = params_dict[key]
                if isinstance(converter, commands.Converter):
                    converter = converter.convert
                if asyncio.iscoroutinefunction(converter):
                    value = await converter(ctx, raw_value)
                else:
                    value = converter(ctx, raw_value)
                converted[key] = value

        if not converted:
            raise commands.BadArgument("No valid arguments were included")

        return converted


class ColorValueConverter(commands.ColourConverter):
    async def convert(self, ctx, argument):
        return (await super().convert(ctx, argument)).value


def convert_to_bool(ctx, argument):
    lowered = argument.lower()
    if lowered in ('yes', 'y', 'true', 't', '1', 'enable', 'on'):
        return True
    elif lowered in ('no', 'n', 'false', 'f', '0', 'disable', 'off'):
        return False
    else:
        raise commands.BadBoolArgument(lowered)


def convert_to_int(ctx, argument):
    try:
        return int(argument)
    except ValueError:
        raise commands.BadArgument("This argument should be an integer")


async def ensure_unique(db_class, value, arg="name"):
    arg = {arg: value}
    if await db_class.exists(**arg):
        raise commands.BadArgument(f"Object '{value}' already exists")
    return value


def convert_ensure_unique(db_class, arg="name"):
    async def inner(ctx, value):
        return await ensure_unique(db_class, value, arg)
    return inner
