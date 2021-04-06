import logging

import discord
from discord.ext import commands
from discord_slash import cog_ext, SlashContext
from discord_slash.utils.manage_commands import create_option, create_choice

import tortoise
from tortoise import fields
from tortoise import exceptions
from tortoise.models import Model
from tortoise.transactions import atomic

import asyncio
import typing

import cogs.cog_utils as utils
import cogs.db_utils as db_utils
from cogs.permissions import has_server_perms, has_bot_perms

logger = logging.getLogger(__name__)

role_number = db_utils.NextNumber()
group_number = db_utils.NextNumber()

guild_ids = [570257083040137237, 568072142843936778]


class Role(Model):
    id = fields.IntField(pk=True)
    name = fields.CharField(max_length=250, unique=True, description="Name of the role")
    color = db_utils.ColorField(default=discord.Colour(0), description="Color of the role")
    number = fields.IntField(default=role_number, description="Priority (ordering) of the role")  # default=role_number,
    archived = fields.BooleanField(default=False,
                                   description="Archived roles remain in internal DB but removed from discord servers")
    assignable = fields.BooleanField(default=True,
                                     description="Whether role can be assigned by regular member to themselves")
    mentionable = fields.BooleanField(default=True, description="Whether people can mention this role")
    group = fields.ForeignKeyField("models.RoleGroup", related_name="roles",
                                   description="Role group this role belongs to")

    class Meta:
        ordering = ["number"]

    def __str__(self):
        return self.name


class RoleGroup(Model):
    id = fields.IntField(pk=True)
    name = fields.CharField(max_length=250, unique=True, description="Name of the group")
    number = fields.IntField(default=group_number,
                             description="Priority (ordering) of the group")  # default=group_number,
    exclusive = fields.BooleanField(default=False,
                                    description="Whether users can have only one role from this group at time")
    roles: fields.ReverseRelation["Role"]

    class Meta:
        ordering = ["number"]
        use_choices = True

    def __str__(self):
        return self.name


role_number.set_model(Role)
group_number.set_model(RoleGroup)
fk_dict = {"group": RoleGroup, "role": Role}


class RoleGroupStates:
    normal = "normal"
    empty = "does not have any assigned roles"
    archived = "all roles are archived"


