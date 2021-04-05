import logging

import discord
from discord.ext import commands
from discord_slash import cog_ext, SlashContext
from discord_slash.utils.manage_commands import create_option, create_choice

import traceback

from cogs.cog_utils import send_file, abs_join

logger = logging.getLogger(__name__)


class Errors(commands.Cog):
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
        else:
            logger.error(f"Error {type(error)} occurred: {error}")
            await ctx.channel.send(f"Unexpected error! "
                                   f"{(await ctx.bot.fetch_user(246333265495982080)).mention} come and fix me!")
            # await send_file(ctx.channel, abs_join("misc", "code.jpg"), "code.jpg")
            return

        await ctx.send(message, hidden=True)


def setup(bot):
    bot.add_cog(Errors(bot))
