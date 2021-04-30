import asyncio
import json
import logging
import os
from datetime import datetime

import discord
import nest_asyncio
from discord.ext import commands
from discord_slash import SlashCommand
from tortoise import Tortoise

import cogs.cog_utils as utils
from cogs.logging_utils import BufferingSocketHandler

# from cogs.comics import Comics

version = "3.6.4"  # bump this with update releases

nest_asyncio.apply()


# https://discordapp.com/oauth2/authorize?&client_id=804306819660644372&scope=applications.commands%20bot&permissions=1446509632
# scope=applications.commands%20bot
class SkyComicBot(commands.Bot):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        SlashCommand(self, override_type=True)
        self.version = version
        self.current_dir = os.path.dirname(os.path.realpath(__file__))
        self.token = None

        self.config = {}
        self.load_config("config.json")

    def real_path(self, *paths):
        return utils.abs_join(self.current_dir, *paths)

    def load_config(self, path):
        with open(self.real_path(path), "r") as f:
            self.config = json.load(f)

        self.owner_ids = set(self.config["discord"]["owner_ids"])
        utils.guild_ids = self.config["discord"]["guild_ids"]
        self.token = self.config["auth"]["discord_token"]

    async def start(self):
        await super().start(self.token)


async def main():
    bot = SkyComicBot(command_prefix=commands.when_mentioned_or("$", "!"),
                      help_command=None,
                      case_insensitive=True,
                      intents=discord.Intents.all())

    now = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    utils.ensure_dir(utils.abs_join(bot.current_dir, "logs"))

    logging.setLoggerClass(utils.DBLogger)
    log_handlers = [
        logging.FileHandler(bot.real_path("logs", f"{now}.log")),
        logging.StreamHandler()
    ]
    if "logging" in bot.config and "socket_handlers" in bot.config["logging"]:
        for num, handler in enumerate(bot.config["logging"]["socket_handlers"], start=1):
            buffer = bot.real_path("logs", f"log_buffer_{num}.bin")
            socket_handler = BufferingSocketHandler(handler["host"], handler["port"], buffer)
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

    bot.load_extension("cogs.service")
    bot.load_extension("cogs.converters")
    bot.load_extension("cogs.greetings")
    bot.load_extension("cogs.permissions")
    bot.load_extension("cogs.errors")
    bot.load_extension("cogs.automod")
    bot.load_extension("cogs.reactions")
    bot.load_extension("cogs.emotes")
    bot.load_extension("cogs.roles")
    bot.load_extension("cogs.channels")

    models = ["cogs.permissions", "cogs.roles", "cogs.converters", "cogs.channels", ]  # "cogs.comics",
    try:
        await Tortoise.init(db_url=bot.config["auth"]["db_url"],
                            modules={"models": models})
        await Tortoise.generate_schemas()
        await bot.start()
    finally:
        await bot.logout()
        await Tortoise.close_connections()


if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
