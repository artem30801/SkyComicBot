import logging
import math
import random
from datetime import datetime
from dateutil import relativedelta

import discord
from discord.ext import commands

import tortoise
from tortoise import fields
from tortoise.models import Model

import cogs.cog_utils as utils

logger = logging.getLogger(__name__)


def display_delta(delta):
    d = {"month": delta.months,
         "day": delta.days,
         "hour": delta.hours,
         "minute": delta.minutes,
         "second": delta.seconds,
         }
    return ", ".join([f"{value} {key+'s' if value > 1  else key}" for key, value in d.items() if value > 0])


class HomeChannels(Model):
    guild_id = fields.BigIntField()
    home_channel_id = fields.BigIntField()


class Greetings(commands.Cog):
    """Simple greetings and welcome commands"""

    def __init__(self, bot):
        self.bot = bot
        self._last_greeted_member = None
        self._first_ready = True
        self._started_at = None

    @commands.Cog.listener()
    async def on_ready(self):
        if self._first_ready:
            await self.on_first_ready()
            self._first_ready = False

        guilds_list = [f"[{g.name}: {g.member_count} members]" for g in self.bot.guilds]
        logger.info(f"Connected. Current servers: {', '.join(guilds_list)}")

    async def on_first_ready(self):
        logger.info(f"Logged in as {self.bot.user}")

        self._started_at = datetime.now()

        for guild in self.bot.guilds:
            home_channel, exists_in_db = await self.get_home_channel(guild)

            # try to set a system channel as a home, if entry not exists
            if not exists_in_db:
                home_channel = guild.system_channel
                if not utils.can_bot_respond(self.bot, home_channel):
                    logger.warning(f"Bot can't send messages to system channel #{home_channel.name} at '{guild.name}'")
                    continue

                await self.set_home_channel(guild, home_channel)

            if utils.can_bot_respond(self.bot, home_channel):
                await home_channel.send("Hello hello! I'm back online and ready to work!")
            elif home_channel is not None:
                logger.warning(f"Bot can't send messages to home channel #{home_channel.name} at '{guild.name}'")


    @staticmethod
    async def set_home_channel(guild: discord.Guild, channel: discord.TextChannel):
        """Sets bots home channel for the server"""
        channel_id = channel.id if channel is not None else 0
        home_channel, was_created = await HomeChannels.get_or_create(guild_id=guild.id, defaults={"home_channel_id": channel_id})
        if not was_created:
            home_channel.home_channel_id = channel_id
            await home_channel.save()


    @staticmethod
    async def get_home_channel(guild: discord.Guild):
        home_channel = await HomeChannels.get_or_none(guild_id=guild.id)
        if home_channel:
            if home_channel.home_channel_id == 0:
                return None, True
            return guild.get_channel(home_channel.home_channel_id), True
        
        return None, False

    def get_greeting(self, member):
        greetings = \
            ["Hi, {}!",
             "Hello, {}~",
             "Yo, {}!",
             "Sup, {}",
             "{}! Good to see you!",
             "The Skybox waited for you, {}!",
             "Greetings, {} =)",
             "/-//- /--/ {}.",
             "Oh! Hello there, you must be {}.",
             "G'day, {}!",
             "Howdy, {}",
             "Origato, {}-san",
             "Hoi, {}",
             ]
        message = random.choice(greetings).format(member.display_name)

        if self._last_greeted_member is not None and self._last_greeted_member.id == member.id:
            message = f"{message} This feels oddly familiar..."

        self._last_greeted_member = member
        return message

    @commands.Cog.listener()
    async def on_member_join(self, member):
        channel = member.guild.system_channel
        if channel is not None:
            await channel.send(f"{self.get_greeting(member)} Welcome!")

    @commands.command(aliases=["hi", ])
    async def hello(self, ctx, *, member: discord.Member = None):
        """Says hello to you or mentioned member."""
        member = member or ctx.author
        await ctx.send(self.get_greeting(member))

    @commands.group(aliases=["bind"], case_insensitive=True, invoke_without_command=True)
    @commands.guild_only()
    @utils.has_bot_perms()
    async def home(self, ctx):
        """Sets this channel as a home channel for the bot"""
        current_home, _ = await self.get_home_channel(ctx.guild)
        new_home = ctx.channel

        if current_home == new_home:
            if utils.can_bot_respond(ctx.bot, current_home):
                await ctx.send("I'm already living here, but hey, thanks for the invitation!")
            return

        await self.set_home_channel(ctx.guild, ctx.channel)

        if utils.can_bot_respond(ctx.bot, current_home):
            old_home_response = f"Moving to the {new_home.mention}."
            if not utils.can_bot_respond(ctx.bot, new_home):
                old_home_response += " You know I'm muted there, right? -_-"

            await current_home.send(old_home_response)

        if utils.can_bot_respond(ctx.bot, new_home):
            await new_home.send("From now I'm living here, yay!")

    @home.command(name="evict", aliases=["none", "clear", "yeet"])
    @commands.guild_only()
    @utils.has_bot_perms()
    async def remove_home(self, ctx):
        "Removes home channel for the bot"
        old_home, _ = await self.get_home_channel(ctx.guild)

        if old_home is None:
            await ctx.send("I'm already homeless T_T")
            return

        await self.set_home_channel(ctx.guild, None)

        if utils.can_bot_respond(ctx.bot, old_home):
            await old_home.send("Moved away in the search of a better home")

        await ctx.send("I'm homeless now >_<")        


    @commands.command()
    async def uptime(self, ctx):
        """Shows how long the bot was running for."""
        now = datetime.now()
        delta = relativedelta.relativedelta(now, self._started_at)
        await ctx.send(f"I was up and running since {self._started_at.strftime('%d/%m/%Y, %H:%M:%S')} "
                       f"for {display_delta(delta)}")

    @commands.command()
    async def latency(self, ctx):
        """
        Shows latency between bot and Discord servers.
        Use to check if there are problems with Discord API or bot network.
        """
        await ctx.send(f"Current latency: {math.ceil(self.bot.latency*100)} ms")


