import logging
from enum import Enum

import discord
from discord_slash import cog_ext, SlashContext
from discord_slash.utils.manage_commands import create_option, create_choice
from tortoise import fields
from tortoise.models import Model

import cogs.cog_utils as utils
from cogs.permissions import has_bot_perms

logger = logging.getLogger(__name__)


class ChannelType(Enum):
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
    def get_choices():
        return [create_choice(name=ch_type.value[1], value=ch_type.value[0]) for ch_type in ChannelType]


class ChannelSetup(Model):
    guild_id = fields.BigIntField()
    channel_id = fields.BigIntField()
    channel_type = fields.IntField()

# /channel check Optional[channel]
# /channel check_all

class Channels(utils.AutoLogCog):
    """Cog that manages bot channels (e.g. home channel, update notification, ...)"""

    def __init__(self, bot):
        utils.AutoLogCog.__init__(self, logger)
        self.bot = bot

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

    @cog_ext.cog_subcommand(base="channel", subcommand_group="type", name="remove",
                            options=[
                                create_option(
                                    name="type",
                                    description="Type of the channel to remove",
                                    option_type=int,
                                    choices=ChannelType.get_choices(),
                                    required=True
                                ),
                                create_option(
                                    name="channel",
                                    description="Channel to remove the type from (this by default)",
                                    option_type=discord.TextChannel,
                                    required=False
                                )
                            ], guild_ids=utils.guild_ids)
    @has_bot_perms()
    async def remove_type(self, ctx: SlashContext, type_idx: int, channel: discord.TextChannel = None):
        """Removes type from the selected channel (or this one by default)"""
        await ctx.defer(hidden=True)
        channel = channel or ctx.channel
        channel_type_name = ChannelType.get_name_by_index(type_idx)
        channel_id = channel.id
        guild_id = ctx.guild_id
        logger.db(f"'{ctx.author}' trying to remove type '{channel_type_name}' from '{channel}' in '{ctx.guild}'")

        existing_setup = await ChannelSetup.get_or_none(guild_id=guild_id, channel_id = channel_id, channel_type=type_idx)
        if existing_setup is None:
            await ctx.send(f"{channel.mention} don't have type '{channel_type_name}'", hidden=True)
            return
        
        await existing_setup.delete()
        logger.db(f"Remove type '{channel_type_name}' from '{channel}' in '{ctx.guild}'")

        existing_channels = await ChannelSetup.filter(guild_id=guild_id, channel_type=type_idx)
        success_msg = f"Remove type '{channel_type_name}' for {channel.mention}"
        if existing_channels:
            mentions = [ctx.guild.get_channel(channel.channel_id).mention for channel in existing_channels]
            await ctx.send(f"{success_msg}. Channels with this type: {', '.join(mentions)}")
        else:
            await ctx.send(f"{success_msg}. There is no more channels with this type", hidden=True)

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
                                    choices=ChannelType.get_choices(),
                                    required=True
                                )
                            ], guild_ids=utils.guild_ids)
    @has_bot_perms()
    async def list_channels(self, ctx: SlashContext, type_idx: int):
        """Lists all channels with the selected type"""
        await ctx.defer(hidden=True)
        guild_id = ctx.guild_id
        type_name = ChannelType.get_name_by_index(type_idx)
        logger.db(f"'{ctx.author}' trying to list channels with '{type_name}' type in '{ctx.guild}'")

        channels = await ChannelSetup.filter(guild_id=guild_id, channel_type=type_idx)
        channels = [ctx.guild.get_channel(setup.channel_id).mention for setup in channels]
        if channels:
            await ctx.send(f"Channels with type '{type_name}': {', '.join(channels)}", hidden=True)
        else:
            await ctx.send(f"There is no channels with type '{type_name}'", hidden=True)
        

def setup(bot):
    bot.add_cog(Channels(bot))