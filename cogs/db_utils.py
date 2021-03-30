from tortoise.transactions import atomic
from tortoise import fields

import discord
from discord.ext import commands

import typing
from dataclasses import dataclass


@atomic()
async def reshuffle(model, instance, number):
    number = max(number, 1)
    changing_objs = await model.filter(number__gte=number).exclude(id=instance.id)
    for db_obj in changing_objs:
        number += 1
        db_obj.number = number
        await db_obj.save()


@dataclass
class FileStorageField:
    # required: bool = False
    nullable: bool = True


class File:
    pass


class ColorField(fields.IntField):
    field_type = discord.Colour

    def to_db_value(self, value: discord.Color, instance) -> int:
        return value.value

    def to_python_value(self, value: typing.Union[int, discord.Color]) -> discord.Color:
        if isinstance(value, discord.Color) or value is None:
            return value
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
