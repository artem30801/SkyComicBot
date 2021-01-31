import discord
from discord.ext import commands

from tortoise.models import Model
from tortoise.functions import Max
from tortoise import fields

import re
import asyncio
import json


def next_priority():
    loop = asyncio.get_event_loop()
    max_priority = loop.run_until_complete(Role.annotate(m=Max("priority")).values_list("m", flat=True))[0]
    # print("prior", max_priority, max_priority or 0)
    return max_priority + 1 if max_priority is not None else 0


class Role(Model):
    id = fields.IntField(pk=True)
    name = fields.TextField()
    color = fields.SmallIntField()
    priority = fields.IntField(default=next_priority)
    to_remove = fields.BooleanField(default=False)
    group = fields.TextField(default="default")  # users can have only one decorative role

    class Meta:
        ordering = ["priority"]

    def __str__(self):
        return self.name


class Roles(commands.Cog):
    """Roles management commands"""
    # :griffin_hug:
    def __init__(self, bot):
        self.bot = bot

    async def update_guilds_roles(self):
        db_roles = await Role.all().values("name", "color", "to_remove")
        roles_dict = {role['name']: role['color'] for role in db_roles if not role['to_remove']}
        to_remove = set(role['name'] for role in db_roles if role['to_remove'])

        for guild in self.bot.guilds:
            for name, color in roles_dict.items():
                role = discord.utils.get(guild.roles, name=name)
                if role is not None:
                    await role.edit(colour=color, mentionable=True)
                else:
                    await guild.create_role(name=name, colour=color)

            for name in to_remove:
                role = discord.utils.get(guild.roles, name=name)
                if role is not None:
                    await role.delete()

    @commands.group(aliases=["roles", ], case_insensitive=True,)
    async def role(self, ctx):
        """Role management commands, shows you your roles. You can use mentions instead of role names"""
        if ctx.invoked_subcommand is None:
            await ctx.send(f"Your roles are: {', '.join([f'**{role.name}**' for role in ctx.author.roles[1:]])}. "
                           f"To view available roles, use !role list")

    @role.command(aliases=["all", "available", "view", ])
    async def list(self, ctx):
        roles = await Role.exclude(to_remove=True).values_list("name", flat=True)
        await ctx.send("Avialiable roles: \n" + "\n".join(roles))
        # print(*[(role.name, role.position) for role in ctx.guild.roles], sep="\n")

    @role.command(aliases=["join", "assign", ])
    @commands.guild_only()
    async def add(self, ctx, role: discord.Role, member: discord.Member = None):
        """Add specified role to you or mentioned member"""
        if member is not None and not await self.has_bot_perms(ctx.author):
            raise commands.MissingPermissions("Insufficient permissions for editing other users roles")

        member = member or ctx.author

        bot_role = self.get_bot_role(ctx.guild)
        if role == bot_role and not await self.has_bot_perms(ctx.author):
            raise commands.MissingPermissions("Insufficient permissions for the bot management role")

        await member.add_roles(role)
        await ctx.send(f"Added role **{role.name}** to {member.mention}")

    def get_bot_role(self, guild):
        return discord.utils.get(guild.roles, name="Bot manager")

    async def has_bot_perms(self, member: discord.Member):
        if member == member.guild.owner or member.id in self.bot.owner_ids:
            return True
        bot_role = self.get_bot_role(member.guild)
        if bot_role is not None and member.top_role >= bot_role:
            return True
        return False

    @role.command(aliases=["leave", "clear", "delete", "yeet", ])
    @commands.guild_only()
    async def remove(self, ctx, role: discord.Role = None, member: discord.Member = None):
        """Remove specified role from you or mentioned member"""
        if member is not None and not await self.has_bot_perms(ctx.author):
            raise commands.MissingPermissions("Insufficient permissions for editing other users roles")

        member = member or ctx.author
        roles = [role, ] if role is not None else await Role.exclude(to_remove=True).values_list("name", flat=True)
        await member.remove_roles(*roles)
        await ctx.send(f"Removed role **{role.name}** from {member.mention}")

    @role.command()
    async def new(self, ctx, name: str, color: discord.Colour, group: str = "default"):
        """Adds new role to server network database and discord servers"""
        if not await self.has_bot_perms(ctx.author):
            raise commands.MissingPermissions("Insufficient permissions for editing roles")

        name = await self.ensure_unique_name(name)
        await Role.create(name=name, color=color.value, group=group)
        await self.update_guilds_roles()

        role = discord.utils.get(ctx.guild.roles, name=name)
        await ctx.send(f"Created role {role.mention}")

    @role.command()
    async def edit(self, ctx, role: discord.Role, *, json_arg):
        kwargs = json.loads(json_arg)
        d = {}
        if "name" in kwargs:
            name = await self.ensure_unique_name(kwargs["name"])
            d["name"] = name
        if "color" in kwargs:
            converter = commands.ColourConverter()
            color = converter.convert(ctx, kwargs["color"])
            d["color"] = color
        if "priority" in kwargs:
            priority = kwargs["priority"]

    async def ensure_unique_name(self, name):
        if await Role.filter(name=name).exists():
            raise commands.BadArgument(f"Role '{name}' already exists")
        return name

    @role.command()
    async def archive(self, ctx, role: discord.Role):
        """Archives role to server network database, deletes it from discord"""
        if not await self.has_bot_perms(ctx.author):
            raise commands.MissingPermissions("Insufficient permissions for editing roles")

        if self.get_bot_role(ctx.guild) == role:
            raise commands.MissingPermissions("I wont delete the bot management role lmao")

        # db_role = await Role.filter(name=role.name).first()
        # if db_role.to_remove:
        #     raise commands.BadArgument("Already archived")

        db_role = await Role.filter(name=role.name).update(to_remove=True)
        await self.update_guilds_roles()
        await ctx.send(f"Archived role {role.mention}")

    @commands.command()
    async def side(self, ctx, *, role_arg: str):
        if role_arg in ["leave", "clear", "delete", "remove", "yeet", "no", "none", "noside", "nosider", "human"]:
            await self.remove_group(ctx.author, "side")
            await ctx.send("You're now a *noside*! Is that what you wanted?")
            return
        role_arg = role_arg.title()
        try:
            converter = commands.RoleConverter()
            role = await converter.convert(ctx, role_arg)
        except commands.RoleNotFound:
            pass

        print(role)

    async def get_role_froup(self, group: str):
        return await Role.filter(group=group).values_list("name", flat=True)
        #await member.remove_roles(*roles)
        #ctx.author.add_roles(role)