class Roles(utils.StartupCog):
    """Roles management commands"""

    def __init__(self, bot):
        super().__init__()
        self.bot = bot
        self._sider_group = None

    async def on_startup(self):
        await self.update_options()

    async def update_options(self):

        self.group_add.options = await db_utils.generate_db_options(RoleGroup)
        self.group_edit.options = await db_utils.generate_db_options(RoleGroup, edit="group")

        self.role_add.options = await db_utils.generate_db_options(Role)
        self.role_edit.options = await db_utils.generate_db_options(Role, edit="role")

        group_choices = [create_choice(name=group.name, value=group.id) for group in await RoleGroup.all()][:25]
        group_commands = [self.group_info, self.group_remove, self.group_archive]
        for command in group_commands:
            command.options[0]["choices"] = group_choices

        if self._sider_group is None:
            self._sider_group = await RoleGroup.get_or_none(name="Sider")
        if self._sider_group is not None:
            sider_choices = [create_choice(name=role.name, value=role.id)
                             for role in await Role.filter(group=self._sider_group)][:24]
            sider_choices.insert(0, create_choice(name="Noside", value=-1))
            self.side_join.options[0]["choices"] = sider_choices

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
                        await role.edit(colour=db_role.color, mentionable=db_role.mentionable, position=position)
                    else:
                        role = await guild.create_role(name=db_role.name, colour=db_role.color,
                                                       mentionable=db_role.mentionable)
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

    @cog_ext.cog_subcommand(base="role", name="check",
                            options=[
                                create_option(
                                    name="member",
                                    description="Member to check roles (or yourself if empty)",
                                    option_type=discord.Member,
                                    required=False,
                                ),
                            ],
                            guild_ids=guild_ids)
    async def role_check(self, ctx: SlashContext, member: discord.Member = None):
        """Shows your (or specified users) roles"""

        member = member or ctx.author
        mention = member.mention if member != ctx.author else "You"

        logger.debug(f"{ctx.author} checked {member} roles")

        if len(member.roles) <= 1:
            role_text = "don't have any roles"
        else:
            mentions = [role.mention for role in member.roles[1:]]
            role_text = f"have following roles:  {', '.join(mentions)}"

        await ctx.send(f"{mention} {role_text}. To view available roles, use /role list",
                       allowed_mentions=discord.AllowedMentions.none(),
                       hidden=True)

    @cog_ext.cog_subcommand(base="role", name="list", guild_ids=guild_ids)
    async def role_list(self, ctx: SlashContext):
        """Shows list of all available roles. Roles are grouped by role group"""
        logger.debug(f"{ctx.author} listed roles")

        embed = discord.Embed(title="Available roles:", color=utils.embed_color)
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

    @cog_ext.cog_subcommand(base="role", name="database", guild_ids=guild_ids)
    @has_server_perms()
    async def list_db(self, ctx: SlashContext):
        """Shows list of roles in internal DB with additional data"""
        logger.debug(f"{ctx.author} checked roles database")
        
        db_roles = await Role.all()
        role_mentions = [f"{self.get_role_repr(ctx, role.name)} "
                         f"*(color={role.color}, archived={role.archived}, "
                         f"priority={role.number}, group={(await role.group).name})*" for role in db_roles]
        await ctx.send("All roles in DB: \n" + "\n".join(role_mentions),
                       allowed_mentions=discord.AllowedMentions.none(),
                       hidden=True)

    @cog_ext.cog_subcommand(base="role", subcommand_group="group", name="info",
                            options=[
                                create_option(
                                    name="group",
                                    description="Group to get info about",
                                    option_type=int,
                                    required=True,
                                ),
                            ],
                            guild_ids=guild_ids)
    async def group_info(self, ctx: SlashContext, group):
        """Shows info about specified group"""
        logger.debug(f"{ctx.author} checked group with ID '{group}'")
        
        group = await RoleGroup.get(id=group)
        logger.debug(f"Group '{group.id}' is '{group.name}'")

        embed = discord.Embed(title=group.name, color=utils.embed_color)
        db_roles = await group.roles

        role_mentions = [self.get_role_repr(ctx, role.name) if not role.archived else f"**{role.name}** (archived)"
                         for role in db_roles]
        if role_mentions:
            embed.add_field(name="Available roles", value="\n".join(role_mentions))
        else:
            embed.description = "Woe is me, there are no roles in this group!"

        await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    @cog_ext.cog_subcommand(base="role", subcommand_group="group", name="list", guild_ids=guild_ids)
    async def group_list(self, ctx: SlashContext):
        """Shows a list of role groups with additional data"""
        logger.debug(f"{ctx.author} listed role groups")

        async def display_group(group):
            state = await self.get_group_state(group)
            name = f"**{group.name}**"
            if state == RoleGroupStates.normal:
                role_count = len(await group.roles)
                state = f"has {role_count} assigned role{'s' if role_count > 1 else ''}"
            return f"{name} *({state}, exclusive={group.exclusive})*"

        db_groups = await RoleGroup.all()
        await ctx.send("Available role groups (by internal DB): \n" +
                       "\n".join([await display_group(group) for group in db_groups]),
                       hidden=True)

    @cog_ext.cog_subcommand(base="role", subcommand_group="group", name="add", guild_ids=guild_ids)
    @has_bot_perms()
    async def group_add(self, ctx: SlashContext, *args, **params):
        """Adds new role group to internal DB"""
        logger.db(f"{ctx.author} trying to add group with args '{args}' and params '{params}'")

        # params = utils.convert_args(self.group_add, args, params)
        params = await db_utils.process(RoleGroup, params, fk_dict)
        instance = await RoleGroup.create(**params)
        logger.db(f"{ctx.author} added group '{instance.name}'")

        await ctx.send(f"Successfully added role group **{instance.name}** *(exclusive={instance.exclusive})*",
                       hidden=True)
        await self.update_options()

    @cog_ext.cog_subcommand(base="role", subcommand_group="group", name="delete",
                            options=[
                                create_option(
                                    name="group",
                                    description="Group to delete",
                                    option_type=int,
                                    required=True,
                                ),
                            ],
                            guild_ids=guild_ids)
    @has_bot_perms()
    async def group_remove(self, ctx: SlashContext, group):
        """Removes specified group from internal DB"""
        logger.db(f"{ctx.author} trying to remove group with ID '{group}'")

        group = await RoleGroup.get(id=group)
        name = group.name
        logger.db(f"Group {group.id} is '{name}'")

        if await self.get_group_state(group) not in (RoleGroupStates.archived, RoleGroupStates.empty):
            logger.db(f"Failed to remove group {name}")
            raise commands.BadArgument(f"Cannot delete role group **{name}**! "
                                       f"Archive all roles in the role group or move them to other groups!")
        await group.delete()
        logger.db(f"{ctx.author} (from {ctx.guild}) removed role group {name}")
        await ctx.send(f"Successfully removed role group **{name}**", hidden=True)
        await self.update_options()

    @cog_ext.cog_subcommand(base="role", subcommand_group="group", name="edit", guild_ids=guild_ids)
    @has_bot_perms()
    @atomic()
    async def group_edit(self, ctx: SlashContext, *args, **params):
        """Edits specified role group"""
        logger.db(f"{ctx.author} trying to edit group with args '{args}' and params '{params}")

        params = await db_utils.process(RoleGroup, params, fk_dict)
        group = params.pop("group")

        old_name = group.name
        logger.db(f"Group {group.id} is '{old_name}'")
        
        group = group.update_from_dict(params)
        await group.save()
        logger.db(f"Edited group {old_name} (args '{args}', params '{params}')")

        await ctx.send(f"Updated role group **{old_name}** with new parameters: "
                       f"{utils.format_params(params)}",
                       hidden=True)
        await self.update_options()

    @cog_ext.cog_subcommand(base="role", subcommand_group="group", name="archive",
                            options=[
                                create_option(
                                    name="group",
                                    description="Group to archive/unarchive",
                                    option_type=int,
                                    required=True,
                                ),
                                create_option(
                                    name="action",
                                    description="Whether to archive/unarchive role group",
                                    option_type=int,
                                    required=False,
                                    choices=[create_choice(name="archive", value=int(True)),
                                             create_choice(name="unarchive", value=int(False))]
                                )
                            ],
                            guild_ids=guild_ids)
    @has_bot_perms()
    async def group_archive(self, ctx: SlashContext, group, archive: bool = True):
        """Archives or unarchives all roles in specified group"""
        logger.db(f"{ctx.author} trying to archive group with ID '{group}'")

        await ctx.defer(True)
        group = await RoleGroup.get(id=group)
        archive = bool(archive)
        logger.db(f"Group {group.id} is '{group.name}'")

        if archive and utils.bot_manager_role in [role.name for role in await group.roles]:
            logger.warn(f"Failed to archive group '{group.name}'")
            raise commands.BadArgument(f"Cannot archive role group **{group.name}**! Nope!")

        await Role.filter(group=group).update(archived=archive)
        await self.update_guilds_roles()
        action = "Archived" if archive else "Unarchived"
        logger.db(f"Successfully {action.lower()} role group {group.name}")
        await ctx.send(f"{action} role group **{group.name}** and all roles in that group", hidden=True)

    @cog_ext.cog_subcommand(base="role", name="assign",
                            options=[
                                create_option(
                                    name="role",
                                    description="Role to assign",
                                    option_type=discord.Role,
                                    required=True,
                                ),
                                create_option(
                                    name="member",
                                    description="Member to assign role to",
                                    option_type=discord.Member,
                                    required=False,
                                )
                            ],
                            guild_ids=guild_ids)
    @commands.guild_only()
    async def role_assign(self, ctx: SlashContext, role: discord.Role, member: discord.Member = None):
        """Assign specified role to you or specified member"""
        member = member or ctx.author
        logger.info(f"{ctx.author} trying to assign '{role}' to {member}")

        try:
            db_role = await Role.get(name=role.name)
        except exceptions.DoesNotExist:
            logger.warn(f"Role with name '{role}'' not found in database")
            raise commands.BadArgument(f"Role {role.mention} is not in bots database! "
                                       f"You probably shouldn't use that role")

        if (member != ctx.author or db_role.name == utils.bot_manager_role or not db_role.assignable) \
                and not has_server_perms():
            # MissingPermissions expects an array of permissions
            logger.info(f"{ctx.author} don't have permissions to assign '{role}' to {member}")
            raise commands.MissingPermissions([utils.bot_manager_role])

        group = await db_role.group
        if group.exclusive:
            logger.info(f"Removing roles, conflicting with {role}")
            await self.remove_conflicting_roles(ctx, member, group)

        await member.add_roles(role)
        logger.info(f"Assigned role '{role}' to {member}")
        await ctx.send(f"Added role {role.mention} to {member.mention}",
                       allowed_mentions=discord.AllowedMentions.none(),
                       hidden=True
                       )

    @cog_ext.cog_subcommand(base="role", name="unassign",
                            options=[
                                create_option(
                                    name="role",
                                    description="Role to remove",
                                    option_type=discord.Role,
                                    required=True,
                                ),
                                create_option(
                                    name="member",
                                    description="Member to remove role from",
                                    option_type=discord.Member,
                                    required=False,
                                )
                            ],
                            guild_ids=guild_ids)
    async def role_unassign(self, ctx: SlashContext, role: discord.Role, member: discord.Member = None):
        """Remove specified role from you or specified member"""
        member = member or ctx.author
        logger.info(f"{ctx.author} trying to remove role '{role}' from {member}")

        try:
            db_role = await Role.get(name=role.name)
        except exceptions.DoesNotExist:
            logger.warn(f"Role with name '{role.name}' not found in database")
            raise commands.BadArgument(f"Role {role.mention} is not in bots database!"
                                       f"You probably shouldn't touch that role")

        if (member != ctx.author or not db_role.assignable) and not has_server_perms():
            logger.info(f"{ctx.author} don't have permissions to remove '{role}' from {member}")
            # MissingPermissions expects an array of permissions
            raise commands.MissingPermissions([utils.bot_manager_role])

        await member.remove_roles(role)
        logger.info(f"Removed role '{role}' from {member}")
        await ctx.send(f"Removed role {role.mention} from {member.mention}",
                       allowed_mentions=discord.AllowedMentions.none(),
                       hidden=True
                       )

    @cog_ext.cog_subcommand(base="role", name="add", guild_ids=guild_ids)
    @has_bot_perms()
    async def role_add(self, ctx: SlashContext, *args, **params):
        """Adds new role to internal DB and discord servers"""
        logger.db(f"{ctx.author} trying to add role with args '{args}' and params '{params}'")

        await ctx.defer(hidden=True)
        params = await db_utils.process(Role, params, fk_dict)
        instance = await Role.create(**params)
        await self.update_guilds_roles()

        logger.db(f"{ctx.author} added role '{instance.name}' with args '{args}' and params '{params}'")
        await ctx.send(f"Created role {self.get_role_repr(ctx, params['name'])} *(assignable={instance.assignable})*",
                       allowed_mentions=discord.AllowedMentions.none(),
                       hidden=True)

        await self.update_options()

    @cog_ext.cog_subcommand(base="role", name="delete",
                            options=[
                                create_option(
                                    name="role",
                                    description="Role name to delete",
                                    option_type=str,
                                    required=True,
                                ),
                            ],
                            guild_ids=guild_ids)
    @has_bot_perms()
    async def role_delete(self, ctx: SlashContext, role):
        """Completely removes role from internal DB"""
        logger.db(f"{ctx.author} trying to remove role '{role}'")
        
        role = (await db_utils.process(Role, {"role": role}, fk_dict))["role"]
        if not role.archived:
            logger.warn(f"Can't remove role '{role.name}', it is not archived!")
            raise commands.BadArgument("Role must be archived to be removed from internal DB")

        await role.delete()
        logger.db(f"{ctx.author} removed role '{role.name}'")
        await ctx.send(f"Successfully removed **{role.name}** from internal DB", hidden=True)
        await self.update_options()

    @cog_ext.cog_subcommand(base="role", name="sync", guild_ids=guild_ids)
    @has_bot_perms()
    async def role_sync(self, ctx: SlashContext):
        """Updates roles and command options on connected discord servers according to internal DB"""
        logger.debug(f"{ctx.author} updated roles and options")
        await ctx.defer(hidden=True)
        await self.update_guilds_roles()
        await self.update_options()
        await ctx.send("Updated all guilds roles in accordance with internal DB", hidden=True)

    @cog_ext.cog_subcommand(base="role", name="edit", guild_ids=guild_ids)
    @has_bot_perms()
    @atomic()
    async def role_edit(self, ctx: SlashContext, *args, **params):
        """Edits specified role"""
        logger.db(f"{ctx.author} trying to edit role with args '{args}' and params '{params}'")
        
        # await ctx.defer(hidden=True)
        params = await db_utils.process(Role, params, fk_dict)
        role = params.pop("role")

        if "archived" in params and role.name == utils.bot_manager_role and params["archive"]:
            logger.warn("Trying to archive bot manager role")
            raise commands.MissingPermissions(["I won't archive Bot manager role, lol. Nope."])

        old_name = role.name
        role = role.update_from_dict(params)
        await role.save()

        if "name" in params:
            await self.rename_guilds_roles(old_name, role.name)
        await self.update_guilds_roles()

        logger.db(f"Edited role '{old_name}'' (args '{args}', params '{params}')")

        await ctx.send(f"Updated role {self.get_role_repr(ctx, role.name)} with new parameters: "
                       f"{utils.format_params(params)}",
                       allowed_mentions=discord.AllowedMentions.none(),
                       hidden=True)
        await self.update_options()

    @cog_ext.cog_subcommand(base="side", name="join",
                            options=[
                                create_option(
                                    name="side",
                                    description="Pick side to join",
                                    option_type=int,
                                    required=True,
                                ),
                            ],
                            guild_ids=guild_ids)
    async def side_join(self, ctx: SlashContext, side):
        """Choose your Sider role!"""
        logger.info(f"{ctx.author} trying to join side with ID {side}")
        if self._sider_group is None:
            logger.warn("Sider group is empty")
            raise commands.CheckFailure(
                "Sorry, but this command is unavailable as there is no **Sider** role group yet.")

        member = ctx.author
        group = self._sider_group

        role_names = [role.name for role in member.roles]
        previous_roles = await Role.filter(group=group, name__in=role_names).values_list("name", flat=True)

        if side == -1:
            if not previous_roles:
                logger.info(f"{member} don't have a side role")
                await ctx.send("But... you're already a noside, isn't you?")
            else:
                await self.remove_conflicting_roles(ctx, member, group)
                logger.info(f"Removed sider role from {member}")
                await ctx.send("You're a *noside* now! Is that what you wanted?")
            return

        side = await Role.get(id=side)
        logger.info(f"Side with ID {side.id} is '{side.name}'")

        if side.name in role_names:
            logger.info(f"{member} already have this side role")
            await ctx.send(f"Aren't you already a {side.name}, {member.display_name}?")
            return

        await self.remove_conflicting_roles(ctx, member, group)

        try:
            role = await commands.RoleConverter().convert(ctx, side.name)
        except commands.RoleNotFound:
            logger.warn(f"Role with name '{side.name}'' not found")
            raise commands.BadArgument(f"Sorry, but there is no role for **{side.name}** on this server yet")
        await member.add_roles(role)

        if "Nixside" in previous_roles and role.name == "Drakeside":
            message = "You know, Ziva, you can't really learn drakeside boxsignal this way!"
        elif role.name == "Simurgh":
            message = "THE SIMURGH\nIS HERE"
        elif role.name == "Spaceside":
            message = "/-//-/- /-//-/ //-/-/-/-"
        elif role.name == "Zalside":
            message = "Splish-splash!"
        else:
            message = f"You're a **{role.name}** now!"
        
        logger.info(f"{member} joined side '{role.name}'")
        await ctx.send(message)

    @cog_ext.cog_subcommand(base="side", name="leave", guild_ids=guild_ids)
    async def side_leave(self, ctx: SlashContext):
        await self.side_join.invoke(ctx, side=-1)

    @cog_ext.cog_subcommand(base="livestream_crew", name="join", options=[], guild_ids=guild_ids)
    async def streamcrew_join(self, ctx: SlashContext, join=True):
        """Receive 'livestream crew' role to get pings when LynxGriffin is streaming!"""
        logger.info(f"{ctx.author} trying to {'join' if join else 'leave'} streamcrew")

        try:
            db_role = await Role.get(name=utils.stream_crew_role)
        except exceptions.DoesNotExist:
            logger.warn(f"Can't find a role with name '{utils.stream_crew_role}' in the DB")
            raise commands.CheckFailure(
                f"Sorry, but this command is unavailable as there is no **{utils.stream_crew_role}** role in DB.")
        try:
            role = await commands.RoleConverter().convert(ctx, db_role.name)
        except commands.RoleNotFound:
            logger.warn(f"Role with name '{utils.stream_crew_role}' in not a valid role for {ctx.guild}")
            raise commands.CheckFailure(
                f"Sorry, but this command is unavailable as there is no **{utils.stream_crew_role}** role "
                f"on this server yet.")

        member = ctx.author
        if join:
            if role in member.roles:
                logger.info(f"{ctx.author} already in streamcrew")
                await ctx.send(f"Do you need **more** pings, {member.mention}? You're already in livestream crew")
            else:
                await member.add_roles(role)
                logger.info(f"{ctx.author} joined streamcrew")
                await ctx.send("Welcome to the livestream crew! Enjoy your pings ;)")
        else:
            if role not in member.roles:
                logger.info(f"{ctx.author} is not in streamcrew")
                await ctx.send("You're not in the livestream crew? Never have been 🔫")
            else:
                await member.remove_roles(role)
                logger.info(f"{ctx.author} left streamcrew")
                await ctx.send("Goodbye o7")

    @cog_ext.cog_subcommand(base="livestream_crew", name="leave", guild_ids=guild_ids)
    async def streamcrew_leave(self, ctx: SlashContext):
        """Leave 'livestream crew'"""
        await self.streamcrew_join.invoke(ctx, join=False)


def setup(bot):
    bot.add_cog(Roles(bot))
