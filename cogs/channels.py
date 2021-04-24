import logging
from enum import Enum

import discord
from discord_slash import cog_ext, SlashContext
from discord_slash.utils.manage_commands import create_option, create_choice
from tortoise import fields
from tortoise.models import Model

import cogs.cog_utils as utils
from cogs.permissions import has_bot_perms
from cogs.models import HomeChannels

logger = logging.getLogger(__name__)


class ChannelType(Enum):
    # 0 reserved for All
    HOME = 1, "Home channel"
    UPDATE = 2, "Update notify channel"
    JOIN_CHECK = 3, "Automatic check channel"
    NO_REACTIONS = 4, "No reactions channel"

    @staticmethod
    def get_by_index(index: int):
        for channel_type in ChannelType:
            if channel_type.value[0] == index:
                return channel_type
        raise IndexError(f"No channel type with index {index}")
    
    @staticmethod
    def get_name_by_index(index: int):
        return ChannelType.get_by_index(index).value[1]

    @staticmethod
    def is_default_index(index: int):
        return index == 0

    @staticmethod
    def get_choices():
        return [create_choice(name=ch_type.value[1], value=ch_type.value[0]) for ch_type in ChannelType]
    
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

    async def on_startup(self):
        existing_home_channels = await HomeChannels.all()
        for channel in existing_home_channels:
            logger.db(f"Converting home channel in guild with id '{channel.guild_id}' to channel setup")
            await ChannelSetup.create(guild_id=channel.guild_id, channel_id=channel.channel_id, channel_type=ChannelType.HOME.value[0])
            await channel.delete()

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
                                    description="Channel to set the type to (this by default)",
                                    option_type=discord.TextChannel,
                                    required=False
                                )
                            ], guild_ids=utils.guild_ids)
    @has_bot_perms()
    async def set_type(self, ctx: SlashContext, type_idx: int, channel: discord.TextChannel = None):
        """Sets type for the selected channel (or this one by default)"""
        await ctx.defer(hidden=True)
        channel = channel or ctx.channel
        channel_type_name = ChannelType.get_name_by_index(type_idx)
        channel_id = channel.id
        guild_id = ctx.guild_id
        logger.db(f"'{ctx.author}' trying to set type '{channel_type_name}' to '{channel}' in '{ctx.guild}'")

        existing_setup = await ChannelSetup.get_or_none(guild_id=guild_id, channel_id = channel_id, channel_type=type_idx)
        if existing_setup:
            await ctx.send(f"{channel.mention} already has type '{channel_type_name}'", hidden=True)
            return
        
        await ChannelSetup.create(guild_id=guild_id, channel_id=channel_id, channel_type=type_idx)
        logger.db(f"Set type '{channel_type_name}' to '{channel}' in '{ctx.guild}'")

        existing_channels = await ChannelSetup.filter(guild_id=guild_id, channel_type=type_idx)
        success_msg = f"Set type '{channel_type_name}' for {channel.mention}"
        if len(existing_channels) == 1:
            await ctx.send(f"{success_msg}. This is the only channel with this type", hidden=True)
        else:
            mentions = [ctx.guild.get_channel(channel.channel_id).mention for channel in existing_channels]
            await ctx.send(f"{success_msg}. Channels with this type: {', '.join(mentions)}")

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
                            ], guild_ids=utils.guild_ids)
    @has_bot_perms()
    async def clear_type(self, ctx: SlashContext, type_idx: int, channel: discord.TextChannel = None):
        """Removes type from the selected channel (or this one by default)"""
        await ctx.defer(hidden=True)
        channel = channel or ctx.channel
        type_string = "all types" if ChannelType.is_default_index(type_idx) else f"type '{ChannelType.get_name_by_index(type_idx)}'"
        logger.db(f"'{ctx.author}' trying to remove {type_string} from '{channel}' in '{ctx.guild}'")

        channel_setups = await ChannelSetup.filter(guild_id=ctx.guild_id, channel_id=channel.id)
        if not channel_setups:
            await ctx.send(f"{channel.mention} don't have any type set", hidden=True)
            return

        removed_types = []
        for ch_setup in channel_setups:
            if not ChannelType.is_default_index(type_idx) and ch_setup.channel_type != type_idx:
                continue
            channel_type = ChannelType.get_by_index(ch_setup.channel_type)
            removed_types.append(channel_type)
            await ch_setup.delete()
            logger.db(f"Removed type '{channel_type.value[1]}' from '{channel}' in '{ctx.guild}'")

        removed_types = [f"'{ch_type.value[1]}'" for ch_type in removed_types]
        await ctx.send(f"Removed {', '.join(removed_types)} {'types' if len(removed_types) > 1 else 'type'} from {channel.mention}", hidden=True)

    @cog_ext.cog_subcommand(base="channel", subcommand_group="list", name="types",
                            options=[
                                create_option(
                                    name="channel",
                                    description="Channel to list types (this by default)",
                                    option_type=discord.TextChannel,
                                    required=False
                                )
                            ], guild_ids=utils.guild_ids)
    @has_bot_perms()
    async def list_types(self, ctx: SlashContext, channel: discord.TextChannel = None):
        """Lists all types of the selected channel (or this one by default)"""
        await ctx.defer(hidden=True)
        channel = channel or ctx.channel
        channel_id = channel.id
        guild_id = ctx.guild_id
        logger.db(f"'{ctx.author}' trying to list types of '{channel}' in '{ctx.guild}'")

        types = await ChannelSetup.filter(guild_id=guild_id, channel_id=channel_id)
        types = [f"'{ChannelType.get_name_by_index(setup.channel_type)}'" for setup in types]
        if types:
            await ctx.send(f"Channel {channel.mention} has {'types' if len(types) > 1 else 'type'} {', '.join(types)}", hidden=True)
        else:
            await ctx.send(f"Channel {channel.mention} don't have any types set", hidden=True)

    @cog_ext.cog_subcommand(base="channel", subcommand_group="list", name="channels",
                            options=[
                                create_option(
                                    name="type",
                                    description="Type to list channels",
                                    option_type=int,
                                    choices=ChannelType.get_choices_with_default("Any"),
                                    required=True
                                )
                            ], guild_ids=utils.guild_ids)
    @has_bot_perms()
    async def list_channels(self, ctx: SlashContext, type_idx: int):
        """Lists all channels with the selected type (or with any type)"""
        await ctx.defer(hidden=True)
        guild_id = ctx.guild_id
        logger.db(f"'{ctx.author}' trying to list channels with {type_idx} type index in '{ctx.guild}'")
        types = ChannelType if ChannelType.is_default_index(type_idx) else [ChannelType.get_by_index(type_idx)]

        channels = await ChannelSetup.filter(guild_id=guild_id)
        if not channels:
            await ctx.send("There is no set channels", hidden=True)
            return

        results = []
        for ch_type in types:
            ch_type_idx = ch_type.value[0]
            ch_type_name = ch_type.value[1]

            type_channels = [ctx.guild.get_channel(setup.channel_id).mention for setup in channels if setup.channel_type == ch_type_idx]
            if type_channels:
                results.append(f"Channels with type '{ch_type_name}': {', '.join(type_channels)}")
            elif type_idx: # Don't send no channels notifications if this was list all types command
                results.append(f"There is no channels with type '{ch_type_name}'")
        
        await ctx.send('\n'.join(results), hidden=True)

    @cog_ext.cog_subcommand(base="channel", subcommand_group="list", name="database", guild_ids=utils.guild_ids)
    @has_bot_perms()
    async def list_database(self, ctx: SlashContext):
        """Lists all chanel setup entries in the database"""
        await ctx.defer(hidden=True)
        logger.db(f"'{ctx.author}' trying to list database")

        setups = await ChannelSetup.all()
        if not setups:
            await ctx.send("Database is empty", hidden=True)
            return

        result = ""
        for ch_setup in setups:
            guild = await self.bot.fetch_guild(ch_setup.guild_id)
            channel = await self.bot.fetch_channel(ch_setup.channel_id)
            type_name = ChannelType.get_name_by_index(ch_setup.channel_type)
            line = f"{channel.mention} in '{guild}'' has type '{type_name}'\n"
            if len(result) + len(line) >= 2000:
                await ctx.send(result, hidden=True)
                result = ""
            result += line
        
        await ctx.send(result, hidden=True)
        

def setup(bot):
    bot.add_cog(Channels(bot))