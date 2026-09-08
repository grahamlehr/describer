"""Display on/off schedule, HDMI power control and output mode."""

from __future__ import annotations

import asyncio
import logging
import shutil
from datetime import datetime, time

from .config import WEEKDAYS, DisplayConfig, ScheduleConfig
from .profiles import in_window, parse_hhmm

log = logging.getLogger(__name__)

#: The connector cage drives on a Pi 4. Both HDMI sockets are HDMI-A-*; the
#: one nearest the USB-C power socket is HDMI-A-1.
OUTPUT = "HDMI-A-1"

#: wlr-randr mode strings for each forced resolution. ``auto`` restores the
#: monitor's preferred mode.
MODES: dict[str, str] = {"1080p": "1920x1080", "720p": "1280x720"}


def window_for(config: ScheduleConfig, moment: datetime) -> tuple[time, time] | None:
    """The (on, off) window in force on ``moment``'s weekday, or None if off all day."""
    day = WEEKDAYS[moment.weekday()]
    if day in config.per_weekday:
        override = config.per_weekday[day]
        if override is None:
            return None
        return parse_hhmm(override["on_time"]), parse_hhmm(override["off_time"])
    return parse_hhmm(config.on_time), parse_hhmm(config.off_time)


def is_display_on(config: ScheduleConfig, moment: datetime | None = None) -> bool:
    """Whether the board should be awake right now."""
    if not config.enabled:
        return True
    moment = moment or datetime.now()
    window = window_for(config, moment)
    if window is None:
        return False
    on_at, off_at = window
    return in_window(on_at, off_at, moment.time())


async def set_display_power(config: ScheduleConfig, on: bool) -> None:
    """Blank or wake the HDMI output. A no-op when the tool is unavailable."""
    method = config.power_method
    if method == "none":
        return
    if method == "wlr-randr":
        if not shutil.which("wlr-randr"):
            log.debug("wlr-randr not present; skipping display power change")
            return
        command = ["wlr-randr", "--output", OUTPUT, "--on" if on else "--off"]
    else:
        if not shutil.which("vcgencmd"):
            log.debug("vcgencmd not present; skipping display power change")
            return
        command = ["vcgencmd", "display_power", "1" if on else "0"]

    if await _run(command):
        log.info("Display power %s via %s", "on" if on else "off", method)


def display_mode_command(config: DisplayConfig) -> list[str]:
    """The wlr-randr invocation that puts the output in the configured mode."""
    command = ["wlr-randr", "--output", OUTPUT]
    if config.resolution == "auto":
        return [*command, "--preferred"]
    return [*command, "--mode", MODES[config.resolution]]


async def set_display_mode(config: DisplayConfig) -> bool:
    """Force the HDMI output to the configured resolution.

    Returns True once the mode is in force, or when there is nothing to do:
    without wlr-randr (development on a Mac) the request is simply dropped.
    False means the compositor was not reachable, usually because cage is not
    up yet; the caller retries later.
    """
    if not shutil.which("wlr-randr"):
        log.debug("wlr-randr not present; leaving the display mode alone")
        return True
    command = display_mode_command(config)
    if await _run(command):
        log.info("Display mode set to %s", config.resolution)
        return True
    return False


async def _run(command: list[str]) -> bool:
    """Run a display tool, logging a failure at WARNING. True on success."""
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await process.communicate()
    except OSError as exc:
        log.warning("Could not run %s: %s", command[0], exc)
        return False
    if process.returncode:
        log.warning("%s failed: %s", " ".join(command), stderr.decode().strip())
        return False
    return True
