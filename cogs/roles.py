import logging

import discord
from discord.ext import commands

import tortoise
from tortoise.models import Model
from tortoise import fields
from tortoise.functions import Max
from tortoise.transactions import atomic

import asyncio
import typing

from cogs.permissions import has_server_perms, has_bot_perms
from cogs.cog_utils import fuzzy_search
from cogs.db_utils import reshuffle
from cogs.param_coverter import ParamConverter, ColorValueConverter, \
    convert_to_bool, convert_to_int, convert_ensure_unique


logger = logging.getLogger(__name__)


def next_number(cls_name, field="number"):
    def inner():
        loop = asyncio.get_event_loop()
        cls = globals()[cls_name]
        max_number = loop.run_until_complete(cls.annotate(m=Max(field)).values_list("m", flat=True))[0]
        return max_number + 1 if max_number is not None else 0
    return inner


# def next_role_number():
#     loop = asyncio.get_event_loop()
#     max_number = loop.run_until_complete(Role.annotate(m=Max("number")).values_list("m", flat=True))[0]
#     return max_number + 1 if max_number is not None else 0


class Role(Model):
    id = fields.IntField(pk=True)
    name = fields.TextField()
    color = fields.IntField()
    number = fields.IntField(default=next_number("Role"))
    archived = fields.BooleanField(default=False)
    group = fields.ForeignKeyField("models.RoleGroup", related_name="roles")

    class Meta:
        ordering = ["number"]

    def __str__(self):
        return self.name


class RoleGroup(Model):
    id = fields.IntField(pk=True)
    name = fields.TextField()
    number = fields.IntField(default=next_number("RoleGroup"))
    # archived = fields.BooleanField(default=False)
    exclusive = fields.BooleanField(default=True)
    roles: fields.ReverseRelation["Role"]

    class Meta:
        ordering = ["number"]

    def __str__(self):
        return self.name


class RoleGroupConverter(commands.Converter):
    async def convert(self, ctx, argument):
        group_name = fuzzy_search(argument, await RoleGroup.all().values_list("name", flat=True))
        if group_name is None:
            raise commands.BadArgument(f"Sorry, I cant find role group **{argument}**. "
                                       f"Try *!role groups* command to see available groups")
        return await RoleGroup.get(name=group_name)


class InvalidData:
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


