import logging

import discord
from discord.ext import commands

from tortoise.functions import Max

import os
import asyncio
import inspect

from fuzzywuzzy import process
from fuzzywuzzy import fuzz


class CommandsAliases:
    list_aliases = ["all", "available", "view", ]
    new_aliases = ["add", "create", "+"]
    remove_aliases = ["clear", "delete", "yeet", "-"]
    edit_aliases = ["update", "change", "="]


embed_color = 0x72a3f2


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


def next_number(cls_name, field="number"):
    def inner():
        loop = asyncio.get_event_loop()
        cls = inspect.stack()[3][0].f_globals[cls_name]
        max_number = loop.run_until_complete(cls.annotate(m=Max(field)).values_list("m", flat=True))[0]
        return max_number + 1 if max_number is not None else 0
    return inner


def can_bot_respond(bot: discord.ext.commands.Bot, channel: discord.TextChannel):
    """Checks, can a bot send messages to this channel"""
    if bot is None or channel is None:
        return False

    bot_as_member = channel.guild.get_member(bot.user.id)
    permissions = channel.permissions_for(bot_as_member)
    return permissions.send_messages


# if __name__ == "__main__":
#     pass
    # r = fuzzy_search(
    #     "https://discordpy.readthedocs.io/en/attachment/faq.html#how-do-i-use-a-local-image-file-for-an-embed-image",
    #     ["attachment", "file_attached"], score_cutoff=80)
    # print(r)
    # l = ["help", "me", "please", "zalside"]
    # print(fuzzy_search("pls", l))
    # print(fuzzy_search("zlolsider", l))

