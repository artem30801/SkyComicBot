import logging
from enum import Enum

import discord
from discord.ext import commands
from discord_slash import cog_ext, SlashContext
from discord_slash.utils.manage_commands import create_option, create_choice
from tortoise import fields
from tortoise.models import Model

import cogs.cog_utils as utils
import cogs.db_utils as db_utils
from cogs.permissions import has_bot_perms, has_server_perms

logger = logging.getLogger(__name__)


class ChannelType(Enum):
    # 0 reserved for All
    HOME = 1, "Home channel"
    UPDATE_MONITOR = 2, "Monitor update channel"
    UPDATE_NOTIFY = 3, "Update notify channel"
    MOD_LOG = 4, "Auto-moderation log channel"
    NO_REACTIONS = 5, "No reactions channel"

    def __new__(cls, *args, **kwargs):
        obj = object.__new__(cls)
        obj._value_ = args[0]
        return obj

    # ignore the first param since it's already set by __new__
    def __init__(self, _, description=None):
        self._description = description

    @property
    def description(self):
        return self._description

    @staticmethod
    def is_default_index(index: int):
        return index == 0

    @staticmethod
    def get_choices():
        return [create_choice(name=ch_type.description, value=ch_type.value) for ch_type in ChannelType]

    @staticmethod
    def get_choices_with_default(default_name: str = "Any"):
        return [create_choice(name=default_name, value=0)] + ChannelType.get_choices()


class ChannelSetup(Model):
    guild_id = fields.BigIntField()
    channel_id = fields.BigIntField()
    channel_type = fields.IntField()


