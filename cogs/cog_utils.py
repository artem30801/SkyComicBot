import logging
import os

import discord
from discord import Guild, TextChannel, Member, Role
from discord.ext import commands
from discord.ext.commands import Bot
from discord_slash import SlashContext
from fuzzywuzzy import fuzz
from fuzzywuzzy import process

from cogs.models import HomeChannels

embed_color = 0x72a3f2
bot_manager_role = "skybox manager"
stream_crew_role = "livestream crew"
update_crew_role = "update crew"
snapshot_role_group = "Snapshot"
db_log_level = 25
important_log_level = 29
guild_ids = None  # Set in main.py


class AutoLogCog(commands.Cog):
    def __init__(self, logger, *args, **kwargs):
        self.logger = logger
        print(self, logger)

    @staticmethod
    def format_stack(*args):
        return ">".join([str(arg) for arg in args if arg is not None])

    @classmethod
    def format_caller(cls, ctx):
        return cls.format_stack(ctx.guild, ctx.channel, ctx.author)

    @classmethod
    def format_command(cls, ctx):
        return cls.format_stack(ctx.command, ctx.subcommand_name, ctx.subcommand_group)

    def get_command(self, ctx):
        if ctx.subcommand_name is not None:
            command = self.bot.slash.subcommands[ctx.command]
            if ctx.subcommand_group is None:
                return command[ctx.subcommand_name]
            return command[ctx.subcommand_name][ctx.subcommand_group]
        return self.bot.slash.commands[ctx.command]

    def check_command_cog(self, ctx):
        try:
            command = self.get_command(ctx)
        except KeyError:
            return False

        if command.cog is self:
            return True
        return False

    @commands.Cog.listener()
    async def on_slash_command(self, ctx: SlashContext):
        if self.check_command_cog(ctx):
            self.logger.debug(f"{self.format_caller(ctx)} invoked command "
                              f"{self.format_command(ctx)}")

    @commands.Cog.listener()
    async def on_slash_command_error(self, ctx: SlashContext, error):
        if self.check_command_cog(ctx):
            self.logger.warning(f"{self.format_caller(ctx)} caused exception in command "
                                f"{self.format_command(ctx)}: {repr(error)}")


class StartupCog(commands.Cog):
    def __init__(self, *args, **kwargs):
        self._first_ready = True

    @commands.Cog.listener()
    async def on_ready(self):
        if self._first_ready:
            await self.on_startup()
            self._first_ready = False

    async def on_startup(self):
        pass


class DBLogger(logging.getLoggerClass()):
    def __init__(self, name, level=logging.NOTSET):
        super().__init__(name, level)
        logging.addLevelName(db_log_level, "DATABASE")
        logging.addLevelName(important_log_level, "IMPORTANT")

    def db(self, msg, *args, **kwargs):
        if self.isEnabledFor(db_log_level):
            self._log(db_log_level, msg, args, **kwargs)

    def important(self, msg, *args, **kwargs):
        if self.isEnabledFor(important_log_level):
            self._log(important_log_level, msg, args, **kwargs)


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


def can_bot_respond(bot: Bot, channel: TextChannel) -> bool:
    """Checks, can a bot send messages to this channel"""
    if bot is None or channel is None:
        return False

    bot_as_member = channel.guild.get_member(bot.user.id)
    permissions = channel.permissions_for(bot_as_member)
    return permissions.send_messages


async def get_home_channel(guild: Guild) -> TextChannel:
    home_channel = await HomeChannels.get_or_none(guild_id=guild.id)
    if home_channel is None:
        return guild.system_channel
    if home_channel.channel_id is None:
        return None
    return guild.get_channel(home_channel.channel_id)


def can_manage_role(bot: Member, role: Role) -> bool:
    """Checks, can a bot change assign this role to anybody"""
    if not bot.guild_permissions.manage_roles:
        return False

    if bot.top_role > role:
        return True

    return False
