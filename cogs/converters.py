import logging
import re
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional, Union

import aiohttp
import discord
from discord.ext import commands
from discord_slash import cog_ext, SlashContext
from discord_slash.utils.manage_commands import create_option, create_choice

import tortoise
from tortoise import fields
from tortoise.models import Model

import cogs.cog_utils as utils


logger = logging.getLogger(__name__)


class Timezones(Model):
    member_id = fields.BigIntField()
    member_timezone = fields.TextField()


class TimezonedbException(Exception):
    """Exception class for all errors connected with TimezoneDB"""
    pass


class TimezoneInputType(Enum):
    ValidUser = 0
    ValidTimezone = 1
    InvalidData = 2


class Conversions(utils.AutoLogCog):
    """Timezone conversions (and maybe temperature later?)"""

    def __init__(self, bot: commands.Bot):
        utils.AutoLogCog.__init__(self, logger)
        self.bot = bot
        self.timedb_key = None
        try:
            self.timedb_key = bot.config["auth"]["timezonedb_key"]
        except KeyError:
            logger.warning("Can't read TimezoneDB API key!")

    @cog_ext.cog_subcommand(base="time", name="convert",
                            options=[
                                create_option(
                                    name="time",
                                    description="Time in 12/24h format in 'from' timezone",
                                    option_type=str,
                                    required=True
                                ),
                                create_option(
                                    name="timezone_from",
                                    description="Source timezone abbreviation (may be with shift, e.g. GMT+3)",
                                    option_type=str,
                                    required=False
                                ),
                                create_option(
                                    name="member_from",
                                    description="Member with set timezone, that will be used as source timezone",
                                    option_type=discord.Member,
                                    required=False
                                ),
                                create_option(
                                    name="timezone_to",
                                    description="Destination timezone abbreviation (may be with shift, e.g. GMT+3)",
                                    option_type=str,
                                    required=False
                                ),
                                create_option(
                                    name="member_to",
                                    description="Member with set timezone, that will be used as destination timezone",
                                    option_type=discord.Member,
                                    required=False
                                ),
                            ])
    async def convert(self, ctx: SlashContext, time: str, 
                      timezone_from: str = None, member_from: discord.Member = None,
                      timezone_to: str = None, member_to: discord.Member = None):
        """Converts time from one timezone to another"""
        if member_from and not isinstance(member_from, discord.Member):
            raise commands.BadArgument(f"Failed to get member '{member_from}' info!")
        if member_to and not isinstance(member_to, discord.Member):
            raise commands.BadArgument(f"Failed to get member '{member_to}' info!")

        await ctx.defer()
        logger.debug(f"{ctx.author} trying to convert '{time}' from '{timezone_from}'/'{member_from}' to '{timezone_to}'/'{member_to}'")

        source_time, should_use_12_hours = Conversions.strtime_to_datetime(time)
        if source_time is None:
            raise commands.BadArgument(f"Can't parse '{time}' time. Is it valid?")

        tz_from = member_from or timezone_from or ctx.author
        tz_to = member_to or timezone_to
        if tz_to is None:
            raise commands.BadArgument("Either timezone_to or member_to should be set")

        tz_from_utc_diff, from_tz_name, input_type = await self.get_diff_from_utc(ctx, tz_from)
        if tz_from_utc_diff is None:
            if member_from is None and timezone_from is None:
                raise commands.BadArgument("Either timezone_from, member_from or your timezone should be set")
            raise commands.BadArgument(self.get_invalid_timezone_response(tz_from, input_type, ctx.author))

        tz_to_utc_diff, to_tz_name, input_type = await self.get_diff_from_utc(ctx, tz_to)
        if tz_to_utc_diff is None:
            raise commands.BadArgument(self.get_invalid_timezone_response(tz_to, input_type, ctx.author))

        result_time = source_time - tz_from_utc_diff + tz_to_utc_diff

        # set result as 12/24 hours time depending on request format
        if should_use_12_hours:
            source_time = f'{source_time:%I:%M %p}'
            result_time = f'{result_time:%I:%M %p}'
        else:
            source_time = f'{source_time:%H:%M}'
            result_time = f'{result_time:%H:%M}'

        from_tz_shift = self.timedelta_to_utc_str(tz_from_utc_diff)
        to_tz_shift = self.timedelta_to_utc_str(tz_to_utc_diff)

        embed_result = discord.Embed(title=result_time)
        embed_result.color = utils.embed_color
        embed_result.add_field(name="From", value=f"{source_time}\n{from_tz_name} ({from_tz_shift})")
        embed_result.add_field(name="To", value=f"{result_time}\n{to_tz_name} ({to_tz_shift})")

        await ctx.send(embed=embed_result)

    @cog_ext.cog_subcommand(base="time", subcommand_group = "now", name="timezone",
                            options=[
                                create_option(
                                    name="timezone",
                                    description="Timezone abbreviation (may be with shift, e.g. GMT+3)",
                                    option_type=str,
                                    required=True
                                )
                            ])
    async def now_tz(self, ctx: SlashContext, timezone: str):
        """Shows current time in the other timezone"""
        await self.now(ctx, timezone)

    @cog_ext.cog_subcommand(base="time", subcommand_group = "now", name="member",
                            options=[
                                create_option(
                                    name="member",
                                    description="Server member with set timezone",
                                    option_type=discord.Member,
                                    required=True
                                )
                            ])
    async def now_member(self, ctx: SlashContext, member: discord.Member):
        """Shows current time for the other member, if they have timezone set"""
        if not isinstance(member, discord.Member):
            raise commands.BadArgument(f"Failed to get member '{member}' info!")

        await self.now(ctx, member)

    async def now(self, ctx: SlashContext, timezone: Union[str, discord.Member]):
        """Shows current time in the other timezone or for the member"""
        await ctx.defer()
        logger.debug(f"{ctx.author} checking time for '{timezone}'")

        tz_utc_diff, tz_name, input_type = await self.get_diff_from_utc(ctx, timezone)
        if tz_utc_diff is None:
            raise commands.BadArgument(self.get_invalid_timezone_response(timezone, input_type, ctx.author))

        result_time = datetime.utcnow() + tz_utc_diff
        tz_shift = self.timedelta_to_utc_str(tz_utc_diff)

        embed_result = discord.Embed(title=f'{result_time:%I:%M %p} | {result_time:%H:%M}')
        embed_result.color = utils.embed_color
        embed_result.add_field(name="Timezone", value=f"{tz_name} ({tz_shift})")

        await ctx.send(embed=embed_result)

    @cog_ext.cog_subcommand(base="time", subcommand_group="zone", name="check",
                            options=[
                                create_option(
                                    name="member",
                                    description="Server member with set timezone",
                                    option_type=discord.Member,
                                    required=False,
                                )
                            ])
    async def timezone(self, ctx: SlashContext, member: discord.Member = None):
        """Shows the timezone of the member (or yours by default)"""
        if member and not isinstance(member, discord.Member):
            raise commands.BadArgument(f"Failed to get member '{member}' info!")

        await ctx.defer(hidden=True)
        logger.debug(f"{ctx.author} checking {member or ctx.author} timezone")
        
        member_timezone = await self.get_timezone_for_member(member or ctx.author)

        if member_timezone is not None:
            await ctx.send(f"{member.mention if member else 'Your'} timezone is '{member_timezone}'",
                           hidden=True)
        else:
            if member is not None:
                await ctx.send(f"Sorry, {member.mention} didn't set the timezone",
                               hidden=True)
            else:
                await ctx.send("You didn't set your timezone. User '/time zone set' for this", hidden=True)
    
    @cog_ext.cog_subcommand(base="time", subcommand_group="zone", name="set",
                            options=[
                                create_option(
                                    name="timezone",
                                    description="Abbreviation of the timezone that you want to set",
                                    option_type=str,
                                    required=True,
                                )
                            ])
    async def set_timezone(self, ctx: SlashContext, timezone: str):
        """Sets your timezone to the one you stated"""
        await ctx.defer(hidden=True)
        logger.info(f"{ctx.author} trying to set timezone '{timezone}'")

        diff, result_tz, _ = await self.get_diff_from_utc(ctx, timezone)
        if diff is None:
            raise commands.BadArgument(f"Sorry, looks like '{timezone}' is not a valid timezone abbreviation")

        await self.set_timezone_for_member(ctx.author, result_tz)
        await ctx.send(f"Your timezone is set to '{result_tz}' ({self.timedelta_to_utc_str(diff)})", hidden=True)

    @cog_ext.cog_subcommand(base="time", subcommand_group="zone", name="reset")
    async def remove_timezone(self, ctx: SlashContext):
        """Resets your set timezone"""
        await ctx.defer(hidden=True)
        logger.info(f"{ctx.author} trying to reset timezone")

        if await self.clear_timezone_for_member(ctx.author):
            await ctx.send("Your timezone was removed!", hidden=True)
        else:
            await ctx.send("You have no timezone to remove", hidden=True)

    @staticmethod
    def get_invalid_timezone_response(timezone: Union[str, discord.Member], input_type: TimezoneInputType, sender: discord.Member):
        if input_type == TimezoneInputType.ValidUser:
            if isinstance(timezone, discord.Member):
                timezone = "you" if timezone == sender else timezone.mention
            elif str(timezone).upper() == "ME":
                timezone = "you"
            return f"Looks like {timezone} did't set the timezone!"

        return f"{timezone} is not a valid member or timezone abbreviation"

    @staticmethod
    def strtime_to_datetime(time: str):
        """
        Tries to convert a string time to the datetime
        In case of success returns tulpe <time: datetime, is_using_12_hours: bool>
        In case of fail returns None, None
        """

        # first numbers are considered to be hours
        before_hours, hours, leftover = Conversions.split_on_first_numbers(time.upper())
        if hours is None:
            return None, None

        time_format = before_hours + "%H"
        is_using_12_hours = False

        # try to scan for minutes
        if len(leftover) > 0:
            # try search for the minutes
            before_minutes, minutes, leftover = Conversions.split_on_first_numbers(leftover)
            if minutes is not None:
                time_format += before_minutes
                time_format += "%M"
            else:
                leftover = before_minutes

        # try to scan for AM/PM
        if len(leftover) > 0:
            # Period should be a separate word
            period = re.search(r'(\A|\W)(AM|PM)(\Z|\W)', leftover)
            if period is not None:
                time_format += leftover[:period.start()]
                # replace the actual period with marker of the period
                period_string = period.string.replace('AM', '%p').replace('PM', '%p')
                time_format += period_string
                # replace 24 hours format with 12 hours format
                time_format = time_format.replace('%H', '%I', 1)
                is_using_12_hours = True
                leftover = leftover[period.end():]

        # add leftover to the format
        if len(leftover) > 0:
            time_format += leftover

        try:
            result = datetime.strptime(time.upper(), time_format)
        except ValueError:
            return None, None

        # using current day to avoid problems with dates less than starting one
        result = datetime.utcnow().replace(hour=result.hour, minute=result.minute)
        return result, is_using_12_hours

    @staticmethod
    def split_on_first_numbers(string: str):
        """
        Splits string on three parts <before_numbers, numbers, after_numbers
        If there is no numbers, result will be <string, None, None>
        """
        numbers_start = re.search(r'\d', string)
        if numbers_start is None:
            return string, None, None

        before_numbers = string[:numbers_start.start()]

        numbers_end = re.search(r'\d\D', string)
        if numbers_end is None:
            return before_numbers, string[numbers_start.start():], ''

        # start + 1 for \D symbol
        numbers = string[numbers_start.start():numbers_end.start() + 1]
        after_numbers = string[numbers_end.start() + 1:]
        return before_numbers, numbers, after_numbers

    @staticmethod
    def timedelta_to_utc_str(delta: timedelta) -> str:
        """converts timedelta to the string like 'UTC+delta'"""
        hours, seconds = divmod(delta.total_seconds(), 3600)
        minutes, _ = divmod(seconds, 60)
        return f"UTC{int(hours):+}" + (f":{int(minutes)}" if minutes != 0 else "")

    async def get_diff_from_utc(self, ctx: commands.context.Context, timezone_or_member: Union[str, discord.Member]) -> (Optional[timedelta], Optional[str], TimezoneInputType):
        """
        Returns difference between stated timezone and UTC+0 as timedelta, timezone name, and was the first argument a member or not
        If timezone is a member, will try to get a timezone from DB for this member
        If timezone is invalid or member don't have a timezone set, result will be None, None, bool
        Can raise TimezonedbException in case of errors with TimezoneDB
        """
        try:
            if isinstance(timezone_or_member, discord.Member):
                member = timezone_or_member
            elif timezone_or_member.upper() == "ME":
                member = ctx.author
            else:
                member = await commands.MemberConverter().convert(ctx, timezone_or_member)
            timezone = await self.get_timezone_for_member(member)
            if timezone is None:
                return None, None, TimezoneInputType.ValidUser

            result_type = TimezoneInputType.ValidUser
        except commands.MemberNotFound:
            timezone = timezone_or_member.upper()
            if len(timezone) > 5: # too long for timezone abbreviation
                return None, None, TimezoneInputType.InvalidData
            if re.search(r'\W', timezone): # not a word character shouldn't be in the timezone
                return None, None, TimezoneInputType.InvalidData
            if re.search(r'\d', timezone): # no numbers either
                return None, None, TimezoneInputType.InvalidData

            result_type = TimezoneInputType.ValidTimezone
        
        if timezone == "PST":
            timezone = "PDT"
        if timezone == "CST":
            timezone = "CDT"
        if timezone == "EST":
            timezone = "EDT"

        diff = await self.timezones_diff("UTC", timezone)
        result_type = result_type if diff is not None else TimezoneInputType.InvalidData
        return diff, timezone if diff is not None else None, result_type

    async def timezones_diff(self, timezone_from: str, timezone_to: str):
        """
        Returns difference between timezones as timedelta, if timezones are valid, and None if not
        timezone_from and timezone_to should be timezone abbreviations
        Can raise TimezonedbException in case of errors with TimezoneDB
        """
        # TimezoneDB not supports shifts properly, so they are handled manually
        timezone_from, from_shift = self.split_timezone_and_shift(timezone_from)
        if timezone_from is None:
            return None
        timezone_to, to_shift = self.split_timezone_and_shift(timezone_to)
        if timezone_to is None:
            return None

        if timezone_from != timezone_to:
            # We're force timezones to upper case since we're expecting abbreviation and TimezoneDB API is case sensitive
            # Technically, we can also work with cities, but then some adjustments are needed
            diff = await self.get_diff_from_timezonedb(timezone_from.upper(), timezone_to.upper())
        else:
            diff = 0

        if diff is not None:
            diff = diff + to_shift - from_shift
            diff = timedelta(seconds=diff)

        return diff

    @staticmethod
    def split_timezone_and_shift(timezone: str):
        """
        Splits timezone with shift on just timezone and shift in seconds
        If there is no shift in timezone, timezone will be left as is
        If shift is invalid, then <None, None> will be returned
        For example, GMT+1 will return <"GMT", 3600>
        """
        separator = None
        offset_is_positive = True
        if '+' in timezone:
            separator = '+'
        elif '-' in timezone:
            separator = '-'
            offset_is_positive = False
        elif '−' in timezone:
            separator = '−'
            offset_is_positive = False

        if separator is None:
            return timezone, 0

        [timezone, offset] = timezone.split(separator)
        offset, _ = Conversions.strtime_to_datetime(offset)
        if offset is None:
            return None, None
        offset = timedelta(hours=offset.hour, minutes=offset.minute)

        if offset_is_positive:
            offset = offset.seconds
        else:
            offset = -offset.seconds

        return timezone, offset

    async def get_diff_from_timezonedb(self, timezone_from: str, timezone_to: str):
        """
        Sends request to TimezoneDB to get the difference between statet timezones
        Returns difference in seconds in case if success, and None in case of invalid timezones
        Logs and raises TimezonedbException in case when something else went wrong
        """
        params = {
            "key": self.timedb_key,
            "format": "json",
            "from": str(timezone_from),
            "to": str(timezone_to)
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get("http://api.timezonedb.com/v2.1/convert-time-zone", params=params) as response:
                    if not response.ok:
                        logger.error(f"Got error {response.status}: {response.reason} while processing request!\n" +
                                    f"Request is '{response.url}'")
                        raise TimezonedbException()

                    result = await response.json()
                    if result["status"] != "OK":
                        message = result["message"]
                        if message == "From Time Zone: Invalid zone name or abbreviation.":
                            return None
                        if message == "To Time Zone: Invalid zone name or abbreviation.":
                            return None

                        logger.error(f"TimezoneDB request status is not OK. Message: '{message}'")
                        raise TimezonedbException()

                    return int(result["offset"])
                    
        except Exception as exception:
            logger.error(exception)
            raise TimezonedbException()

    @staticmethod
    async def set_timezone_for_member(member: discord.Member, timezone: str):
        timezone_entry, was_created = await Timezones.get_or_create(member_id=member.id, defaults={"member_timezone": timezone})
        if not was_created:
            timezone_entry.member_timezone = timezone
            await timezone_entry.save()

    @staticmethod
    async def clear_timezone_for_member(member: discord.Member) -> bool:
        """Returns True if member had timezone and it was removed and false if member had no entry"""
        timezone_entry = await Timezones.get_or_none(member_id=member.id)
        if timezone_entry is None:
            return False

        await timezone_entry.delete()
        return True

    @staticmethod
    async def get_timezone_for_member(member: discord.Member) -> Optional[str]:
        timezone_entry = await Timezones.get_or_none(member_id=member.id)
        if timezone_entry is None:
            return None
        return timezone_entry.member_timezone


def setup(bot):
    bot.add_cog(Conversions(bot))
