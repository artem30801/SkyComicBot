"""Backports of newer Discord features missing from discord.py 1.7.3.

discord.py 1.7.3 predates Discord's "timeout" (Moderate Members) feature, so the
permission flag and the member API for it simply don't exist. Rather than do a
risky full upgrade to discord.py 2.x, we monkey-patch in just the two pieces we
need:

* ``discord.Permissions.moderate_members`` so the flag shows up in permission
  listings / checks (see ``cogs/service.py`` ``/bot permissions``).
* ``discord.Member.timeout(...)`` -- a barebones port of the 2.x method that
  sends the ``communication_disabled_until`` field via the existing HTTP route.

Call :func:`apply_patches` once, as early as possible, before any cog runs.
"""

import datetime
import logging

import discord
from discord.flags import flag_value

logger = logging.getLogger(__name__)

# https://discord.com/developers/docs/topics/permissions -- MODERATE_MEMBERS
MODERATE_MEMBERS = 1 << 40

# Discord rejects timeouts longer than 28 days into the future.
MAX_TIMEOUT = datetime.timedelta(days=28)


def _patch_permissions():
    if "moderate_members" in discord.Permissions.VALID_FLAGS:
        return  # already present (patched, or a newer discord.py)
    discord.Permissions.moderate_members = flag_value(lambda self: MODERATE_MEMBERS)
    discord.Permissions.VALID_FLAGS["moderate_members"] = MODERATE_MEMBERS

    # Administrator (and guild ownership) collapse to Permissions.all(), which is a
    # hardcoded bitmask predating moderate_members -- so admins would wrongly report
    # the new flag as denied. Extend all() to include it so the collapse is honest.
    all_value = discord.Permissions.all().value | MODERATE_MEMBERS
    discord.Permissions.all = classmethod(lambda cls: cls(all_value))


async def _member_timeout(self, until, *, reason=None):
    """Times the member out until ``until``, or clears it when ``until`` is None.

    ``until`` may be a timezone-aware ``datetime`` (an absolute moment) or a
    ``timedelta`` (relative to now). Pass ``None`` to remove an active timeout.
    """
    if until is None:
        payload = None
    else:
        if isinstance(until, datetime.timedelta):
            until = datetime.datetime.now(datetime.timezone.utc) + until
        if until - datetime.datetime.now(datetime.timezone.utc) > MAX_TIMEOUT:
            raise ValueError("Timeouts cannot be longer than 28 days.")
        payload = until.isoformat()

    await self._state.http.edit_member(
        self.guild.id, self.id,
        communication_disabled_until=payload, reason=reason,
    )


def _patch_member_timeout():
    if not hasattr(discord.Member, "timeout"):
        discord.Member.timeout = _member_timeout


def apply_patches():
    """Apply all backport patches. Safe to call more than once."""
    _patch_permissions()
    _patch_member_timeout()
    logger.info("Applied discord.py 1.7.3 backport patches (timeout support)")
