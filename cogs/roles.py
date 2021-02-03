import logging

import discord
from discord.ext import commands

from tortoise.models import Model
from tortoise import fields

import re
import json
import typing

from .cog_utils import next_number, fuzzy_search, has_server_perms, has_bot_perms, parse_params, convert_to_bool


logger = logging.getLogger(__name__)


class Role(Model):
    id = fields.IntField(pk=True)
    name = fields.TextField()
    color = fields.SmallIntField()
    number = fields.IntField(default=next_number(__qualname__))
    archived = fields.BooleanField(default=False)
    group = fields.ForeignKeyField("models.RoleGroup", related_name="roles")

    class Meta:
        ordering = ["number"]

    def __str__(self):
        return self.name


class RoleGroup(Model):
    id = fields.IntField(pk=True)
    name = fields.TextField()
    # archived = fields.BooleanField(default=False)
    exclusive = fields.BooleanField(default=True)
    roles: fields.ReverseRelation["Role"]

    def __str__(self):
        return self.name


class RoleGroupConverter(commands.Converter):
    async def convert(self, ctx, argument):
        group_name = fuzzy_search(argument, await RoleGroup.all().values_list("name", flat=True))
        if group_name is None:
            raise commands.BadArgument(f"Sorry, I cant find role group **{argument}**. "
                                       f"Try *!role groups* command to see available groups")
        return await RoleGroup.get(name=group_name)


class InvalidData():
    """Class-flag that signals, that we're failed to convert string to the valid user/role"""
    
    def __init__(self, data: [str]):
        self.data = data

    def __str__(self):
        return ' '.join(self.data)
    
    def __getitem__(self, key):
        return self.data[key]

    def __len__(self):
        return len(self.data)


class MemberConverter(commands.MemberConverter):
    async def convert(self, ctx, argument):
        try:
            return await super().convert(ctx, argument)
        except commands.MemberNotFound:
            # Maybe try users fuzzy search, but I feel like it will be slow with a lot of users on the server
            return InvalidData(argument.split(' '))


class DBRoleConverter(commands.RoleConverter):
    async def convert(self, ctx, argument):
        try:
            role = await super().convert(ctx, argument)  # try to get role directly (for mentions and stuff)
            argument = role.name
        except commands.RoleNotFound:  # if cant, try to fuzzy search
            pass

        role_name = fuzzy_search(argument, await Role.exclude(archived=True).values_list("name", flat=True))
        if role_name is None:
            raise commands.RoleNotFound(argument)
        return await Role.get(name=role_name)


class DiscordRoleConverter(DBRoleConverter):
    async def convert(self, ctx, argument):
        try:
            return await super().convert(ctx, argument)
        except commands.RoleNotFound:
            pass

        roles = ctx.guild.roles
        role_name = fuzzy_search(argument, [role.name for role in roles])
        result = discord.utils.get(roles, name=role_name)

        if result is None:
            raise commands.RoleNotFound(argument)
        return result


class RoleGroupStates:
    normal = "normal"
    empty = "does not have any assigned roles"
    archived = "all roles are archived"


