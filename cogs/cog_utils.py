import logging
import os
import re

import asyncio
from functools import wraps

import discord
import tortoise.exceptions
from discord import Guild, TextChannel, Member, Role
from discord.ext import commands

from discord_slash import SlashContext

from fuzzywuzzy import fuzz
from fuzzywuzzy import process

time_format = '%d/%m/%Y, %H:%M:%S'
embed_color = 0x72a3f2

check_emote = "✅"
fail_emote = "❌"
info_emote = "ℹ"
refresh_emote = "🔁"

bot_manager_role = "skybox manager"
stream_crew_role = "livestream crew"
update_crew_role = "update crew"
bot_crew_role = "bot crew"
developer_role = "coroutine"
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
        return cls.format_stack(getattr(ctx, "guild", None),
                                getattr(ctx, "channel", None),
                                getattr(ctx, "author", ctx))

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


class BackoffStrategyBase:
    def __init__(self, base_delay=1, max_attempts=None, max_delay=None, exponent=2):
        self.base_delay = base_delay
        self.max_attempts = max_attempts
        self.max_delay = max_delay or float('inf')

        if exponent <= 1:
            raise ValueError("Exponent must be greater than 1")
        self.exponent = exponent

        self.attempt = 0

    def get_attempt_delay(self, attempt):
        delay = self.base_delay * pow(self.exponent, attempt)
        return min(delay, self.max_delay)

    def reset(self):
        self.attempt = 0

    def __iter__(self):
        self.reset()
        return self

    def __next__(self):
        self.attempt += 1
        if self.max_attempts is not None and self.attempt > self.max_attempts:
            raise StopIteration

        return self.get_attempt_delay(self.attempt)


class OrmBackoffStrategy(BackoffStrategyBase):
    def __init__(self, base_delay=0.5, max_attempts=5, exponent=2,
                 fail_exception_class=tortoise.exceptions.OperationalError):
        super().__init__(base_delay=base_delay, max_attempts=max_attempts, exponent=exponent)
        self.exception_class = fail_exception_class

    async def run_task(self, task, *args, **kwargs):
        last_exception = None
        for delay in self:
            try:
                return await task(*args, **kwargs)
            except self.exception_class as exception:
                last_exception = exception
                await asyncio.sleep(delay)
        # Raise last exception if all attempts failed
        raise last_exception


class ThreadUnsupported(commands.CheckFailure):
    def __init__(self, message, *args):
        super().__init__(message=message, *args)


def block_in_threads(command):
    @wraps(command)
    async def ensure(cog_object, ctx, *args, **kwargs):
        if ctx.channel_id and not ctx.channel:
            raise ThreadUnsupported(ctx.message)
        return await command(cog_object, ctx, *args, **kwargs)

    return ensure


async def run(cmd):
    proc = await asyncio.create_subprocess_shell(
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE)

    stdout, stderr = await proc.communicate()
    return stdout.decode('ascii', errors="backslashreplace"), stderr.decode("ascii", errors="backslashreplace")


async def get_message_from_link(bot: commands.Bot, link: str) -> discord.Message:
    if not get_message_from_link.message_regex:
        # basically a static variable, so we don't compile this regex every call
        get_message_from_link.message_regex = re.compile(
            pattern=r"(\A|\W)https://discord.com/channels/(?P<guild_id>\d+)/(?P<channel_id>\d+)/(?P<msg_id>\d+)(\Z|\W)"
        )
    match = get_message_from_link.message_regex.match(string=link)
    if not match:
        raise commands.BadArgument(f"Looks like '{link}' is not a link to discord message")
    ids = match.groupdict()

    guild = bot.get_guild(int(ids['guild_id']))
    if not guild:
        raise commands.BadArgument(f"Bot don't have access to the guild with ID {ids['guild_id']}")
    channel = guild.get_channel(int(ids['channel_id']))
    if not channel:
        raise commands.BadArgument(f"Bot don't have access to the channel with ID {ids['channel_id']} on server {guild}")
    try:
        message = await channel.fetch_message(int(ids['msg_id']))
    except discord.NotFound:
        raise commands.BadArgument(f"Bot can't get the message with with ID {ids['msg_id']} in {channel.mention}")
    if not message:
        raise commands.BadArgument(f"Bot can't get the message with with ID {ids['msg_id']} in {channel.mention}")
    return message
