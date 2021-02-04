import discord
from discord.ext import commands

from tortoise.models import Model
from tortoise.functions import Max
from tortoise import fields

import re
import asyncio
import json


class Comic(Model):
    id = fields.IntField(pk=True)
    title = fields.TextField()
    description = fields.TextField()
    url = fields.TextField()
    author = fields.ForeignKeyField("cogs.comics.Author", related_name="comics")
    embed_color = fields.IntField()
    cover_path = fields.TextField(null=True)
    arcs: fields.ReverseRelation["Arc"]

    #language = fields.TextField(default="english")


class Author(Model):
    id = fields.IntField(pk=True)
    name = fields.IntField(pk=True)
    url = fields.TextField()
    avatar_path = fields.TextField()
    embed_color = fields.IntField()
    comics: fields.ReverseRelation["Comic"]


class Arc:
    id = fields.IntField(pk=True)
    number = fields.IntField()
    title = fields.IntField(pk=True)
    comic = fields.ForeignKeyField("cogs.comics.Comic", related_name="arcs")
    parts: fields.ReverseRelation["Part"]

    class Meta:
        ordering = ["number"]


class Part(Model):
    id = fields.IntField(pk=True)
    number = fields.IntField()
    title = fields.TextField(pk=True)
    text = fields.TextField(pk=True, null=True)
    arc = fields.ForeignKeyField("cogs.comics.Arc", related_name="parts")
    pages: fields.ReverseRelation["Page"]

    class Meta:
        ordering = ["number"]


class Page(Model):
    id = fields.IntField(pk=True)
    number = fields.IntField()
    page = fields.ForeignKeyField("cogs.comics.Part", related_name="pages")
    path = fields.TextField()

    class Meta:
        ordering = ["number"]

