"""Time-of-day profiles: which board is on the screen at this hour.

A profile is a named window of the week carrying a sparse override of the
config (see Addendum 6). This module decides which one is in force and hands
back the config as it then stands; the merge itself lives in :mod:`config`,
which cannot import this one.

Profiles choose content. ``schedule:`` still owns the power: while the display
is off no profile is active and nothing is fetched.
"""

from __future__ import annotations

import logging
from datetime import datetime, time, timedelta

from .config import WEEKDAYS, Config, ProfileConfig, merge_profile

log = logging.getLogger(__name__)

#: How far ahead :func:`next_change` will look for the next switch.
HORIZON_DAYS = 8


def parse_hhmm(text: str) -> time:
    """Read "14:32" as a :class:`~datetime.time`; the model enforces the pattern."""
    hours, minutes = (int(part) for part in text.split(":", 1))
    return time(hours, minutes)


def in_window(on: time, off: time, now: time) -> bool:
    """Is ``now`` inside the window ``on``–``off``, which may wrap midnight?"""
    if on == off:
        return True
    if on < off:
        return on <= now < off
    return now >= on or now < off


def profile_matches(profile: ProfileConfig, moment: datetime) -> bool:
    """Does this profile claim ``moment``?"""
    if WEEKDAYS[moment.weekday()] not in profile.days:
        return False
    return in_window(parse_hhmm(profile.start), parse_hhmm(profile.end), moment.time())


def active_profile(
    config: Config,
    moment: datetime | None = None,
    *,
    forced: str | None = None,
) -> ProfileConfig | None:
    """The profile in force, or None when the base config stands alone.

    The first entry matching wins, so a profile earlier in the list beats a
    later one covering the same hour. ``forced`` pins one by name for testing
    and ignores both the clock and the ``enabled`` switch, because seeing the
    evening board at eleven in the morning is a deliberate act. A forced name
    that no longer exists falls through to the base config rather than raising:
    the board must keep working after a profile is deleted.
    """
    entries = config.profiles.entries
    if forced is not None:
        return next((entry for entry in entries if entry.name == forced), None)
    if not config.profiles.enabled:
        return None
    moment = moment or datetime.now()
    return next((entry for entry in entries if profile_matches(entry, moment)), None)


def resolve(
    config: Config,
    moment: datetime | None = None,
    *,
    forced: str | None = None,
) -> Config:
    """``config`` with whichever profile is in force applied.

    A config with no profiles resolves to itself, unchanged and un-copied.
    """
    profile = active_profile(config, moment, forced=forced)
    if profile is None:
        return config
    return merge_profile(config, profile)


def next_change(
    config: Config,
    moment: datetime | None = None,
    *,
    forced: str | None = None,
) -> tuple[datetime, str | None] | None:
    """When the board next changes profile, and what takes over.

    None while nothing is going to change: no profiles, none of them enabled,
    or one pinned by hand. Only the entries' own edges can change the answer,
    so those are the only moments worth testing.
    """
    if forced is not None or not config.profiles.enabled:
        return None
    moment = moment or datetime.now()
    current = active_profile(config, moment)
    current_name = current.name if current else None

    midnight = moment.replace(hour=0, minute=0, second=0, microsecond=0)
    edges: set[datetime] = set()
    for profile in config.profiles.entries:
        for text in (profile.start, profile.end):
            edge = parse_hhmm(text)
            for day in range(HORIZON_DAYS):
                when = (midnight + timedelta(days=day)).replace(hour=edge.hour, minute=edge.minute)
                if when > moment:
                    edges.add(when)

    for when in sorted(edges):
        upcoming = active_profile(config, when)
        name = upcoming.name if upcoming else None
        if name != current_name:
            return when, name
    return None
