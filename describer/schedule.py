"""Display on/off schedule and HDMI power control."""

from __future__ import annotations

import asyncio
import logging
import shutil
from datetime import datetime, time

from .config import WEEKDAYS, ScheduleConfig

log = logging.getLogger(__name__)


def _parse(hhmm: str) -> time:
    hours, minutes = (int(part) for part in hhmm.split(":", 1))
    return time(hours, minutes)


def window_for(config: ScheduleConfig, moment: datetime) -> tuple[time, time] | None:
    """The (on, off) window in force on ``moment``'s weekday, or None if off all day."""
    day = WEEKDAYS[moment.weekday()]
    if day in config.per_weekday:
        override = config.per_weekday[day]
        if override is None:
            return None
        return _parse(override["on_time"]), _parse(override["off_time"])
    return _parse(config.on_time), _parse(config.off_time)


def is_display_on(config: ScheduleConfig, moment: datetime | None = None) -> bool:
    """Whether the board should be awake right now."""
    if not config.enabled:
        return True
    moment = moment or datetime.now()
    window = window_for(config, moment)
    if window is None:
        return False
    on_at, off_at = window
    now = moment.time()
    if on_at == off_at:
        return True
    if on_at < off_at:
        return on_at <= now < off_at
    # Window wraps midnight, e.g. on 06:00 / off 23:00 inverted to 23:00-06:00.
    return now >= on_at or now < off_at


async def set_display_power(config: ScheduleConfig, on: bool) -> None:
    """Blank or wake the HDMI output. A no-op when the tool is unavailable."""
    method = config.power_method
    if method == "none":
        return
    if method == "wlr-randr":
        if not shutil.which("wlr-randr"):
            log.debug("wlr-randr not present; skipping display power change")
            return
        command = ["wlr-randr", "--output", "HDMI-A-1", "--on" if on else "--off"]
    else:
        if not shutil.which("vcgencmd"):
            log.debug("vcgencmd not present; skipping display power change")
            return
        command = ["vcgencmd", "display_power", "1" if on else "0"]

    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await process.communicate()
        if process.returncode:
            log.warning("Display power command failed: %s", stderr.decode().strip())
        else:
            log.info("Display power %s via %s", "on" if on else "off", method)
    except OSError as exc:
        log.warning("Could not run %s: %s", command[0], exc)
