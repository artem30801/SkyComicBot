import configparser
import logging

import asyncio
import nest_asyncio

from tortoise import Tortoise, run_async

import discord
from discord.ext import commands
from discord_slash import SlashCommand

# from cogs.errors import Errors
# from cogs.roles import Roles
# from cogs.comics import Comics
# from cogs.converters import Conversions

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [%(levelname)-7.7s]-[%(name)-15.15s]: %(message)s")
nest_asyncio.apply()

bot = commands.Bot(command_prefix=commands.when_mentioned_or("-", "!"), case_insensitive=True,
                   intents=discord.Intents.all(), owner_ids=[246333265495982080, 248892679813857280])
slash = SlashCommand(bot, override_type=True)

# https://discordapp.com/oauth2/authorize?&client_id=804306819660644372&scope=bot&permissions=1446509632
# https://discordapp.com/oauth2/authorize?&client_id=804306819660644372&scope=applications.commands%20bot&permissions=1446509632
# scope=applications.commands%20bot


async def main():
    config = configparser.ConfigParser()
    config.read("config.ini")

    bot.load_extension("cogs.greetings")
    bot.load_extension("cogs.permissions")
    bot.load_extension("cogs.errors")
    bot.load_extension("cogs.reactions")
    bot.load_extension("cogs.emotes")

    # bot.add_cog(Errors(bot))
    # bot.add_cog(Roles(bot))
    # bot.add_cog(Conversions(bot, config))
    # bot.add_cog(Comics(bot))

    try:
        #db_url="sqlite://skybot.db"
        await Tortoise.init(db_url=config["AUTH"]["db_url"],
                            modules={"models": ["cogs.greetings", "cogs.converters", "cogs.permissions",
                                                "cogs.roles", #"cogs.comics",
                                                ]
                                     })
        await Tortoise.generate_schemas()
        await bot.start(config["AUTH"]["discord_token"])  # 1446509632
    finally:
        await bot.logout()
        await Tortoise.close_connections()

if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
