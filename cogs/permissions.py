import logging

import discord
from discord.ext import commands
from discord_slash import cog_ext, SlashContext
from discord_slash.utils.manage_commands import create_option, create_choice

import tortoise
from tortoise.models import Model
from tortoise import fields

from cogs.cog_utils import guild_ids
import cogs.cog_utils as utils


logger = logging.getLogger(__name__)


class BotAdmins(Model):
    id = fields.IntField(pk=True)
    user_id = fields.BigIntField()
    permitted = fields.BooleanField(default=True)


def is_guild_owner():
    def predicate(ctx):
        return ctx.guild is not None and ctx.guild.owner_id == ctx.author.id

    return commands.check(predicate)


async def whitelisted(member):
    return await BotAdmins.exists(user_id=member.id, permitted=True)


def is_whitelisted():
    async def predicate(ctx):
        permitted = await whitelisted(ctx.author)
        return permitted

    return commands.check(predicate)


def has_server_perms():
    """Perms to manage other people on the server"""
    return commands.check_any(is_guild_owner(), commands.is_owner(), commands.has_role("Bot manager"))


def has_bot_perms():
    """Perms to manage bot internal DB"""
    return commands.check_any(commands.is_owner(), is_whitelisted())


class Permissions(utils.AutoLogCog):
    """Commands to manage bot DB permissions"""

    def __init__(self, bot):
        utils.AutoLogCog.__init__(self, logger)
        self.bot = bot

    @staticmethod
    async def _has_bot_perms(ctx, member: discord.Member):
        return await ctx.bot.is_owner(member) or await whitelisted(member)

    @cog_ext.cog_subcommand(base="permissions", name="check",
                            options=[
                                create_option(
                                    name="member",
                                    description="Member to check permissions (or yourself if empty)",
                                    option_type=discord.Member,
                                    required=False,
                                ),
                            ],
                            guild_ids=guild_ids)
    async def permissions_check(self, ctx: SlashContext, member: discord.Member = None):
        """Checks whether you (or specified user) have enough permissions to manage bots database"""
        member = member or ctx.author
        mention = member.mention if member != ctx.author else "You"

        await ctx.send(f"{mention}{'' if await self._has_bot_perms(ctx, member) else ' **DO NOT**'} have "
                       f"enough permissions to manage me and my database.",
                       allowed_mentions=discord.AllowedMentions.none(),
                       hidden=True,
                       )

    @cog_ext.cog_subcommand(base="permissions", name="list", guild_ids=guild_ids)
    async def permissions_list(self, ctx: SlashContext):
        """Shows list of users with permissions for bots database"""
        permitted_ids = await BotAdmins.filter(permitted=True).values_list("user_id", flat=True) + list(ctx.bot.owner_ids)
        users = [ctx.bot.get_user(user_id) for user_id in set(permitted_ids)]
        mentions = [user.mention for user in users if user is not None]
        await ctx.send(f"Users with bot database access: {', '.join(sorted(mentions))}",
                       allowed_mentions=discord.AllowedMentions.none(),
                       hidden=True,
                       )

    @cog_ext.cog_subcommand(base="permissions", name="grant",
                            options=[
                                create_option(
                                    name="member",
                                    description="Member to grant permissions to",
                                    option_type=discord.Member,
                                    required=True,
                                ),
                            ],
                            guild_ids=guild_ids)
    @commands.is_owner()
    async def permissions_grant(self, ctx: SlashContext, member: discord.Member):
        """Grants permissions to manage bots database to specified user"""
        if await whitelisted(member):
            raise commands.BadArgument(f"{member.display_name} is already whitelisted!")

        await BotAdmins.create(user_id=member.id)
        await ctx.send(f"Granted bot access to {member.mention}",
                       allowed_mentions=discord.AllowedMentions.none(),
                       hidden=True,
                       )

    @cog_ext.cog_subcommand(base="permissions", name="revoke",
                            options=[
                                create_option(
                                    name="member",
                                    description="Member to revoke permissions from",
                                    option_type=discord.Member,
                                    required=True,
                                ),
                            ],
                            guild_ids=guild_ids)
    @commands.is_owner()
    async def revoke(self, ctx: SlashContext, member: discord.Member):
        """Revokes permissions to manage bots database from specified user"""
        if not await whitelisted(member):
            raise commands.BadArgument(f"{member.display_name} is not whitelisted anyways!")

        user = await BotAdmins.get(user_id=member.id)
        await user.delete()

        await ctx.send(f"Revoked bot access from {member.mention}",
                       allowed_mentions=discord.AllowedMentions.none(),
                       hidden=True
                       )


def setup(bot):
    bot.add_cog(Permissions(bot))