class Channels(utils.AutoLogCog, utils.StartupCog):
    """Cog that manages bot channels (e.g. home channel, update notification, ...)"""

    def __init__(self, bot):
        utils.AutoLogCog.__init__(self, logger)
        utils.StartupCog.__init__(self)
        self.bot = bot

        self.no_react_channels = []
        self.monitor_channels = []

    async def on_startup(self):
        await self.delete_all_notfound()
        await self.update_channels()

    async def update_channels(self):
        self.no_react_channels = await self.get_channels(channel_type=ChannelType.NO_REACTIONS.value)
        self.monitor_channels = await self.get_channels(channel_type=ChannelType.UPDATE_MONITOR.value)

    async def delete_all_notfound(self):
        async for ch_setup in ChannelSetup.all():
            guild = self.bot.get_guild(ch_setup.guild_id)
            channel = guild.get_channel(ch_setup.channel_id)
            if guild is None or channel is None:
                await self.delete_notfound(ch_setup)

    async def delete_notfound(self, channel_setup: ChannelSetup):
        await channel_setup.delete()  # delete from db
        stack = self.format_stack(self.bot.get_guild(channel_setup.guild_id),
                                  self.bot.get_channel(channel_setup.channel_id)
                                  )
        logger.warning(f"Deleted channel setup from {stack or '(deleted guild)'} as channel was deleted")

    async def _get_channel(self, channel_setup: ChannelSetup):
        channel = self.bot.get_channel(channel_setup.channel_id)
        if channel is None:
            await self.delete_notfound(channel_setup)
        else:
            return channel

    async def get_channels(self, guild: discord.Guild = None, channel_type: int = None) -> [discord.TextChannel]:
        setups = ChannelSetup.all()
        if channel_type is not None:
            setups = setups.filter(channel_type=channel_type)
        if guild is not None:
            setups = setups.filter(guild_id=guild.id)

        channels = []
        async for channel_setup in setups:
            channels.append(self._get_channel(channel_setup))
        return channels

    async def get_home_channels(self, guild: discord.Guild = None) -> [discord.TextChannel]:
        return self.get_channels(guild, ChannelType.HOME.value)

    async def get_mod_log_channels(self, guild: discord.Guild = None) -> [discord.TextChannel]:
        return self.get_channels(guild, ChannelType.MOD_LOG.value)

    async def get_update_notify_channels(self, guild: discord.Guild = None) -> [discord.TextChannel]:
        return self.get_channels(guild, ChannelType.UPDATE_NOTIFY.value)

    @staticmethod
    async def is_channel_type(channel: discord.TextChannel, channel_type: int):
        return await ChannelSetup.exists(channel_id=channel.id, channel_type=channel_type)

    def is_no_reactions_channel(self, channel: discord.TextChannel):
        return channel in self.no_react_channels

    def is_update_monitor_channel(self, channel: discord.TextChannel):
        return channel in self.monitor_channels

    @cog_ext.cog_subcommand(base="channel", subcommand_group="type", name="set",
                            options=[
                                create_option(
                                    name="type",
                                    description="Type of the channel to set",
                                    option_type=int,
                                    choices=ChannelType.get_choices(),
                                    required=True
                                ),
                                create_option(
                                    name="channel",
                                    description="Channel to set the type to (current by default)",
                                    option_type=discord.TextChannel,
                                    required=False
                                )
                            ],
                            connector={"type": "type_index"},
                            guild_ids=utils.guild_ids)
    @has_bot_perms()
    async def set_type(self, ctx: SlashContext, type_index: int, channel: discord.TextChannel = None):
        """Sets type for the selected channel (or current one by default)"""
        await ctx.defer(hidden=True)
        channel = channel or ctx.channel
        channel_type_name = ChannelType(type_index).description
        channel_id = channel.id
        guild_id = ctx.guild_id
        logger.db(f"'{ctx.author}' trying to set type '{channel_type_name}' to '{channel}' in '{ctx.guild}'")

        if await ChannelSetup.exists(guild_id=guild_id, channel_id=channel_id, channel_type=type_index):
            await ctx.send(f"{channel.mention} already has type '{channel_type_name}'", hidden=True)
            return

        await ChannelSetup.create(guild_id=guild_id, channel_id=channel_id, channel_type=type_index)
        logger.db(f"Set type '{channel_type_name}' to '{channel}' in '{ctx.guild}'")

        existing_channels = await ChannelSetup.filter(guild_id=guild_id, channel_type=type_index)
        success_msg = f"Set type '{channel_type_name}' for {channel.mention}"
        if len(existing_channels) == 1:
            await ctx.send(f"{success_msg}. This is the only channel with this type", hidden=True)
        else:
            mentions = [ctx.guild.get_channel(channel.channel_id).mention for channel in existing_channels]
            await ctx.send(f"{success_msg}. Channels with this type: {', '.join(mentions)}")

        await self.update_channels()

    @cog_ext.cog_subcommand(base="channel", subcommand_group="type", name="clear",
                            options=[
                                create_option(
                                    name="type",
                                    description="Type of the channel to clear",
                                    option_type=int,
                                    choices=ChannelType.get_choices_with_default("All"),
                                    required=True
                                ),
                                create_option(
                                    name="channel",
                                    description="Channel to clear the type from (this by default)",
                                    option_type=discord.TextChannel,
                                    required=False
                                )
                            ],
                            connector={"type": "type_index"},
                            guild_ids=utils.guild_ids)
    @has_bot_perms()
    async def clear_type(self, ctx: SlashContext, type_index: int, channel: discord.TextChannel = None):
        """Removes type from the selected channel (or this one by default)"""
        await ctx.defer(hidden=True)
        channel = channel or ctx.channel
        type_string = "all types" if ChannelType.is_default_index(type_index) else f"type '{ChannelType(type_index)}'"
        logger.db(f"'{ctx.author}' trying to remove {type_string} from '{channel}' in '{ctx.guild}'")

        channel_setups = await ChannelSetup.filter(guild_id=ctx.guild_id, channel_id=channel.id)
        if not channel_setups:
            await ctx.send(f"{channel.mention} don't have any type set", hidden=True)
            return

        removed_types = []
        for ch_setup in channel_setups:
            if not ChannelType.is_default_index(type_index) and ch_setup.channel_type != type_index:
                continue
            channel_type = ChannelType(ch_setup.channel_type)
            removed_types.append(f"'*{channel_type.description}*'")
            await ch_setup.delete()
            logger.db(f"Removed type '{channel_type.description}' from '{channel}' in '{ctx.guild}'")

        if not removed_types:
            raise commands.BadArgument(f"Channel {channel.mention} does not have assigned type "
                                       f"'*{ChannelType(type_index).description}*'")

        await ctx.send(f"Removed {', '.join(removed_types)} "
                       f"{'types' if len(removed_types) > 1 else 'type'} "
                       f"from {channel.mention}",
                       hidden=True)

        await self.update_channels()

    @cog_ext.cog_subcommand(base="channel", subcommand_group="type", name="check",
                            options=[
                                create_option(
                                    name="channel",
                                    description="Channel to list types (this by default)",
                                    option_type=discord.TextChannel,
                                    required=False
                                )
                            ],
                            guild_ids=utils.guild_ids)
    @has_server_perms()
    async def list_types(self, ctx: SlashContext, channel: discord.TextChannel = None):
        """Lists all types of the selected channel (or this one by default)"""
        await ctx.defer(hidden=True)
        channel = channel or ctx.channel
        channel_id = channel.id
        guild_id = ctx.guild_id
        logger.info(f"'{ctx.author}' trying to list types of '*{channel}*' in '{ctx.guild}'")

        types = await ChannelSetup.filter(guild_id=guild_id, channel_id=channel_id)
        types = [f"'{ChannelType(channel_setup.channel_type).description}'" for channel_setup in types]
        if types:
            await ctx.send(f"Channel {channel.mention} has {'types' if len(types) > 1 else 'type'} {', '.join(types)}",
                           hidden=True)
        else:
            await ctx.send(f"Channel {channel.mention} don't have any types set", hidden=True)

    @cog_ext.cog_subcommand(base="channel", subcommand_group="list", name="channels",
                            options=[
                                create_option(
                                    name="type",
                                    description="Type to list channels",
                                    option_type=int,
                                    choices=ChannelType.get_choices_with_default("Any"),
                                    required=False
                                )
                            ],
                            connector={"type": "type_index"},
                            guild_ids=utils.guild_ids)
    @has_server_perms()
    async def list_channels(self, ctx: SlashContext, type_index: int = 0):
        """Lists all channels with the selected type (or with any type)"""
        await ctx.defer(hidden=True)
        logger.db(f"'{ctx.author}' trying to list channels with {type_index} type index in '{ctx.guild}'")
        types = ChannelType if ChannelType.is_default_index(type_index) else [ChannelType(type_index)]

        channels = await ChannelSetup.filter(guild_id=ctx.guild.id)
        if not channels:
            await ctx.send("There are no channels with set type", hidden=True)
            return

        results = []
        for ch_type in types:
            ch_type_index = ch_type.value
            ch_type_name = ch_type.description

            type_channels = [ctx.guild.get_channel(setup.channel_id).mention for setup in channels
                             if setup.channel_type == ch_type_index]
            if type_channels:
                results.append(f"Channels with type '{ch_type_name}': {', '.join(type_channels)}")
            elif type_index:  # Don't send no channels notifications if this was list all types command
                results.append(f"There is no channels with type '{ch_type_name}'")

        await ctx.send('\n'.join(results), hidden=True)

    @cog_ext.cog_subcommand(base="channel", subcommand_group="list", name="database", guild_ids=utils.guild_ids)
    @has_bot_perms()
    async def list_database(self, ctx: SlashContext):
        """Lists all chanel setup entries in the database"""
        await ctx.defer(hidden=True)
        logger.db(f"'{ctx.author}' trying to list database")

        setups = ChannelSetup.all()

        result = []
        async for ch_setup in setups:
            guild = self.bot.get_guild(ch_setup.guild_id)
            channel = guild.get_channel(ch_setup.channel_id)
            if channel is None:  # if channel is no more
                await ch_setup.delete()  # delete from db

            # channel = channel.mention if guild == ctx.guild else channel.name
            type_name = ChannelType(ch_setup.channel_type).description
            line = f"{channel.mention} in '{guild}'' has type '{type_name}'"
            result.append(line)

        if not result:
            await ctx.send("Database is empty", hidden=True)
            return

        for chunk in db_utils.chunks_split(result):
            await ctx.send("\n".join(chunk), hidden=True)


def setup(bot):
    bot.add_cog(Channels(bot))
