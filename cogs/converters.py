import configparser
import logging
import re
from datetime import datetime, timedelta

import discord
import requests
from discord.ext import commands

logger = logging.getLogger(__name__)

class TimezonedbException(Exception):
    """Exception class for all errors connected with TimezoneDB"""
    pass

class Conversions(commands.Cog):
    """Timezone conversions (and maybe temperature later?)"""

    def __init__(self, bot: commands.Bot, config: configparser.ConfigParser):
        self.bot = bot
        self.timedb_key = None
        try:
            self.timedb_key = config["AUTH"]["timezonedb_key"]
        except KeyError:
            logger.warning("Can't read TimezoneDB API key!")
    
    @commands.command(help="Converts time between timezones. Use '!help convert' for details",
                description = "Use as '!convert time timezone_from timezone_to' "
                "(for example '!convert 9:15 AM CST GMT+3')\n"
                "Time supports both 12h and 24h formats\n"
                "Use abbreviation or UTC offset to specify timezones.\n"
                "Take into account that some timezones are sharing one abbreviation.")
    async def convert(self, ctx: commands.context.Context, *args):
        if self.timedb_key is None:
            logger.warning("Can't handle convert command, TimezoneDB key is missing")
            await ctx.send("Sorry, time conversion is unavailable now. Ping developers!")
            return
        
        if len(args) < 3:
            await ctx.send("There should be at least 3 arguments for time conversion (time timezone_from timezone_to)")
            return

        timezone_to = args[-1]
        timezone_from = args[-2]
        time_str = ' '.join(args[0:-2])
        time, should_use_12_hours = Conversions.strtime_to_datetime(time_str)
        if time is None:
            await ctx.send(f"Can't parse '{time_str}' time. Is it valid?")
            return
        
        try:
            diff = self.timezones_diff(timezone_from, timezone_to)
        except TimezonedbException as exception:
            await ctx.send("Sorry, time conversion is unavailable now. Ping developers!")
            return

        if diff is None:
            await ctx.send(f"Can't get time difference between '{timezone_from}' and '{timezone_to}'. Are they valid timezones?")
            return
        
        result_time = time + diff

        # set result as 12/24 hours time depending on request format
        if should_use_12_hours:
            result_time = f'{result_time:%I:%M %p}'
        else:
            result_time = f'{result_time:%H:%M}'
        
        await ctx.send(f'{result_time} (from {timezone_from} to {timezone_to})')
    
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


    def timezones_diff(self, timezone_from: str, timezone_to: str):
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
            diff = self.get_diff_from_timezonedb(timezone_from.upper(), timezone_to.upper())
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


    def get_diff_from_timezonedb(self, timezone_from: str, timezone_to: str):
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
            diff_request = requests.get("http://api.timezonedb.com/v2.1/convert-time-zone", params=params)
        except Exception as exception:
            logger.error(exception)
            raise TimezonedbException()

        if not diff_request.ok:
            logger.error(f"Got error {diff_request.status_code}: {diff_request.reason} while processing request!\n" +
            f"Request is '{diff_request.url}'\n" +
            f"Response is '{diff_request.text}'")
            raise TimezonedbException()
        
        result = diff_request.json()
        if result["status"] != "OK":
            message = result["message"]
            if message == "From Time Zone: Invalid zone name or abbreviation.":
                return None
            if message == "To Time Zone: Invalid zone name or abbreviation.":
                return None

            logger.error(f"TimezoneDB request status is not OK. Message: '{result['message']}'")
            raise TimezonedbException()

        return int(result["offset"])
