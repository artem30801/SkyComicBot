import discord
from discord.ext import commands

import random
from datetime import datetime
from dateutil import relativedelta


def display_delta(delta):
    d = {"month": delta.months,
         "day": delta.days,
         "hour": delta.hours,
         "minute": delta.minutes,
         "second": delta.seconds,
         }
    return ", ".join([f"{value} {key+'s' if value > 1  else key}" for key, value in d.items() if value > 0])


class Greetings(commands.Cog):
    """Simple greetings and welcome commands"""

    def __init__(self, bot):
        self.bot = bot
        self._last_member = None
        self._started_at = None

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

        if self._last_member is not None and self._last_member.id == member.id:
            message = f"{message} This feels oddly familiar..."

        self._last_member = member
        return message

    @commands.Cog.listener()
    async def on_ready(self):
        now = datetime.now()
        self._started_at = now
        print(f"Logged in as {self.bot.user} at {now.strftime('%d/%m/%Y, %H:%M:%S')}")
        guilds_list = [f"[{g.name}: {g.member_count} members]" for g in self.bot.guilds]
        print(f"Current servers: {', '.join(guilds_list)}")

        for guild in self.bot.guilds:
            channel = guild.system_channel
            if channel is not None:
                await channel.send("Hello hello! I'm back online and ready to work!")

    @commands.Cog.listener()
    async def on_member_join(self, member):
        channel = member.guild.system_channel
        if channel is not None:
            await channel.send(f"{self.get_greeting(member)} Welcome!")

    @commands.command()
    async def uptime(self, ctx):
        """Shows how long the bot was running for"""
        now = datetime.now()
        delta = relativedelta.relativedelta(now, self._started_at)
        await ctx.send(f"I was up and running since {self._started_at.strftime('%d/%m/%Y, %H:%M:%S')} "
                       f"for {display_delta(delta)}")

    @commands.command(aliases=["hi", ])
    async def hello(self, ctx, *, member: discord.Member = None):
        """Says hello to you or mentioned member"""
        member = member or ctx.author
        await ctx.send(self.get_greeting(member))
