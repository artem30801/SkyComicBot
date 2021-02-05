import logging

import discord
from discord.ext import commands

import tortoise
from tortoise.models import Model
from tortoise import fields

import asyncio
import typing


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


class Permissions(commands.Cog):
    """Commands to gr"""
    def __init__(self, bot):
        self.bot = bot

    async def cog_check(self, ctx):
        member = ctx.author
        return await ctx.bot.is_owner(member) or await whitelisted(member)

    @commands.group(aliases=["permissions", "perm", ], invoke_without_command=True)
    async def perms(self, ctx, member: typing.Optional[discord.Member] = None):
        # member = ctx.author or member
        if member is None:
            await ctx.send("If you can see this message, you have "
                           "enough permissions to manage me and my database. Congratulations!")
            return

        await ctx.send(f"{member.mention} has {'' if await whitelisted(member) else '**NOT** '}"
                       f"enough permissions to manage me and my database.",
                       allowed_mentions=discord.AllowedMentions.none()
                       )

    @perms.command(aliases=["add", "+", "whitelist"])
    @commands.is_owner()
    async def grant(self, ctx, member: discord.Member):
        if await whitelisted(member):
            raise commands.BadArgument(f"{member.display_name} is already whitelisted!")

        await BotAdmins.create(user_id=member.id)
        await ctx.send(f"Granted bot access to {member.mention}", allowed_mentions=discord.AllowedMentions.none())

    @perms.command(aliases=["remove", "delete", "ban", "-", "blacklist"])
    @commands.is_owner()
    async def revoke(self, ctx, member: discord.Member):
        if not await whitelisted(member):
            raise commands.BadArgument(f"{member.display_name} is not whitelisted anyways!")

        user = await BotAdmins.get(user_id=member.id)
        await user.delete()

        await ctx.send(f"Revoked bot access from {member.mention}", allowed_mentions=discord.AllowedMentions.none())