get_message_from_link.message_regex = None


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


def ensure_path_dirs(path):
    dirs = os.path.split(os.path.dirname(path))
    for i in range(len(dirs)):
        ensure_dir(abs_join(*dirs[:i + 1]))


async def send_file(channel, path, filename=None):
    file = discord.File(path, filename=filename or os.path.split(path)[1])
    await channel.send(" ", file=file)


def url_hostname(url):
    return url.split("//")[-1].split("/")[0].split('?')[0]


def can_bot_respond(channel: TextChannel) -> bool:
    """Checks, can a bot send messages to this channel"""
    # TODO: until we have proper threads support, we allow bot to respond in threads
    if not channel:
        return True
    bot = channel.guild.me
    permissions = channel.permissions_for(bot)
    return permissions.send_messages


def can_bot_manage_messages(channel: TextChannel):
    bot = channel.guild.me
    permissions = channel.permissions_for(bot)
    return permissions.manage_messages


def can_manage_role(bot: Member, role: Role) -> bool:
    """Checks, can a bot change assign this role to anybody"""
    if not bot.guild_permissions.manage_roles:
        return False

    return bot.top_role > role


def display_delta(delta, display_values_amount: int = 3):
    d = {
        "year": delta.years,
        "month": delta.months,
        "day": delta.days,
        "hour": delta.hours,
        "minute": delta.minutes,
    }
    values = [f"{value} {key + 's' if value > 1 else key}" for key, value in d.items() if value > 0]
    if display_values_amount:
        values = values[:display_values_amount]
    result = ", ".join(values)
    return result or "less than a minute"


def display_task_period(task, separator=" and ") -> str:
    result = ""
    if task.hours:
        result = "hour" if task.hours == 1 else f"{task.hours} hours"
    if task.minutes:
        if task.minutes == 1:
            minutes = "1 minute" if result else "minute"
        else:
            minutes = f"{task.minutes} minutes"
        result = result + separator + minutes if result else minutes
    if task.seconds:
        if task.seconds == 1:
            seconds = "1 second" if result else "second"
        else:
            seconds = f"{task.seconds} seconds"
        result = result + separator + seconds if result else seconds
    return result or "unsupported"


def format_line(line, lang="yaml"):
    return f"```{lang}\n{line}```"


def format_lines(args: dict, lang="yaml", delimiter=":"):
    max_len = max(map(len, args.keys()))
    lines = [f"```{lang}"] + [f"{name:<{max_len}}{delimiter} {value}" for name, value in args.items()] + ["```"]
    return "\n".join(lines)


def format_size(size, accuracy=1):
    units = ["Bytes", "KiB", "MiB", "GiB", "TiB"]
    radix = float(1024)

    for num, unit in enumerate(units):
        if size < radix or num == len(units) - 1:
            return f"{size:.{accuracy}f} {unit}"
        size /= radix


async def has_permissions(ctx, **perms):
    try:
        await commands.bot_has_permissions(**perms).predicate(ctx)
        return True
    except commands.BotMissingPermissions:
        return False


def ensure_tasks_running(tasks):
    for task in tasks:
        if task.is_running():
            continue
        if task.failed():
            task.restart()
        else:
            task.start()


def ensure_tasks_stopped(tasks):
    for task in tasks:
        if task.is_running():
            task.stop()


def bot_embed(bot):
    embed = discord.Embed(colour=embed_color)
    embed.set_author(name=bot.user.name, icon_url=bot.user.avatar_url)
    return embed