class Roles(commands.Cog):
    """Roles management commands"""

    # :griffin_hug:
    def __init__(self, bot):
        self.bot = bot

    async def update_guilds_roles(self):
        db_roles = await Role.all().values("name", "color", "archived")
        roles_dict = {role['name']: role['color'] for role in db_roles if not role['archived']}
        to_remove = set(role['name'] for role in db_roles if role['archived'])

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

    @staticmethod
    def get_role_repr(ctx, role_name):
        role = discord.utils.get(ctx.guild.roles, name=role_name)
        return role.mention if role is not None else f"**{role_name}** *(not available yet on this server)*"

    @staticmethod
    async def get_group_state(group):
        roles = await Role.filter(group=group).values_list("archived", flat=True)
        if not roles:
            return RoleGroupStates.empty
        if False not in roles:
            return RoleGroupStates.archived
        return RoleGroupStates.normal

    @commands.group(aliases=["roles", "r"], case_insensitive=True, )
    async def role(self, ctx):
        """Role management commands, shows you your roles. You can use mentions instead of role names"""
        if ctx.invoked_subcommand is not None:
            return

        await ctx.send(f"Your roles are: {', '.join([role.mention for role in ctx.author.roles[1:]])}. "
                       f"To view available roles, use !role list", allowed_mentions=discord.AllowedMentions.none())

    @role.command(aliases=["all", "available", "view", ])
    async def list(self, ctx):
        # db_roles = await Role.exclude(archived=True).values_list("name", flat=True)
        # embed = discord.Embed(title="Available roles:", description="Desc")
        # embed.add_field(name="Gender", value="\n".join([self.get_role_repr(role) for role in db_roles]), inline=False)
        # await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

        embed = discord.Embed(title="Available roles:", color=0x72a3f2)
        db_groups = await RoleGroup.all()
        for group in db_groups:
            if await self.get_group_state(group) != RoleGroupStates.normal:
                continue
            embed.add_field(name=group.name, value="hi", inline=True)
        if not embed.fields:
            embed.description = "Woe is me, there are no roles!"

        await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        # TODO
        # await ctx.send("Available roles: \n" + "\n".join(
        #                [get_role_repr(role) for role in db_roles]),
        #                allowed_mentions=discord.AllowedMentions.none())
        # # print(*[(role.name, role.position) for role in ctx.guild.roles], sep="\n")

    @role.group(name="group", aliases=["groups", "role_group", "g"], invoke_without_command=True)
    async def role_group(self, ctx, *, group: RoleGroupConverter()=None):
        """Shows current role groups"""
        if ctx.invoked_subcommand is not None:
            return

        #TODO

    @role_group.command(name="list", aliases=["all", "available", "view", ])
    async def group_list(self, ctx):
        async def display_group(group):
            state = await self.get_group_state(group)
            name = f"**{group.name}**"
            if state == RoleGroupStates.normal:
                return name
            return f"{name} *({state})*"

        db_groups = await RoleGroup.all()
        await ctx.send("Available role groups (by internal DB): \n" +
                       "\n".join([await display_group(group) for group in db_groups]))

    @role_group.command(name="new", aliases=["add", "+"])
    @has_bot_perms()
    async def new_group(self, ctx, name, exclusive: typing.Optional[bool] = False):
        """Adds new role group to internal DB"""
        if await RoleGroup.exists(name=name):
            raise commands.BadArgument(f"Role group **{name}** already exists!")

        await RoleGroup.create(name=name, exclusive=exclusive)
        await ctx.send(f"Successfully added role group **{name}** *(exclusive={exclusive})*")

    @role_group.command(name="remove", aliases=["delete", "-"])
    @has_bot_perms()
    async def remove_group(self, ctx, group: RoleGroupConverter()):
        """Removes specified group from internal DB"""
        name = group.name
        # print(await group.roles)
        await group.delete()
        # logger.warning(f"User {ctx.author.name}/{ctx.author.} (from {ctx.guild}) removed role group {name}")
        await ctx.send(f"Successfully removed role group **{name}**")

    @role_group.command(name="edit", aliases=["update", ])
    @has_bot_perms()
    async def edit_group(self, ctx, group: RoleGroupConverter(), *, params):
        """
        Edits specified role group
        Specify params like this: name="New name" exclusive=False
        Use quotation for space-separated names\strings
        """
        params_dict = parse_params(params)
        kwargs = dict()
        if "name" in params_dict:
            name = params_dict["name"]
            if await RoleGroup.exists(name=name):
                raise commands.BadArgument(f"Role group **{name}** already exists!")
            kwargs["name"] = name
        if "exclusive" in params_dict:
            exclusive = convert_to_bool(params_dict["exclusive"])
            kwargs["exclusive"] = exclusive
        if not kwargs:
            raise commands.BadArgument("No valid arguments were included")

        old_name = group.name
        group = group.update_from_dict(kwargs)
        await group.save(update_fields=kwargs.keys())
        await ctx.send(f"Updated role group **{old_name}** with new parameters: "
                       f"*{', '.join([f'{key}={value}' for key, value in kwargs.items()])}*")

    @role.command(aliases=["join", "assign", ])
    @commands.guild_only()
    async def add(self, ctx, roles: commands.Greedy[DiscordRoleConverter], *,
                  member: MemberConverter = None):
        """Add (assign) specified role(s) to you or mentioned member"""
        if not roles:
            raise commands.BadArgument("No valid roles vere given!")

        if isinstance(member, InvalidData):
            error_message = f"{member[0]} is not a valid role" if len(member) > 1 else f"{member[0]} is not a valid user or role"
            raise commands.BadArgument(error_message)

        if member is not None and not has_server_perms():
            raise commands.MissingPermissions("Insufficient permissions for editing other users roles")

        role_names = [role.name for role in roles]
        if "Bot manager" in role_names and not has_server_perms():
            raise commands.MissingPermissions("Insufficient permissions to assign the bot management role")

        member = member or ctx.author

        # todo group check

        await member.add_roles(*roles)
        await ctx.send(f"Added {'role' if len(roles) == 1 else 'roles'} "
                       f"{', '.join([role.mention for role in roles])} "
                       f"to {member.mention}",
                       allowed_mentions=discord.AllowedMentions.none()
                       )

    @role.command(aliases=["leave", "clear", "delete", "yeet", ])
    @commands.guild_only()
    async def remove(self, ctx, roles: commands.Greedy[DiscordRoleConverter],
                     member: MemberConverter = None):
        """Remove specified role(s) from you or mentioned member"""
        if not roles:
            raise commands.BadArgument("No valid roles vere given!")

        if isinstance(member, InvalidData):
            error_message = f"{member[0]} is not a valid role" if len(member) > 1 else f"{member[0]} is not a valid user or role"
            raise commands.BadArgument(error_message)
        
        if member is not None and not has_server_perms():
            raise commands.MissingPermissions("Insufficient permissions for editing other users roles")

        member = member or ctx.author
        await member.remove_roles(*roles)
        await ctx.send(f"Removed {'role' if len(roles) == 1 else 'roles'} "
                       f"{', '.join([role.mention for role in roles])} "
                       f"from {member.mention}",
                       allowed_mentions=discord.AllowedMentions.none()
                       )
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
        if await Role.exists(name=name):
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
        # await member.remove_roles(*roles)
        # ctx.author.add_roles(role)
