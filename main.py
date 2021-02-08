import configparser
import logging

import asyncio
import nest_asyncio

from tortoise import Tortoise, fields, run_async
from tortoise.models import Model

import discord
from discord.ext import commands

from cogs.errors import Errors
from cogs.greetings import Greetings
from cogs.reactions import Reactions
from cogs.roles import Roles
from cogs.emotes import Emotes
from cogs.converters import Conversions

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [%(levelname)-7.7s]-[%(name)-15.15s]: %(message)s")
nest_asyncio.apply()

bot = commands.Bot(command_prefix=commands.when_mentioned_or("!", "$"), case_insensitive=True,
                   intents=discord.Intents.all())
# https://discordapp.com/oauth2/authorize?&client_id=804306819660644372&scope=bot&permissions=1446509632


async def main():
    config = configparser.ConfigParser()
    config.read("config.ini")

    bot.add_cog(Errors(bot))
    bot.add_cog(Greetings(bot))
    bot.add_cog(Reactions(bot))
    bot.add_cog(Roles(bot))
    bot.add_cog(Emotes(bot))
    bot.add_cog(Conversions(bot, config))

    try:
        #db_url="sqlite://skybot.db"
        await Tortoise.init(db_url=config["AUTH"]["db_url"], modules={"models": ["cogs.roles", "cogs.greetings", "cogs.converters"]})
        await Tortoise.generate_schemas()
        await bot.start(config["AUTH"]["discord_token"])  # 1446509632
    finally:
        await bot.logout()
        await Tortoise.close_connections()

if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
