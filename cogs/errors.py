import logging

import discord
from discord.ext import commands

import traceback

logger = logging.getLogger(__name__)


class Errors(commands.Cog):
    @commands.Cog.listener()
    async def on_command_error(self, ctx, error):
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.send(f"Heyo! Not so fast! Try {ctx.command} again in {error.retry_after:.2f}s")
        elif isinstance(error, commands.BadArgument):
            await ctx.send(f"Sorry, but your arguments are invalid: {' ,'.join(error.args)}")
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(f"Sorry, but you missed required argument! {' ,'.join(error.args)}")
        elif isinstance(error, commands.CheckFailure):
            await ctx.send('\n'.join(error.args))
        elif isinstance(error, commands.NoPrivateMessage):
            await ctx.send("You can use that only in guild!")
        else:
            logger.error(f"Error {type(error)} occurred: {error} (in result of {ctx.message.content})")
