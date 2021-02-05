import logging

import discord
from discord.ext import commands

from tortoise.functions import Max

import os
import asyncio
import inspect

from fuzzywuzzy import process
from fuzzywuzzy import fuzz


def fuzzy_search(query, choices, score_cutoff=50):
    result = process.extractOne(query, choices, score_cutoff=score_cutoff, scorer=fuzz.token_set_ratio)
    logging.debug(f"Fuzzy search for {query} in {choices} resulted as {result}")
    return None if result is None else result[0]


def abs_join(*paths):
    return os.path.abspath(os.path.join(*paths))


async def send_file(channel, path, filename=None):
    file = discord.File(path, filename=filename or os.path.split(path)[1])
    await channel.send(" ", file=file)


def next_number(cls_name, field="number"):
    def inner():
        loop = asyncio.get_event_loop()
        cls = inspect.stack()[3][0].f_globals[cls_name]
        max_number = loop.run_until_complete(cls.annotate(m=Max(field)).values_list("m", flat=True))[0]
        return max_number + 1 if max_number is not None else 0
    return inner


if __name__ == "__main__":
    l = ["help", "me", "please", "zalside"]
    print(fuzzy_search("pls", l))
    print(fuzzy_search("zlolsider", l))