class DiscordRoleConverter(commands.RoleConverter):
    async def convert(self, ctx, argument):
        try:
            result = await super().convert(ctx, argument)
        except commands.RoleNotFound:
            roles = ctx.guild.roles
            role_name = fuzzy_search(argument, [role.name for role in roles], score_cutoff=70)
            result = discord.utils.get(roles, name=role_name)

            if result is None:
                raise commands.RoleNotFound(argument)

        if not await Role.exists(archived=False, name=result):
            raise commands.BadArgument(f"Role {result} is not in internal roles DB")

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
        db_roles = await Role.exclude(archived=True)
        to_remove = await Role.filter(archived=True).values_list("name", flat=True)

        for guild in self.bot.guilds:
            position = guild.me.top_role.position
            for db_role in db_roles:
                role = discord.utils.get(guild.roles, name=db_role.name)
                position -= 1
                try:
                    if role is not None:
                        await role.edit(colour=db_role.color, mentionable=True, position=position)
                    else:
                        role = await guild.create_role(name=db_role.name, colour=db_role.color, mentionable=True)
                        await role.edit(position=position)
                except discord.errors.Forbidden:
                    logger.warning(f"Can't setup role {db_role.name} at {guild.name}")

            for name in to_remove:
                role = discord.utils.get(guild.roles, name=name)
                if role is not None:
                    try:
                        await role.delete()
                    except discord.errors.Forbidden:
                        logger.warning(f"Can't delete role {name} at {guild.name}")

    async def rename_guilds_roles(self, old_name, name):
        for guild in self.bot.guilds:
            role = discord.utils.get(guild.roles, name=old_name)
            if role is not None:
                try:
                    await role.edit(name=name)
                except discord.errors.Forbidden:
                    logger.warning(f"Can't rename role {name} at {guild.name}")

    @staticmethod
    async def remove_conflicting_roles(ctx, member: discord.Member, group):
        db_roles = await group.roles
        roles = (discord.utils.get(ctx.guild.roles, name=db_role.name) for db_role in db_roles if not db_role.archived)
        roles = [role for role in roles if role is not None]
        await member.remove_roles(*roles)

    @staticmethod
    def get_role_repr(ctx, role_name):
        role = discord.utils.get(ctx.guild.roles, name=role_name) if ctx.guild is not None else None
        return role.mention if role is not None else f"**{role_name}** *(not available yet on this server)*"

    @staticmethod
    async def get_group_state(group):
        roles = await Role.filter(group=group).values_list("archived", flat=True)
        if not roles:
            return RoleGroupStates.empty
        if False not in roles:
            return RoleGroupStates.archived
        return RoleGroupStates.normal

    @commands.group(aliases=["roles", "r"], case_insensitive=True, invoke_without_command=True)
    async def role(self, ctx, member: typing.Optional[discord.Member]=None):
        """Role management commands, main command shows you your roles. You can use mentions instead of role names"""
        if ctx.invoked_subcommand is not None:
            return

        member = member or ctx.author
        mention = f"{member.mention}" if member != ctx.author else "You"
        if len(member.roles) <= 1:
            role_text = "don't have any roles"
        else:
            mentions = [role.mention for role in member.roles[1:]]
            role_text = f"have following roles:  {', '.join(mentions)}"

        await ctx.send(f"{mention} {role_text}. To view available roles, use !role list",
                       allowed_mentions=discord.AllowedMentions.none())

    @role.group(name="list", aliases=["all", "available", "view", ])
    async def role_list(self, ctx):
        """
        Shows list of all available roles
        Roles are grouped by role group
        """
        if ctx.invoked_subcommand is not None:
            return

        embed = discord.Embed(title="Available roles:", color=0x72a3f2)
        db_groups = await RoleGroup.all()
        for group in db_groups:
            if await self.get_group_state(group) != RoleGroupStates.normal:
                continue
            db_roles = await Role.filter(group=group, archived=False).values_list("name", flat=True)
            role_mentions = [self.get_role_repr(ctx, role) for role in db_roles]
            embed.add_field(name=group.name, value="\n".join(role_mentions), inline=True)
        if not embed.fields:
            embed.description = "Woe is me, there are no roles!"

        await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    @role_list.command(name="db")
    async def list_db(self, ctx):
        """Shows list of roles in internal DB with additional data"""
        db_roles = await Role.all()
        role_mentions = [f"{self.get_role_repr(ctx, role.name)} "
                         f"*(color={discord.Color(role.color)}, archived={role.archived}, "
                         f"priority={role.number}, group={(await role.group).name})*" for role in db_roles]
        await ctx.send("All roles in DB: \n" + "\n".join(role_mentions),
                       allowed_mentions=discord.AllowedMentions.none())

    @role.group(name="group", aliases=["groups", "role_group", "g"], invoke_without_command=True)
    async def role_group(self, ctx, *, group: RoleGroupConverter() = None):
        """Shows list of role groups or info about specified group"""
        if ctx.invoked_subcommand is not None:
            return
        if group is None:
            await self.group_list.invoke(ctx)
            return

        embed = discord.Embed(title=group.name, color=0x72a3f2)
        db_roles = await group.roles

        role_mentions = [self.get_role_repr(ctx, role.name) if not role.archived else f"**{role.name}** (archived)"
                         for role in db_roles]
        if role_mentions:
            embed.description = "Available roles: " + "\n".join(role_mentions)
        else:
            embed.description = "Woe is me, there are no roles in this group!"

        await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    @role_group.command(name="list", aliases=["all", "available", "view", "db", ])
    async def group_list(self, ctx):
        """Shows a list of role groups with additional data"""
        async def display_group(group):
            state = await self.get_group_state(group)
            name = f"**{group.name}**"
            if state == RoleGroupStates.normal:
                role_count = len(await group.roles)
                state = f"has {role_count} assigned role{'s' if role_count>1 else ''}"
            return f"{name} *({state}, exclusive={group.exclusive})*"

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
        if self.get_group_state(group) not in (RoleGroupStates.archived, RoleGroupStates.empty):
            raise commands.BadArgument(f"Cannot delete role group **{name}**! "
                                       f"Archive all roles in the role group or move them to other groups!")
        await group.delete()
        # logger.warning(f"User {ctx.author.name}/{ctx.author.} (from {ctx.guild}) removed role group {name}")
        await ctx.send(f"Successfully removed role group **{name}**")

    @role_group.command(name="edit", aliases=["update", ])
    @has_bot_perms()
    @atomic()
    async def edit_group(self, ctx, group: RoleGroupConverter(), *, params):
        """
        Edits specified role group
        Specify params like this: name="New name" priority=1 exclusive=False archived=False
        You are free to use any combinations of params
        Use quotation for space-separated names\strings
        """
        converter = ParamConverter({"name": convert_ensure_unique(RoleGroup), "priority": convert_to_int,
                                    "archived": convert_to_bool, "exclusive": convert_to_bool})
        converted = await converter.convert(ctx, params)
        converted_msg = converted.copy()

        if "priority" in converted:
            priority = converted.pop("priority")
            await reshuffle(RoleGroup, group, priority)
            converted["number"] = priority

        if "archived" in converted:
            archived = converted.pop("archived")
            await Role.filter(group=group).update(archived=archived)
            await self.update_guilds_roles()

        old_name = group.name
        group = group.update_from_dict(converted)
        await group.save()
        await ctx.send(f"Updated role group **{old_name}** with new parameters: "
                       f"*{', '.join([f'{key}={value}' for key, value in converted_msg.items()])}*")

    @role.command(aliases=["join", "assign", ])
    @commands.guild_only()
    async def add(self, ctx, roles: commands.Greedy[DiscordRoleConverter], *,
                  member: MemberConverter = None):
        """Add (assign) specified role(s) to you or mentioned member"""
        if not roles:
            raise commands.BadArgument("No valid roles vere given!")

        if isinstance(member, InvalidData):
            error_message = f"{member[0]} is not a valid role" if len(member) > 1 else \
                f"{member[0]} is not a valid user or role"
            raise commands.BadArgument(error_message)

        if member is not None and not has_server_perms():
            raise commands.MissingPermissions("Insufficient permissions for editing other users roles")

        role_names = [role.name for role in roles]
        if "Bot manager" in role_names and not has_server_perms():
            raise commands.MissingPermissions("Insufficient permissions to assign the bot management role")
        member = member or ctx.author

        # for group in set((await Role.get(name=role_name)).group for role_name in role_names):
        async for group in RoleGroup.filter(roles__name__in=role_names).distinct():
            if group.exclusive:
                await self.remove_conflicting_roles(ctx, member, group)

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
            error_message = f"{member[0]} is not a valid role" if len(member) > 1 else \
                f"{member[0]} is not a valid user or role"
            raise commands.BadArgument(error_message)

        if member is not None and not has_server_perms():
            raise commands.MissingPermissions("Insufficient permissions for editing other users roles")

        member = member or ctx.author
        await member.remove_roles(*roles)
        mentions = [role.mention for role in roles]
        await ctx.send(f"Removed {'role' if len(roles) == 1 else 'roles'} "
                       f"{', '.join(mentions)} from {member.mention}",
                       allowed_mentions=discord.AllowedMentions.none()
                       )

    @role.command()
    @has_bot_perms()
    async def new(self, ctx, name: str, group: RoleGroupConverter(),
                  color: typing.Optional[discord.Colour] = discord.Color(0x000000)):
        """Adds new role to internal DB and discord servers"""
        if await Role.exists(name=name):
            raise commands.BadArgument(f"Role '{name}' already exists")

        await Role.create(name=name, color=color.value, group=group)
        await self.update_guilds_roles()

        role = discord.utils.get(ctx.guild.roles, name=name)
        await ctx.send(f"Created role {role.mention}", allowed_mentions=discord.AllowedMentions.none())

    @role.command(aliases=["refresh", "setup", ])
    @has_bot_perms()
    async def update(self, ctx):
        """Updates roles on connected discord servers according to internal DB"""
        await self.update_guilds_roles()
        await ctx.send("Updated all guilds roles in accordance with internal DB")

    @role.command()
    @has_bot_perms()
    @atomic()
    async def edit(self, ctx, role: DBRoleConverter(), *, params):
        """
        Edits specified role
        Specify params like this: name="New name" color=#00FF00 group="Group name" priority=3 archived=False
        You are free to use any combinations of params
        Use quotation for space-separated names\strings
        """
        converter = ParamConverter({"name": convert_ensure_unique(Role),
                                    "color": ColorValueConverter(), "group": RoleGroupConverter(),
                                    "priority": convert_to_int, "archived": convert_to_bool})
        converted = await converter.convert(ctx, params)
        converted_msg = converted.copy()

        if "priority" in converted:
            number = converted.pop("priority")
            await reshuffle(Role, role, number)
            converted["number"] = number

        old_name = role.name
        role = role.update_from_dict(converted)
        await role.save()

        if "name" in converted:
            await self.rename_guilds_roles(old_name, role.name)
        await self.update_guilds_roles()

        await ctx.send(f"Updated role {self.get_role_repr(ctx, role.name)} with new parameters: "
                       f"*{', '.join([f'{key}={value}' for key, value in converted_msg.items()])}*",
                       allowed_mentions=discord.AllowedMentions.none())

    @role.command()
    @has_bot_perms()
    async def archive(self, ctx, role: DBRoleConverter(), archive: typing.Optional[bool]=True):
        """Archives role to server network database, deletes it from discord servers"""
        if role.name == "Bot manager" and archive:
            raise commands.MissingPermissions("I wont delete the bot management role lmao")

        role.archived = archive
        await role.save()
        await self.update_guilds_roles()
        await ctx.send(f"Archived role **{role.name}**")

    @commands.command(aliases=["sider", ])
    async def side(self, ctx, *, role_arg: str):
        """Choose your Sider role!"""
        try:
            group = await RoleGroup.get(name="Sider")
        except tortoise.exceptions.DoesNotExist:
            raise commands.CheckFailure("Sorry, but this command is unavailable as there is no **Sider** role group.")

        member = ctx.author

        no_options = ["leave", "clear", "delete", "remove", "yeet", "no", "none", "noside", "nosider", "human"]
        options = await Role.filter(group=group, archived=False).values_list("name", flat=True)
        options.extend(no_options)
        pick = fuzzy_search(role_arg, options)

        if pick in no_options:
            await self.remove_conflicting_roles(ctx, member, group)
            await ctx.send("You're a *noside* now! Is that what you wanted?")
            return

        db_role = await DBRoleConverter().convert(ctx, pick)
        if (await db_role.group).pk != group.pk:
            raise commands.CheckFailure("Sorry, but this is not **Sider** role.")

        previous_roles = await Role.filter(group=group, name__in=[role.name for role in member.roles]).\
            values_list("name", flat=True)
        await self.remove_conflicting_roles(ctx, member, group)

        role = await DiscordRoleConverter().convert(ctx, db_role.name)
        await member.add_roles(role)

        if "Nixside" in previous_roles and role.name == "Drakeside":
            await ctx.send("You know, Ziva, you can't really learn drakeside boxsignal this way!")
        elif role.name == "Simurgh":
            await ctx.send("THE SIMURGH\nIS HERE")
        elif role.name == "Spaceside":
            await ctx.send("/-//-/- /-//-/ //-/-/-/-")
        elif role.name == "Zalside":
            await ctx.send("Splish-splash!")
        else:
            await ctx.send(f"You're a **{role.name}** now!")
