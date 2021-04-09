import logging

import discord
from discord.ext import commands
from discord_slash import SlashContext
from tortoise import exceptions as t_exceptions

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
        elif isinstance(error, t_exceptions.OperationalError):
            await ctx.channel.send("Database connection error, please retry!")
            return
        else:
            logger.error(f"Unexpected error {repr(error)} occurred:", exc_info=error)
            await ctx.channel.send(f"Unexpected error! "
                                   f"{(await ctx.bot.fetch_user(246333265495982080)).mention} come and fix me!")
            # await send_file(ctx.channel, abs_join("misc", "code.jpg"), "code.jpg")
            return

        await ctx.send(message, hidden=True, allowed_mentions=discord.AllowedMentions.none())


def setup(bot):
    bot.add_cog(Errors(bot))
