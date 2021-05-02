import logging

import discord
from discord.ext import commands
from discord_slash import cog_ext, SlashContext
from discord_slash.utils.manage_commands import create_option, create_choice

import cogs.cog_utils as utils
import cogs.db_utils as db_utils

from cogs.cog_utils import guild_ids
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

    @cog_ext.cog_subcommand(base="bot", name="update", guild_ids=guild_ids)
    @has_bot_perms()
    async def update(self, ctx: SlashContext):
        """Updates the bot with the latest version from GIT and restarts it"""
        await ctx.defer()
        output, error = await utils.run(f"(cd {self.bot.current_dir}; git pull)")

        result = ""
        if output:
            result += f"*Output:* {output.strip()}\n"
        if error:
            result += f"*Errors:* {error.strip()}"

        await ctx.send(f"**Pulled updates from git** \n {result or 'No output'}")

        output, error = await utils.run(f"(cd {self.bot.current_dir}; "
                                        f". ./venv/bin/activate; "
                                        f"pip install -r requirements.txt)")
        if not error:
            lines = output.strip().split("\n")
            if (last := lines[-1].strip()).startswith("Successfully installed"):
                installed = last.removeprefix("Successfully installed").strip().split()
                result = f"Installed/upgraded **{len(installed)}** packages: \n, {', '.join(installed)}"
            else:
                result = "No packages were installed or upgraded"
        else:
            result = f"Error installing packages:\n {error.strip()}"
        await ctx.send(f"**Installed requirements** \n {result}")

        if output.strip() != "Already up to date.":
            await self.restart.invoke(ctx)

    @cog_ext.cog_subcommand(base="bot", name="restart", guild_ids=guild_ids)
    @has_bot_perms()
    async def restart(self, ctx: SlashContext):
        """Restarts the bot"""
        await ctx.send(":warning: **Restarting the bot!** :warning:")
        await self.bot.close()
        await utils.run(f"systemctl --user restart skycomicbot.service")

    @cog_ext.cog_subcommand(base="bot", name="shutdown", guild_ids=guild_ids)
    @has_bot_perms()
    async def shutdown(self, ctx: SlashContext):
        """Shutdowns the bot. Use in emergency only!"""
        await ctx.send(":warning: **Shutting down the bot!** :warning:")
        await self.bot.close()
        # It shouldn't be running after "close" but juuust in case add this
        await utils.run("systemctl --user stop skycomicbot.service")

    @cog_ext.cog_subcommand(base="bot", name="logs",
                            options=[create_option(
                                name="lines",
                                description="Amount of lines to show ()",
                                option_type=int,
                                required=False,
                            )],
                            guild_ids=guild_ids)
    @has_bot_perms()
    async def logs(self, ctx: SlashContext, lines=20):
        """Shows specified amount of last lines from bots log"""
        if lines > 200 or lines < 10:
            raise commands.BadArgument("Lines amount should be between 10 and 200!")

        logs, err = await utils.run(f"journalctl --user-unit skycomicbot.service -n {lines} -q --no-pager -o cat")
        if err:
            await ctx.send(f"Cannot fetch logs! Error {err}", hidden=True)
            return

        logs = logs.split("\n")
        for chunk in db_utils.chunks_split(logs):
            await ctx.send("\n".join(chunk))


def setup(bot):
    bot.add_cog(Service(bot))
