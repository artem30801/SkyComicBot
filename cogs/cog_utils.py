import logging

import discord
from discord.ext import commands

import os

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


def get_bot_role(self, guild):
    return discord.utils.get(guild.roles, name="Bot manager")

# async def has_bot_perms(ctx):
#     return await member_bot_perms(ctx.message.author)
#
# def is_guild_owner():
#     def predicate(ctx):
#         return ctx.guild is not None and ctx.guild.owner_id == ctx.author.id
#     return commands.check(predicate)
# #
# async def member_bot_perms(self, member: discord.Member):
#     if member == member.guild.owner or member.id in ctx.bot.owner_ids:
#         return True
#     bot_role = self.get_bot_role(member.guild)
#     if bot_role is not None and member.top_role >= bot_role:
#         return True
#     return False


if __name__ == "__main__":
    l = ["help", "me", "please", "zalside"]
    print(fuzzy_search("pls", l))
    print(fuzzy_search("zlolsider", l))

