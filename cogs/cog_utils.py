import logging

import discord
from discord.ext import commands

from tortoise.functions import Max

import os
import re
import asyncio

from fuzzywuzzy import process
from fuzzywuzzy import fuzz


def fuzzy_search(query, choices):
    result = process.extractOne(query, choices, score_cutoff=50, scorer=fuzz.token_set_ratio)
    logging.debug(f"Fuzzy search for {query} in {choices} resulted as {result}")
    return None if result is None else result[0]


def abs_join(*paths):
    return os.path.abspath(os.path.join(*paths))


async def send_file(channel, path, filename=None):
    file = discord.File(path, filename=filename or os.path.split(path)[1])
    await channel.send(" ", file=file)


def next_number(cls_name, field="priority"):
    def inner():
        loop = asyncio.get_event_loop()
        cls = globals()[cls_name]
        max_number = loop.run_until_complete(cls.annotate(m=Max(field)).values_list("m", flat=True))[0]
        return max_number + 1 if max_number is not None else 0
    return inner


def parse_params(params: str)-> dict:
    param_list = re.findall(r'(?:[^\s,"]|"(?:\\.|[^"])*")+', params)
    param_dict = dict(param.split('=', 1) for param in param_list)
    param_dict = {key: value.replace('"', '') for key, value in param_dict.items()}
    return param_dict


def convert_to_bool(argument):
    lowered = argument.lower()
    if lowered in ('yes', 'y', 'true', 't', '1', 'enable', 'on'):
        return True
    elif lowered in ('no', 'n', 'false', 'f', '0', 'disable', 'off'):
        return False
    else:
        raise commands.BadBoolArgument(lowered)


def get_bot_role(self, guild):
    return discord.utils.get(guild.roles, name="Bot manager")


def is_guild_owner():
    def predicate(ctx):
        return ctx.guild is not None and ctx.guild.owner_id == ctx.author.id
    return commands.check(predicate)


def has_server_perms():  # perms to manage other people on the server
    return commands.check_any(is_guild_owner(), commands.is_owner(), commands.has_role("Bot manager"))


def has_bot_perms():  # perms to manage bot internal DB
    return commands.check_any(commands.is_owner())


if __name__ == "__main__":
    l = ["help", "me", "please", "zalside"]
    print(fuzzy_search("pls", l))
    print(fuzzy_search("zlolsider", l))

