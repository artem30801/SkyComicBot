import logging

import discord
from discord.ext import commands

from tortoise.functions import Max

import os
import asyncio
import inspect

from fuzzywuzzy import process
from fuzzywuzzy import fuzz


embed_color = 0x72a3f2
bot_manager_role = "Bot manager"
stream_crew_role = "livestream crew"


class StartupCog(commands.Cog):
    def __init__(self):
        self._first_ready = True

    @commands.Cog.listener()
    async def on_ready(self):
        if self._first_ready:
            self._first_ready = False
            await self.on_startup()

    async def on_startup(self):
        pass


def convert_args(command, args, kwargs):
    options = command.options
    arg_to_kwarg = {k["name"]: v for k, v in zip(options, args)}
    arg_to_kwarg.update(kwargs)
    print(command, args, kwargs, arg_to_kwarg)
    return arg_to_kwarg


def format_params(params):
    return f"*{', '.join([f'{key}={value}' for key, value in params.items()])}*"


def fuzzy_search(query, choices, score_cutoff=50):
    result = process.extractOne(query, choices, score_cutoff=score_cutoff, scorer=fuzz.token_set_ratio)
    logging.debug(f"Fuzzy search for {query} in {choices} resulted as {result}")
    return None if result is None else result[0]


def abs_join(*paths):
    return os.path.abspath(os.path.join(*paths))


def ensure_dir(directory):
    if not os.path.exists(directory):
        os.makedirs(directory)


async def send_file(channel, path, filename=None):
    file = discord.File(path, filename=filename or os.path.split(path)[1])
    await channel.send(" ", file=file)


def url_hostname(url):
    return url.split("//")[-1].split("/")[0].split('?')[0]


def can_bot_respond(bot: discord.ext.commands.Bot, channel: discord.TextChannel):
    """Checks, can a bot send messages to this channel"""
    if bot is None or channel is None:
        return False

    bot_as_member = channel.guild.get_member(bot.user.id)
    permissions = channel.permissions_for(bot_as_member)
    return permissions.send_messages
