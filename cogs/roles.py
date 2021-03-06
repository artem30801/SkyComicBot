import logging
from typing import Optional

import discord
from discord.ext import commands
from discord_slash import cog_ext, SlashContext
from discord_slash.utils.manage_commands import create_option, create_choice
from tortoise import exceptions
from tortoise import fields
from tortoise.models import Model
from tortoise.transactions import atomic

import cogs.cog_utils as utils
import cogs.db_utils as db_utils
from cogs.cog_utils import guild_ids
from cogs.permissions import has_server_perms, has_server_perms_from_ctx, has_bot_perms

logger = logging.getLogger(__name__)

role_number = db_utils.NextNumber()
group_number = db_utils.NextNumber()


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


class Roles(utils.AutoLogCog, utils.StartupCog):
    """Roles management commands"""

    # noinspection PyTypeChecker
    def __init__(self, bot):
        utils.AutoLogCog.__init__(self, logger)
        utils.StartupCog.__init__(self)

        self.bot = bot
        self._sider_group = None
        self.has_crews = False

        self.role_processor: db_utils.ModelProcessor = None
        self.group_processor: db_utils.ModelProcessor = None

    async def on_startup(self):
        self.role_processor = db_utils.ModelProcessor(Role, fk_dict)
        self.group_processor = db_utils.ModelProcessor(RoleGroup, fk_dict)

        self.role_add.options = self.role_processor.options.add
        self.role_edit.options = self.role_processor.options.edit

        self.group_add.options = self.group_processor.options.add
        self.group_edit.options = self.group_processor.options.edit

        self.group_edit_roles.options = self.group_processor.options.edit[:1] + self.role_processor.options.edit[2:]

        await self.update_options()

    # noinspection PyTypeChecker
    async def update_options(self, updated_fields=None):
        logger.info(f"Updating command fields: {updated_fields}")
        await self.role_processor.update_choices(updated_fields)
        await self.group_processor.update_choices(updated_fields)

        if db_utils.is_updated(updated_fields, "group"):
            group_choices = self.group_processor.choices
            group_commands = [self.group_info, self.group_delete, self.role_snapshot_all]
            for command in group_commands:
                command.options[0]["choices"] = group_choices
            self.role_snapshot_role.options[1]["choices"] = group_choices

            if self._sider_group is None:
                self._sider_group = await RoleGroup.get_or_none(name="Sider")

        if db_utils.is_updated(updated_fields, "role"):
            crews = [await Role.get_or_none(name=role) for role in
                     (utils.stream_crew_role, utils.update_crew_role, utils.bot_crew_role)]
            crews = [create_choice(name=role.name, value=role.id) for role in crews if role is not None]
            self.crew_join.options[0]["choices"] = crews
            self.crew_leave.options[0]["choices"] = crews
            self.has_crews = bool(crews)

            if self._sider_group is not None:
                sider_choices = [create_choice(name=role.name, value=role.id)
                                 for role in await Role.filter(group=self._sider_group)][:24]
                sider_choices.insert(0, create_choice(name="Noside", value=-1))
                self.side_join.options[0]["choices"] = sider_choices

        await self.bot.slash.sync_all_commands()

    async def update_guilds_roles(self):
        to_update = await Role.exclude(archived=True)
        to_remove = await Role.filter(archived=True).values_list("name", flat=True)

        for guild in self.bot.guilds:
            me = guild.me
            if not me.guild_permissions.manage_roles:
                logger.warning(f"Don't have 'manage roles' permissions in '{guild}'")
                continue

            position = me.top_role.position
            for db_role in to_update:
                role = discord.utils.get(guild.roles, name=db_role.name)
                if role is not None and not utils.can_manage_role(me, role):
                    logger.warning(f"Can't manage role '{db_role.name}' at '{guild}'")
                    continue

                position = max(position - 1, 1)
                await self._setup_role(guild, role, db_role, position)

            for name in to_remove:
                role = discord.utils.get(guild.roles, name=name)
                if role is not None:
                    try:
                        await role.delete()
                    except (discord.errors.Forbidden, discord.errors.HTTPException):
                        logger.warning(f"Failed delete role {name} at {guild}")

    @staticmethod
    async def _setup_role(guild, role, db_role, position):
        try:
            if role is not None:
                if not all((role.color == db_role.color,
                            role.mentionable == db_role.mentionable,
                            role.position == position,
                            )):
                    await role.edit(colour=db_role.color, mentionable=db_role.mentionable, position=position)
            else:
                role = await guild.create_role(name=db_role.name, colour=db_role.color,
                                               mentionable=db_role.mentionable)
                await role.edit(position=position)
        except (discord.errors.Forbidden, discord.errors.HTTPException):
            logger.warning(f"Failed to setup role '{db_role.name}' at '{guild}'")

    async def rename_guilds_roles(self, old_name, name):
        if old_name == name:
            return

        for guild in self.bot.guilds:
            role = discord.utils.get(guild.roles, name=old_name)
            if role is not None:
                try:
                    await role.edit(name=name)
                except discord.errors.Forbidden:
                    logger.warning(f"Can't rename role {name} at {guild}")

    @staticmethod
    async def remove_conflicting_roles(ctx, member: discord.Member, group):
        db_roles = await group.roles
        member_roles = member.roles

        roles = (discord.utils.get(ctx.guild.roles, name=db_role.name) for db_role in db_roles if not db_role.archived)
        roles = [role for role in roles if role is not None and role in member_roles]
        if roles:
            await member.remove_roles(*roles)
            logger.debug(f"Removed roles from group {group.name} from {ctx.guild}>{member}")

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

    @staticmethod
    def member_mention(ctx, member):
        return member.mention if member != ctx.author else "You"

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
        if member and not isinstance(member, discord.Member):
            raise commands.BadArgument(f"Failed to get member '{member}' info!")

        await ctx.defer(hidden=True)
        member = member or ctx.author

        logger.debug(f"{self.format_caller(ctx)} checked {member} roles")

        if len(member.roles) <= 1:
            role_text = "don't have any roles"
        else:
            mentions = [role.mention for role in member.roles[1:]]
            role_text = f"have following roles:  {', '.join(mentions)}"

        await ctx.send(f"{self.member_mention(ctx, member)} {role_text}. To view available roles, use /role list",
                       allowed_mentions=discord.AllowedMentions.none(),
                       hidden=True)

    @cog_ext.cog_subcommand(base="role", name="list", guild_ids=guild_ids)
    async def role_list(self, ctx: SlashContext):
        """Shows list of all roles available to you. Roles are grouped by role group"""
        await ctx.defer()

        caller_has_server_perms = await has_server_perms_from_ctx(ctx)
        embed = utils.bot_embed(self.bot)
        embed.title = "Available roles:"
        db_groups = await RoleGroup.all()

        for group in db_groups:
            if await self.get_group_state(group) != RoleGroupStates.normal:
                continue
            query = Role.filter(group=group, archived=False)
            if not caller_has_server_perms:
                query = query.filter(assignable=True)
            db_roles = await query.values_list("name", flat=True)
            if not db_roles:
                continue
            role_mentions = [self.get_role_repr(ctx, role) for role in db_roles]
            embed.add_field(name=group.name, value="\n".join(role_mentions), inline=True)

        if not embed.fields:
            embed.description = "Woe is me, there are no roles!"

        embed.set_footer(text="Use `/role assign <role>` to get those roles!",
                         icon_url=self.bot.user.avatar_url)
        await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    @cog_ext.cog_subcommand(base="role", name="database", guild_ids=guild_ids)
    @has_server_perms()
    async def role_database(self, ctx: SlashContext):
        """Shows list of roles in internal DB with additional data"""
        await ctx.defer(hidden=True)

        db_roles = await Role.all()
        role_mentions = [f"{self.get_role_repr(ctx, role.name)} {await db_utils.format_instance(role)}"
                         for role in db_roles]

        for chunk in db_utils.chunks_split(role_mentions, 2000, 1):
            await ctx.send("\n".join(chunk),
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
        logger.debug(f"{self.format_caller(ctx)} checked group with ID '{group}'")
        await ctx.defer()

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
        await ctx.defer(hidden=True)

        async def display_group(group):
            state = await self.get_group_state(group)
            name = f"**{group.name}**"
            if state == RoleGroupStates.normal:
                role_count = len(await group.roles)
                state = f"has {role_count} assigned role{'s' if role_count > 1 else ''}"
            return f"{name} *({state})* {await db_utils.format_instance(group)}"

        db_groups = await RoleGroup.all()
        await ctx.send("Available role groups (by internal DB): \n" +
                       "\n".join([await display_group(group) for group in db_groups]),
                       hidden=True)

    @cog_ext.cog_subcommand(base="role", subcommand_group="group", name="add", guild_ids=guild_ids)
    @has_bot_perms()
    async def group_add(self, ctx: SlashContext, *args, **params):
        """Adds new role group to internal DB"""
        logger.db(f"{self.format_caller(ctx)} trying to add group with args '{args}' and params '{params}'")

        await ctx.defer(hidden=True)

        params = await self.group_processor.process(params)
        instance = await RoleGroup.create(**params)
        logger.db(f"Added role group '{instance.name}' with  params '{params}'")

        await ctx.send(f"Successfully added role group **{instance.name}**; {await db_utils.format_instance(instance)}",
                       hidden=True)
        await self.update_options("group")

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
    async def group_delete(self, ctx: SlashContext, group):
        """Removes specified group from internal DB"""
        logger.db(f"{self.format_caller(ctx)} trying to remove group with ID '{group}'")

        await ctx.defer(hidden=True)

        group = await RoleGroup.get(id=group)
        name = group.name
        logger.db(f"Group {group.id} is '{name}'")

        if await self.get_group_state(group) not in (RoleGroupStates.archived, RoleGroupStates.empty):
            logger.db(f"Failed to delete group {name}: has non-archived roles")
            raise commands.BadArgument(f"Cannot delete role group **{name}**! "
                                       f"Archive all roles in the role group or move them to other groups!")
        await group.delete()
        logger.db(f"Deleted role group {name}")
        await ctx.send(f"Successfully deleted role group **{name}**", hidden=True)
        await self.update_options("group")

    @cog_ext.cog_subcommand(base="role", subcommand_group="group", name="edit", guild_ids=guild_ids)
    @has_bot_perms()
    @atomic()
    async def group_edit(self, ctx: SlashContext, *args, **params):
        """Edits specified role group"""
        logger.db(f"{self.format_caller(ctx)} trying to edit group with args '{args}' and params '{params}")

        await ctx.defer(hidden=True)

        params = await self.group_processor.process(params)
        group = params.pop("group")

        old_name = group.name
        logger.db(f"Group {group.id} is '{old_name}'")

        group = group.update_from_dict(params)
        await group.save()
        logger.db(f"Edited group {old_name} with params '{params}'")

        await ctx.send(f"Updated role group **{old_name}** with new parameters: "
                       f"{utils.format_params(params)}",
                       hidden=True)
        await self.update_options("group")

    @has_bot_perms()
    async def group_archive(self, ctx: SlashContext, group, archive: bool = True):
        """Archives or unarchives all roles in specified group"""
        logger.db(f"{self.format_caller(ctx)} trying to archive group with ID '{group}'")

        await ctx.defer(True)

        group = await RoleGroup.get(id=group)
        archive = bool(archive)
        logger.db(f"Group {group.id} is '{group.name}'")

        if archive and utils.bot_manager_role in [role.name for role in await group.roles]:
            logger.warning(f"Failed to archive group '{group.name}'")
            raise commands.BadArgument(f"Cannot archive role group **{group.name}** with bot manager role! Nope!")

        await Role.filter(group=group).update(archived=archive)
        await self.update_guilds_roles()
        action = "Archived" if archive else "Unarchived"
        logger.db(f"Successfully {action.lower()} role group {group.name}")
        await ctx.send(f"{action} role group **{group.name}** and all roles in that group", hidden=True)

    @cog_ext.cog_subcommand(base="role", subcommand_group="group", name="edit_roles", guild_ids=guild_ids)
    @has_bot_perms()
    @atomic()
    async def group_edit_roles(self, ctx: SlashContext, *args, **params):
        """Edits all roles in given group with specified parameters"""
        logger.db(f"{self.format_caller(ctx)} trying to edit all roles in group "
                  f"with args '{args}' and params '{params}")

        await ctx.defer(True)
        group = params.pop("group")
        group = (await self.group_processor.process({"group": group}))["group"]

        base_number = params.pop("number", None)
        if base_number is not None:
            base_number = await db_utils.get_max_number(Role, base_number)
            print(base_number)

        roles = await Role.filter(group=group)
        for number, role in enumerate(roles):
            role_params = params.copy()
            role_params["role"] = role.name
            if base_number is not None:
                role_params["number"] = base_number + number
            await self._role_edit(role_params)

        await self.update_guilds_roles()
        await ctx.send(f"Edited role group **{group.name}** and all {len(roles)} roles in that group", hidden=True)
        await self.update_options("role")

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
        if member and not isinstance(member, discord.Member):
            raise commands.BadArgument(f"Failed to get member '{member}' info!")
        if not isinstance(role, discord.Role):
            raise commands.BadArgument(f"Failed to get role '{role}' info!")

        await ctx.defer(hidden=True)
        member = member or ctx.author
        logger.info(f"{self.format_caller(ctx)} trying to assign '{role}' to {member}")

        if role in member.roles:
            await ctx.send(f"Looks like {self.member_mention(ctx, member).lower()} already have {role.mention} role ;)",
                           allowed_mentions=discord.AllowedMentions.none(),
                           hidden=True)
            return

        try:
            db_role = await Role.get(name=role.name)
        except exceptions.DoesNotExist:
            logger.warning(f"Role with name '{role}'' not found in database")
            raise commands.BadArgument(f"Role {role.mention} is not in bots database! "
                                       f"You probably shouldn't use that role")

        if (member != ctx.author or db_role.name == utils.bot_manager_role or not db_role.assignable) \
                and not await has_server_perms_from_ctx(ctx):
            # MissingPermissions expects an array of permissions
            logger.info(f"{self.format_caller(ctx)} don't have permissions to assign '{role}' to {member}")
            raise commands.MissingPermissions([utils.bot_manager_role])

        if not utils.can_manage_role(ctx.guild.me, role):
            await ctx.send(f"Sorry, I cannot manage role {role.mention}",
                           allowed_mentions=discord.AllowedMentions.none(),
                           hidden=True)
            return

        group = await db_role.group
        if group.exclusive:
            logger.info(f"Removing roles, conflicting with {role} (if any)")
            await self.remove_conflicting_roles(ctx, member, group)

        await member.add_roles(role)
        logger.info(f"Assigned role '{role}' to {member}")
        await ctx.send(f"Assigned role {role.mention} to {member.mention}",
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
        if member and not isinstance(member, discord.Member):
            raise commands.BadArgument(f"Failed to get member '{member}' info!")
        if not isinstance(role, discord.Role):
            raise commands.BadArgument(f"Failed to get role '{role}' info!")

        await ctx.defer(hidden=True)
        member = member or ctx.author
        logger.info(f"{self.format_caller(ctx)} trying to remove role '{role}' from {member}")

        if role not in member.roles:
            await ctx.send(f"Looks like {self.member_mention(ctx, member).lower()} "
                           f"don't have {role.mention} role anyways ;)",
                           allowed_mentions=discord.AllowedMentions.none(),
                           hidden=True)
            return

        try:
            db_role = await Role.get(name=role.name)
        except exceptions.DoesNotExist:
            logger.warning(f"Role with name '{role.name}' not found in database")
            raise commands.BadArgument(f"Role {role.mention} is not in bots database!"
                                       f"You probably shouldn't touch that role")

        if (member != ctx.author or not db_role.assignable) and not await has_server_perms_from_ctx(ctx):
            logger.info(f"{self.format_caller(ctx)} don't have permissions to remove '{role}' from {member}")
            # MissingPermissions expects an array of permissions
            raise commands.MissingPermissions([utils.bot_manager_role])

        if not utils.can_manage_role(ctx.guild.me, role):
            await ctx.send(f"Sorry, I cannot manage role {role.mention}",
                           allowed_mentions=discord.AllowedMentions.none(),
                           hidden=True)
            return

        await member.remove_roles(role)
        logger.info(f"Removed role '{role}' from {member}")
        await ctx.send(f"Removed role {role.mention} from {member.mention}",
                       allowed_mentions=discord.AllowedMentions.none(),
                       hidden=True
                       )

    @cog_ext.cog_subcommand(base="role", subcommand_group="snapshot", name="role",
                            options=[
                                create_option(
                                    name="role",
                                    description="Role to snapshot (add)",
                                    option_type=discord.Role,
                                    required=True
                                ),
                                create_option(
                                    name="group",
                                    description="Group to add role to (Snapshot group by default)",
                                    option_type=int,
                                    required=False
                                )
                            ],
                            guild_ids=guild_ids)
    @has_bot_perms()
    async def role_snapshot_role(self, ctx: SlashContext, role: discord.Role, group: Optional[int] = None):
        """Adds existing server role to the internal database (if bot can manage this role)"""
        if not isinstance(role, discord.Role):
            raise commands.BadArgument(f"Failed to get role '{role}' info!")

        logger.db(f"{self.format_caller(ctx)} trying to snapshot role '{role}' from '{ctx.guild}'")
        await ctx.defer(hidden=True)
        if group:
            group = await RoleGroup.get_or_none(id=group)
        await self.snapshot_role(ctx, role, group)
        await ctx.send(f"Added '{role}' role to the internal database", hidden=True)
        await self.update_guilds_roles()
        await self.update_options("role")

    @cog_ext.cog_subcommand(base="role", subcommand_group="snapshot", name="all",
                            options=[
                                create_option(
                                    name="group",
                                    description="Group to add role to (Snapshot group by default)",
                                    option_type=int,
                                    required=False
                                )
                            ],
                            guild_ids=guild_ids)
    @has_bot_perms()
    @atomic()
    async def role_snapshot_all(self, ctx: SlashContext, group: Optional[int] = None):
        """Adds all existing server roles to the internal database (except roles, that bot cannot manage)"""
        await ctx.defer(hidden=True)
        logger.db(f"{self.format_caller(ctx)} trying to snapshot all roles from '{ctx.guild}'")

        if group:
            group = await RoleGroup.get_or_none(id=group)
        group = group or (await RoleGroup.get_or_create(name=utils.snapshot_role_group))[0]

        new_roles = []
        for role in reversed(ctx.guild.roles[1:]):  # to exclude @everyone
            try:
                await self.snapshot_role(ctx, role, group)
            except commands.BadArgument:
                continue
            else:
                new_roles.append(role)

        await self.update_guilds_roles()
        await ctx.send(f"Added roles: {', '.join([role.mention for role in new_roles])}", hidden=True)
        await self.update_options("role")

    @cog_ext.cog_subcommand(base="role", name="add", guild_ids=guild_ids)
    @has_bot_perms()
    async def role_add(self, ctx: SlashContext, *args, **params):
        """Adds new role to internal DB and discord servers"""
        logger.db(f"{self.format_caller(ctx)} trying to add role with args '{args}' and params '{params}'")

        await ctx.defer(hidden=True)
        params = await self.role_processor.process(params)
        instance = await Role.create(**params)
        await self.update_guilds_roles()

        logger.db(f"Added role '{instance.name}' with  params '{params}'")
        await ctx.send(f"Created role {self.get_role_repr(ctx, params['name'])}; "
                       f"{await db_utils.format_instance(instance)}",
                       allowed_mentions=discord.AllowedMentions.none(),
                       hidden=True)

        await self.update_options("role")

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
        logger.db(f"{self.format_caller(ctx)} trying to delete role '{role}'")

        await ctx.defer(hidden=True)

        role = (await self.role_processor.process({"role": role}))["role"]
        if not role.archived:
            logger.warning(f"Can't delete role '{role.name}', it is not archived!")
            raise commands.BadArgument("Role must be archived to be deleted from internal DB")

        await role.delete()
        logger.db(f"Deleted role '{role.name}'")
        await ctx.send(f"Successfully deleted **{role.name}** from internal DB", hidden=True)
        await self.update_options("role")

    @cog_ext.cog_subcommand(base="role", name="sync", guild_ids=guild_ids)
    @has_bot_perms()
    async def role_sync(self, ctx: SlashContext):
        """Updates roles and command options on connected discord servers according to internal DB"""
        await ctx.defer(hidden=True)
        await self.update_guilds_roles()
        await self.update_options()
        await ctx.send("Updated all guilds roles in accordance with internal DB", hidden=True)

    async def _role_edit(self, params):
        params = await self.role_processor.process(params)
        role = params.pop("role")

        if ("archived" in params or "name" in params) and role.name == utils.bot_manager_role and params["archived"]:
            logger.warning("Trying to edit bot manager role")
            raise commands.MissingPermissions(["I won't edit Bot manager role this way, lol. Nope."])

        old_name = role.name
        role = role.update_from_dict(params)
        await role.save()

        logger.db(f"Edited role '{old_name}' with params '{params}'")

        if "name" in params:
            await self.rename_guilds_roles(old_name, role.name)

        return role

    @cog_ext.cog_subcommand(base="role", name="edit", guild_ids=guild_ids)
    @has_bot_perms()
    @atomic()
    async def role_edit(self, ctx: SlashContext, *args, **params):
        """Edits specified role"""
        logger.db(f"{self.format_caller(ctx)} trying to edit role with args '{args}' and params '{params}'")

        await ctx.defer(hidden=True)

        role = await self._role_edit(params)

        await self.update_guilds_roles()
        await ctx.send(f"Updated role {self.get_role_repr(ctx, role.name)} with new parameters: "
                       f"{utils.format_params(params)}",
                       allowed_mentions=discord.AllowedMentions.none(),
                       hidden=True)
        await self.update_options("role")

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
            logger.warning("Sider group is empty")
            raise commands.CheckFailure(
                "Sorry, but this command is unavailable as there is no **Sider** role group yet.")

        await ctx.defer()

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

        role = discord.utils.get(ctx.guild.roles, name=side.name)
        if role is None:
            logger.warning(f"Role with name '{side.name}'' not found")
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

    @cog_ext.cog_subcommand(base="crew", name="join",
                            options=[
                                create_option(
                                    name="crew",
                                    description="Pick crew to join",
                                    option_type=int,
                                    required=True,
                                ),
                            ],
                            guild_ids=guild_ids)
    async def crew_join(self, ctx: SlashContext, crew, join=True):  # todo all
        """Receive crew roles to get pings when LynxGriffin is streaming or updating comics!"""
        logger.info(f"{ctx.author} trying to {'join' if join else 'leave'} {crew}")

        if not self.has_crews:
            raise commands.CheckFailure(f"Sorry, but this command is unavailable as there is no crew roles in DB.")

        await ctx.defer(hidden=True)
        db_role = await Role.get(id=crew)

        role = discord.utils.get(ctx.guild.roles, name=db_role.name)
        if role is None:
            logger.warning(f"Role with name '{db_role.name}' in not a valid role for {ctx.guild}")
            raise commands.CheckFailure(
                f"Sorry, but this command is unavailable as there is no **{db_role.name}** role "
                f"on this server yet.")

        if not utils.can_manage_role(ctx.guild.me, role):
            await ctx.send(f"Sorry, I cannot manage role {role.mention}",
                           allowed_mentions=discord.AllowedMentions.none(),
                           hidden=True)
            return

        member = ctx.author
        if join:
            if role in member.roles:
                logger.info(f"{ctx.author} already in {db_role.name}")
                await ctx.send(f"Do you need **more** pings, {member.mention}? You're already in {role.mention}",
                               allowed_mentions=discord.AllowedMentions(users=True, roles=False),
                               hidden=True)
            else:
                await member.add_roles(role)
                logger.info(f"{ctx.author} joined {db_role.name}")
                await ctx.send(f"Welcome to the {role.mention}! Enjoy your pings ;)",
                               allowed_mentions=discord.AllowedMentions.none(),
                               hidden=True)
        else:
            if role not in member.roles:
                logger.info(f"{ctx.author} is not in {db_role.name}")
                await ctx.send(f"You're not in the {role.mention}? Never have been 🔫",
                               allowed_mentions=discord.AllowedMentions.none(),
                               hidden=True)
            else:
                await member.remove_roles(role)
                logger.info(f"{ctx.author} left {db_role.name}")
                await ctx.send("Goodbye o7", hidden=True)

    @cog_ext.cog_subcommand(base="crew", name="leave",
                            options=[
                                create_option(
                                    name="crew",
                                    description="Pick crew to leave",
                                    option_type=int,
                                    required=True,
                                ),
                            ],
                            guild_ids=guild_ids)
    async def crew_leave(self, ctx: SlashContext, crew):
        """Leave selected crew"""
        await self.crew_join.invoke(ctx, crew=crew, join=False)

    @staticmethod
    async def snapshot_role(ctx, role: discord.Role, group: RoleGroup = None):
        """Adds role to the internal database"""
        if role.is_bot_managed() or role.is_integration() or role.is_premium_subscriber():
            logger.info(f"Skipping role '{role}' as it's system role")
            raise commands.BadArgument(f"Role '{role}' is a system role")

        if not utils.can_manage_role(ctx.guild.me, role):
            logger.info(f"Skipping role '{role}' as bot cannot manage it")
            raise commands.BadArgument(f"Bot cannot manage role '{role}'")

        if await Role.exists(name=role.name):
            logger.info(f"Skipping role '{role}' as it already exists")
            raise commands.BadArgument(f"Role '{role}' already exists in DB")

        group = group or (await RoleGroup.get_or_create(name=utils.snapshot_role_group))[0]
        number = await db_utils.get_max_number(Role)
        await db_utils.reshuffle(Role, number)
        db_role = Role(name=role.name,
                       color=role.color.value,
                       number=number,
                       archived=False,
                       assignable=False,
                       mentionable=role.mentionable,
                       group=group)
        await db_role.save()


def setup(bot):
    bot.add_cog(Roles(bot))
