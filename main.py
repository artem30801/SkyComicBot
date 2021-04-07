
import os
import json
import logging
from logging.handlers import SocketHandler
import configparser
from datetime import datetime

import asyncio
import nest_asyncio

from tortoise import Tortoise, run_async

import discord
from discord.ext import commands
from discord_slash import SlashCommand

import cogs.cog_utils as utils

# from cogs.comics import Comics
# from cogs.converters import Conversions

nest_asyncio.apply()


# https://discordapp.com/oauth2/authorize?&client_id=804306819660644372&scope=applications.commands%20bot&permissions=1446509632
# scope=applications.commands%20bot


async def main():
    bot = commands.Bot(command_prefix=commands.when_mentioned_or("-", "!"), case_insensitive=True,
                       intents=discord.Intents.all(), help_command=None)
    SlashCommand(bot, override_type=True)

    current_dir = os.path.dirname(os.path.realpath(__file__))

    with open(utils.abs_join(current_dir, "config.json"), "r") as f:
        config = json.load(f)
    bot.config = config
    bot.owner_ids = set(config["discord"]["owner_ids"])

    now = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    utils.ensure_dir(utils.abs_join(current_dir, "logs"))

    logging.setLoggerClass(utils.DBLogger)
    log_handlers = [
        logging.FileHandler(utils.abs_join(current_dir, "logs", f"{now}.log")),
        logging.StreamHandler()
    ]
    if "logging" in bot.config and "socket_handlers" in bot.config["logging"]:
        for handler in bot.config["logging"]["socket_handlers"]:
            socket_handler = SocketHandler(handler["host"], handler["port"])
            socket_handler.closeOnError = True
            log_handlers.append(socket_handler)

    # noinspection PyArgumentList
    logging.basicConfig(
        level=logging.DEBUG,
        format="[%(asctime)s] [%(levelname)-9.9s]-[%(name)-15.15s]: %(message)s",
        handlers=log_handlers
    )
    logging.getLogger("tortoise").setLevel(logging.INFO)
    logging.getLogger("db_client").setLevel(logging.INFO)
    logging.getLogger("aiomysql").setLevel(logging.INFO)
    logging.getLogger("discord.client").setLevel(logging.CRITICAL)
    logging.getLogger("discord.gateway").setLevel(logging.ERROR)
    logging.getLogger("discord.http").setLevel(logging.ERROR)

    bot.load_extension("cogs.greetings")
    bot.load_extension("cogs.permissions")
    bot.load_extension("cogs.errors")
    bot.load_extension("cogs.reactions")
    bot.load_extension("cogs.emotes")
    bot.load_extension("cogs.roles")

    models = ["cogs.greetings", "cogs.permissions", "cogs.roles", ]  # "cogs.comics",
    try:
        # db_url="sqlite://skybot.db"
        await Tortoise.init(db_url=bot.config["auth"]["db_url"],
                            modules={"models": models})
        await Tortoise.generate_schemas()
        await bot.start(bot.config["auth"]["discord_token"])  # 1446509632
    finally:
        await bot.logout()
        await Tortoise.close_connections()


if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
