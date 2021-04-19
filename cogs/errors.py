import logging
import random

import discord
from discord.ext import commands
from discord.ext.commands.errors import RoleNotFound
from discord_slash import SlashContext
from tortoise import exceptions as t_exceptions

import cogs.cog_utils as utils


logger = logging.getLogger(__name__)


class Errors(commands.Cog):

    def __init__(self, bot):
        self.should_ping_on_error = True
        if 'ping_on_error' in bot.config['discord']:
            self.should_ping_on_error = bot.config['discord']['ping_on_error']

    @commands.Cog.listener()
    async def on_slash_command_error(self, ctx: SlashContext, error):
        if isinstance(error, commands.CommandOnCooldown):
            message = f"Heyo! Not so fast! Try {ctx.command} again in {error.retry_after:.2f}s"
        elif isinstance(error, commands.BadArgument):
            message = f"Sorry, but your arguments are invalid: {' ,'.join(error.args)}"
        elif isinstance(error, commands.MissingRequiredArgument):
            message = f"Sorry, but you missed required argument! {' ,'.join(error.args)}"
        elif isinstance(error, commands.CheckFailure):
            message = '\n'.join(error.args)
        elif isinstance(error, commands.NoPrivateMessage):
            message = "You can use that only in guild!"
        elif isinstance(error, t_exceptions.OperationalError):
            await ctx.channel.send("Database connection error, please retry!")
            return
        else:
            logger.error(f"Unexpected error {repr(error)} occurred:", exc_info=error)
            if self.should_ping_on_error:
                await ctx.channel.send(await self.get_emergency_message(ctx))
            # await send_file(ctx.channel, abs_join("misc", "code.jpg"), "code.jpg")
            return

        await ctx.send(message, hidden=True, allowed_mentions=discord.AllowedMentions.none())
    
    @staticmethod
    async def get_emergency_message(ctx) -> str:
        try:
            mention = (await commands.RoleConverter().convert(ctx, utils.developer_role)).mention
        except RoleNotFound:
            mention = (await ctx.bot.fetch_user(246333265495982080)).mention
        except:
            return "Where am I? I've caught an error, ping developers"
        
        if random.random() < 0.005:
            return f":sparkles: Congrats! You've caught a shiny error message! :sparkles:\n{mention}, come and fix me!"

        message = [
            "{}, we have a problem!",
            "{}, hjælp",
            "{}, something went wrong",
            "Whoops, {}, I've caught an error!",
            "Uhh, {}, help me!",
            "{}, fix me!",
            "{}, halp",
            "{}, I've got an error!",
            "{}, something is broken!",
            "{}, h̵͚̍͝ȅ̵̢̠͠l̴̜̪̿ṕ̸̫",
            "{}, pick me up, I'm scared!",
            "{}, need your help >_<",
            "{}, I broke my code!",
            "{}, I felt a great disturbance in the Code...",
            "{}, after questioning my life choices and reevaluating my state in the world I've come to the conclusion that I have a bug",
            "{}, I found :bug:",
            "{}, I found :lady_beetle:",
            "{}, it was a beautiful day on the server and I've encountered a horrible bug",
            "{}, something is wrong and I blame Gaster",
            "{}, I can't work like this!",
            "Good code makes sense. Bad code just works. {}'s doesn't do either.",
            "{}, plz send help",
        ]
        return random.choice(message).format(mention)


def setup(bot):
    bot.add_cog(Errors(bot))
