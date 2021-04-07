import logging

import discord
from discord.ext import commands
from discord_slash import cog_ext, SlashContext
from discord_slash.utils.manage_commands import create_option, create_choice

import cogs.cog_utils as utils
from cogs.permissions import has_server_perms, has_bot_perms


logger = logging.getLogger(__name__)


class Service(utils.AutoLogCog, utils.StartupCog):
    def __init__(self, bot):
        utils.StartupCog.__init__(self)
        utils.AutoLogCog.__init__(self, logger)
        self.bot = bot

    @commands.command()
    @has_bot_perms()
    async def sync(self, ctx):
        logger.info(f"{self.format_caller(ctx)} trying to sync slash commands manually")
        message = await ctx.send("Syncing slash commands...")
        try:
            await self.bot.slash.sync_all_commands()
        except Exception:
            await message.edit(content="Error during commands synchronization! Refer to logs")
            raise
        else:
            logger.info("Successfully synchronized all slash commands")
            await message.edit(content="Completed syncing all slash commands")

    @commands.command()
    async def help(self, ctx, *, _=''):
        embed = discord.Embed(title="SkyComicBot help", color=utils.embed_color,
                              description="We migrated to use newest Discord feature: slash commands.\n"
                                          "Now you can interact with the bot in much more... eh... interactive way!\n"
                                          "Just type / and list of all available commands will appear.\n"
                                          "Use 'TAB' button to autocomplete and choose commands, options, choices.\n"
                                          "[Read details at Discord blog]"
                                          "(https://blog.discord.com/slash-commands-are-here-8db0a385d9e6)",
                              )
        await ctx.send(embed=embed)


def setup(bot):
    bot.add_cog(Service(bot))
