"""Configuration model: load, validate and save `config.yaml`.

The YAML file is the single source of truth. The admin page reads and writes
the same file through :func:`load_config` / :func:`save_config`.
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

log = logging.getLogger(__name__)

#: Search order for the config file. The first existing path wins; if none
#: exist we fall back to the repo-root path so a fresh checkout self-heals.
CONFIG_PATHS: tuple[Path, ...] = (
    Path("config.yaml"),
    Path("/etc/describer/config.yaml"),
)

Weekday = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
WEEKDAYS: tuple[Weekday, ...] = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

CrsCode = Annotated[str, Field(min_length=3, max_length=3, pattern=r"^[A-Za-z]{3}$")]
HHMM = Annotated[str, Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")]
#: A platform as printed on the board: "1", "9B", "13". Held upper-case.
Platform = Annotated[str, Field(min_length=1, max_length=5)]


class StationConfig(BaseModel):
    """One half of the board: a station shown in one mode."""

    crs: CrsCode
    mode: Literal["departures", "arrivals"] = "departures"
    #: Overrides the station name reported by the API. Useful for long names.
    name: str | None = None
    #: Number of service rows to render for this station.
    rows: int = Field(default=8, ge=1, le=20)
    #: Announce services for this station (requires announcements.enabled).
    announce: bool = True
    #: Show only these platforms, e.g. ["1", "2A"]. Empty shows every platform.
    #: Matching ignores case and surrounding space.
    platforms: list[Platform] = Field(default_factory=list)
    #: Whether a service with no platform survives the filter above. Both feeds
    #: withhold a platform until it is confirmed, so a filtered board is empty
    #: for most of the hour with this off — and carries other platforms' trains
    #: with it on.
    show_unplatformed: bool = False
    #: Minutes needed to reach the platform. A train leaving (or, on an
    #: arrivals board, arriving) sooner than this is dropped: it cannot be
    #: caught from where the board is read, so printing it only pushes the
    #: trains that can be caught off the bottom. 0 shows everything.
    walk_time: int = Field(default=0, ge=0, le=120)

    @field_validator("crs")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper()

    @field_validator("platforms", mode="before")
    @classmethod
    def _clean_platforms(cls, values: object) -> object:
        """Normalise to the form :meth:`accepts_platform` compares against.

        Runs before the item type, so that a stray empty entry from the admin
        page's comma-separated field is dropped rather than rejected.
        """
        if not isinstance(values, list):
            return values
        seen: set[str] = set()
        result: list[str] = []
        for value in values:
            if not isinstance(value, str):
                return values
            token = value.strip().upper()
            if not token or token in seen:
                continue
            seen.add(token)
            result.append(token)
        return result

    def accepts_platform(self, platform: str | None) -> bool:
        """Is a service standing at ``platform`` wanted on this board?"""
        if not self.platforms:
            return True
        if platform is None or not platform.strip():
            return self.show_unplatformed
        return platform.strip().upper() in self.platforms

    def accepts_time(self, seconds_until: float | None) -> bool:
        """Is a service ``seconds_until`` away still worth showing?

        A service whose time we cannot read (no estimate, no schedule) is
        kept: the walk time is a judgement about a known departure, and
        dropping the unknowns would take trains off the board for the wrong
        reason.
        """
        if not self.walk_time or seconds_until is None:
            return True
        return seconds_until >= self.walk_time * 60


#: A colour as the admin page's picker writes it: "#rrggbb", held lower-case.
Colour = Annotated[str, Field(pattern=r"^#[0-9a-fA-F]{6}$")]


class ThemeColoursConfig(BaseModel):
    """One theme's palette, in the vocabulary every theme shares.

    A role left unset (``None``) means the theme's own stylesheet decides, so
    the CSS stays the source of truth and a redesign still reaches a board
    that never touched the picker. A theme uses the roles it has: thameslink
    has no on-time colour, because it counts down in white, and ignores
    ``on_time`` if one is written here by hand.
    """

    background: Colour | None = None
    text: Colour | None = None
    dim_text: Colour | None = None
    accent: Colour | None = None
    on_time: Colour | None = None
    late: Colour | None = None
    cancelled: Colour | None = None

    @field_validator("*")
    @classmethod
    def _lower(cls, v: str | None) -> str | None:
        return v.lower() if isinstance(v, str) else v


class ModernThemeConfig(BaseModel):
    colours: ThemeColoursConfig = ThemeColoursConfig()


class ThameslinkThemeConfig(BaseModel):
    colours: ThemeColoursConfig = ThemeColoursConfig()


class CrtThemeConfig(BaseModel):
    phosphor: Literal["amber", "green"] = "amber"
    scanlines: bool = True
    #: Barrel distortion and bloom. Disable if the Pi struggles.
    curvature: bool = True


class SplitflapThemeConfig(BaseModel):
    #: Play a click sound as flaps settle.
    click_sound: bool = False
    #: Milliseconds per flap step; lower is faster and more demanding.
    flap_ms: int = Field(default=40, ge=10, le=200)


class NseThemeConfig(BaseModel):
    #: Colour of a disc's lit face.
    dot_colour: Literal["yellow", "white", "green"] = "yellow"
    #: Rattle as the discs flip.
    click_sound: bool = False


class ThemesConfig(BaseModel):
    modern: ModernThemeConfig = ModernThemeConfig()
    crt: CrtThemeConfig = CrtThemeConfig()
    splitflap: SplitflapThemeConfig = SplitflapThemeConfig()
    nse: NseThemeConfig = NseThemeConfig()
    thameslink: ThameslinkThemeConfig = ThameslinkThemeConfig()


ThemeName = Literal["modern", "crt", "splitflap", "1990s", "nse", "led-matrix", "thameslink"]


class DisplayConfig(BaseModel):
    #: Active theme; switchable live from /admin.
    theme: ThemeName = "modern"
    #: Show the live clock in each board header.
    clock: bool = True
    #: Expand the first row to scroll its calling points.
    show_calling_points: bool = True
    #: HDMI output mode. ``auto`` leaves the monitor's preferred mode alone;
    #: a 4K panel makes the Pi 4 composite four times the pixels it needs, so
    #: forcing 1080p or 720p keeps the animations smooth. Applied live with
    #: wlr-randr while the kiosk is running under cage.
    resolution: Literal["auto", "1080p", "720p"] = "auto"
    themes: ThemesConfig = ThemesConfig()


SourceName = Literal["rdm", "rtt"]


class RdmSourceConfig(BaseModel):
    """Rail Data Marketplace LDBWS. Key comes from RDM_API_KEY."""

    #: LDBWS base URL (no trailing slash). This is the combined
    #: "Live Arrival and Departure Boards" product; departures and arrivals are
    #: two readings of its one response. See ldbws.BOARD_ENDPOINT.
    base_url: str = "https://api1.raildata.org.uk/1010-live-arrival-and-departure-boards-arr-and-dep1_1/LDBWS/api/20220120"
    #: HTTP timeout in seconds for a single request.
    timeout: float = Field(default=10.0, ge=1.0, le=60.0)

    @field_validator("base_url")
    @classmethod
    def _strip_slash(cls, v: str) -> str:
        return v.rstrip("/")


class RttSourceConfig(BaseModel):
    """Realtime Trains (next-generation API). Token comes from RTT_TOKEN."""

    base_url: str = "https://data.rtt.io"
    timeout: float = Field(default=10.0, ge=1.0, le=60.0)
    #: Show replacement bus (and ship) services, which RTT lists alongside trains.
    include_buses: bool = False
    #: Board rows given a calling-points call; each one is its own round trip.
    #: The board only expands the first row, so a small number goes a long way.
    detail_rows: int = Field(default=3, ge=1, le=12)
    #: Minutes of departures to ask for. One call returns the whole window.
    time_window: int = Field(default=60, ge=30, le=360)
    #: Seconds between polls while RTT is live. Its free tier allows 10 calls a
    #: minute and 100 an hour, which a 30 s board would exhaust in minutes.
    min_poll_interval: int = Field(default=120, ge=60, le=600)

    @field_validator("base_url")
    @classmethod
    def _strip_slash(cls, v: str) -> str:
        return v.rstrip("/")


class SourcesConfig(BaseModel):
    """Which upstreams to use, and when to swap between them."""

    #: The source used while it is healthy.
    primary: SourceName = "rdm"
    #: Used after the primary fails repeatedly. null disables failover.
    fallback: SourceName | None = "rtt"
    #: Consecutive primary failures, across all stations, before switching.
    failover_after: int = Field(default=3, ge=1, le=20)
    #: Seconds between quiet background retries of the primary once failed over.
    recover_after: int = Field(default=300, ge=30, le=86400)
    #: Seconds between polls per station. Never below 20 s (API fair use).
    poll_interval: int = Field(default=30, ge=20, le=600)
    #: A board older than this many seconds is flagged "data stale" on screen.
    stale_after: int = Field(default=120, ge=30, le=3600)
    rdm: RdmSourceConfig = RdmSourceConfig()
    rtt: RttSourceConfig = RttSourceConfig()

    @model_validator(mode="after")
    def _distinct_sources(self) -> SourcesConfig:
        if self.fallback is not None and self.fallback == self.primary:
            raise ValueError("sources.fallback must differ from sources.primary")
        return self


class AnnouncementsConfig(BaseModel):
    enabled: bool = True
    #: Announce a service when it is this many seconds from its expected time.
    lead_time: int = Field(default=120, ge=0, le=1800)
    #: Playback volume, 0.0 to 1.0.
    volume: float = Field(default=0.8, ge=0.0, le=1.0)
    #: Piper voice name; the .onnx file must exist in voices_dir.
    voice: str = "en_GB-alan-medium"
    #: Directory holding Piper voice .onnx/.onnx.json files.
    voices_dir: str = "voices"
    #: Piper executable; must be on PATH or an absolute path.
    piper_binary: str = "piper"
    #: Where synthesised clips are cached, keyed by a hash of the text.
    cache_dir: str = "tts-cache"
    #: Audio output: HDMI or the 3.5 mm jack.
    audio_device: Literal["hdmi", "jack", "default"] = "hdmi"
    #: Two-tone chime before each announcement.
    chime: bool = True
    #: Announce delays as they appear.
    announce_delays: bool = True
    #: Announce cancellations with the standard apology.
    announce_cancellations: bool = True
    #: Maximum calling points read out before "and <final destination>".
    max_calling_points: int = Field(default=8, ge=1, le=30)


class ScheduleConfig(BaseModel):
    """Daily display on/off times.

    ``on_time`` before ``off_time`` means a daytime window; the reverse means
    the window wraps midnight. ``per_weekday`` overrides the default window
    for named days; a day mapped to ``null`` is off all day.
    """

    enabled: bool = False
    on_time: HHMM = "06:00"
    off_time: HHMM = "23:00"
    per_weekday: dict[Weekday, dict[str, str] | None] = Field(default_factory=dict)
    #: How the HDMI output is blanked on the Pi.
    power_method: Literal["wlr-randr", "vcgencmd", "none"] = "wlr-randr"

    @model_validator(mode="after")
    def _check_windows(self) -> ScheduleConfig:
        for day, window in self.per_weekday.items():
            if window is None:
                continue
            missing = {"on_time", "off_time"} - set(window)
            if missing:
                raise ValueError(f"schedule.per_weekday.{day} is missing {sorted(missing)}")
        return self


class DisplayOverride(BaseModel):
    """The display keys a profile may set; anything unset keeps the base value.

    ``resolution`` is deliberately absent: it is a hardware action with a retry
    loop behind it, and no template needs the monitor to change mode at half
    past six.
    """

    theme: ThemeName | None = None
    clock: bool | None = None
    show_calling_points: bool | None = None
    #: Theme options and palettes, merged key by key onto the base block. Held
    #: loosely because a theme's options are its own; the merged result is
    #: validated against ThemesConfig before the profile is accepted.
    themes: dict[str, dict[str, Any]] | None = None


class AnnouncementsOverride(BaseModel):
    """The announcement keys a profile may set.

    Piper's paths (``voices_dir``, ``piper_binary``, ``cache_dir``) are not
    here: they describe the machine, not the hour.
    """

    enabled: bool | None = None
    lead_time: int | None = Field(default=None, ge=0, le=1800)
    volume: float | None = Field(default=None, ge=0.0, le=1.0)
    voice: str | None = None
    audio_device: Literal["hdmi", "jack", "default"] | None = None
    chime: bool | None = None
    announce_delays: bool | None = None
    announce_cancellations: bool | None = None
    max_calling_points: int | None = Field(default=None, ge=1, le=30)


class ProfileConfig(BaseModel):
    """A named window of the week carrying a sparse override of the config.

    ``start == end`` means the whole day; ``start`` after ``end`` wraps
    midnight, exactly as :class:`ScheduleConfig` does.
    """

    name: str = Field(min_length=1, max_length=40)
    days: list[Weekday] = Field(default_factory=lambda: list(WEEKDAYS), min_length=1)
    start: HHMM = "00:00"
    end: HHMM = "00:00"
    #: Replaces the station list wholesale when present. Merging two lists
    #: positionally would have to decide what a half-specified second station
    #: means, and what a profile wants is a different station, not a tweaked one.
    stations: list[StationConfig] | None = Field(default=None, min_length=1, max_length=2)
    display: DisplayOverride | None = None
    announcements: AnnouncementsOverride | None = None

    @field_validator("days", mode="before")
    @classmethod
    def _clean_days(cls, values: object) -> object:
        """Lower-case, de-duplicate and hold in week order, whatever the file says."""
        if not isinstance(values, list):
            return values
        wanted = {value.strip().lower() for value in values if isinstance(value, str)}
        ordered = [day for day in WEEKDAYS if day in wanted]
        # An unrecognised day is handed back untouched, so the item type names it.
        return ordered if len(ordered) == len(wanted) else values

    def override_payload(self) -> dict[str, Any]:
        """This profile's overrides as plain data, with the unset keys removed.

        A ``null`` means "not set here", so it is dropped rather than merged;
        lists are values in their own right and are left alone.
        """
        payload = self.model_dump(mode="json", include={"stations", "display", "announcements"})
        return _without_nulls(payload)


class ProfilesConfig(BaseModel):
    """Time-of-day profiles, in the order they are tried.

    The first entry matching the moment wins. Overlaps and gaps are legal: the
    alternative is a validation error standing between the user and a board,
    and what falls through the list is the base config, which is the "every
    other hour" template without anyone having to write one.
    """

    enabled: bool = True
    entries: list[ProfileConfig] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def _unique_names(self) -> ProfilesConfig:
        names = [entry.name.strip().lower() for entry in self.entries]
        duplicates = {name for name in names if names.count(name) > 1}
        if duplicates:
            raise ValueError(f"duplicate profile names: {sorted(duplicates)}")
        return self


def _without_nulls(value: Any) -> Any:
    """Drop ``None`` values from nested dicts. Lists are left as they are."""
    if not isinstance(value, dict):
        return value
    return {key: _without_nulls(item) for key, item in value.items() if item is not None}


def merge_overrides(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge ``override`` onto ``base``. A list replaces, a dict merges."""
    result = dict(base)
    for key, value in override.items():
        current = result.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            result[key] = merge_overrides(current, value)
        else:
            result[key] = value
    return result


def merge_profile(config: Config, profile: ProfileConfig) -> Config:
    """The config as it stands with ``profile`` applied."""
    merged = merge_overrides(config.model_dump(mode="json"), profile.override_payload())
    # A resolved config carries no profiles of its own: nothing downstream can
    # resolve twice, and the validator below cannot recurse.
    merged["profiles"] = {"enabled": False, "entries": []}
    return Config.model_validate(merged)


class Config(BaseModel):
    stations: list[StationConfig] = Field(min_length=1, max_length=2)
    display: DisplayConfig = DisplayConfig()
    sources: SourcesConfig = SourcesConfig()
    announcements: AnnouncementsConfig = AnnouncementsConfig()
    schedule: ScheduleConfig = ScheduleConfig()
    profiles: ProfilesConfig = ProfilesConfig()

    @model_validator(mode="before")
    @classmethod
    def _accept_legacy_api_key(cls, data: object) -> object:
        """Map the v1 ``api:`` block onto ``sources:``. Drop after one release."""
        if not isinstance(data, dict) or "api" not in data:
            return data
        data = dict(data)
        legacy = data.pop("api") or {}
        if "sources" in data:
            log.warning("Both 'api:' and 'sources:' are set; ignoring the old 'api:' block")
            return data
        log.warning("Config key 'api:' is deprecated; rename it to 'sources:' (see CLAUDE.md)")
        rdm = {k: legacy[k] for k in ("base_url", "timeout") if k in legacy}
        shared = {k: legacy[k] for k in ("poll_interval", "stale_after") if k in legacy}
        data["sources"] = {**shared, "rdm": rdm}
        return data

    @model_validator(mode="after")
    def _profiles_resolve(self) -> Config:
        """Every profile must merge into a valid config, proved here and now.

        A profile is applied hours after it is saved. Finding out at 07:00 that
        it names a theme that does not exist is not an option, so each one is
        merged and validated at the Save button instead.
        """
        for profile in self.profiles.entries:
            try:
                merge_profile(self, profile)
            except ValidationError as exc:
                raise ValueError(f"profile {profile.name!r} is not valid: {exc}") from exc
        return self

    @property
    def announce_any(self) -> bool:
        """True if any station wants announcements and they are enabled."""
        return self.announcements.enabled and any(s.announce for s in self.stations)


def default_config() -> Config:
    return Config(stations=[StationConfig(crs="PAD")])


def config_path() -> Path:
    """The path we load from and save to."""
    override = os.environ.get("DESCRIBER_CONFIG")
    if override:
        return Path(override)
    for path in CONFIG_PATHS:
        if path.exists():
            return path
    return CONFIG_PATHS[0]


def load_config(path: Path | None = None) -> Config:
    """Read and validate the config file, falling back to defaults."""
    path = path or config_path()
    if not path.exists():
        log.warning("No config at %s; using built-in defaults", path)
        return default_config()
    with path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    return Config.model_validate(raw)


def save_config(config: Config, path: Path | None = None) -> Path:
    """Write the config back as YAML, atomically."""
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = config.model_dump(mode="json")
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(payload, fh, sort_keys=False, allow_unicode=True)
    tmp.replace(path)
    log.info("Wrote config to %s", path)
    return path


class ConfigStore:
    """Holds the live config and notifies listeners when it is replaced."""

    def __init__(self, config: Config, path: Path | None = None) -> None:
        self._config = config
        self._path = path or config_path()
        self._lock = threading.Lock()
        self._listeners: list[object] = []
        #: Bumped whenever the config is replaced, so the resolution below is
        #: never served from a cache built against an older file.
        self._version = 0
        self._forced_profile: str | None = None
        self._resolved: tuple[tuple[int, str], Config] | None = None

    @property
    def path(self) -> Path:
        return self._path

    def get(self) -> Config:
        """The config as written in the file. What /admin edits and saves."""
        with self._lock:
            return self._config

    def set(self, config: Config, *, persist: bool = True) -> Config:
        with self._lock:
            self._config = config
            self._version += 1
            self._resolved = None
        if persist:
            save_config(config, self._path)
        return config

    def reload(self) -> Config:
        return self.set(load_config(self._path), persist=False)

    # -- profiles ----------------------------------------------------------

    @property
    def forced_profile(self) -> str | None:
        """The profile pinned for testing, or None while the clock decides."""
        return self._forced_profile

    def force_profile(self, name: str | None) -> None:
        """Pin a profile so it can be seen out of hours. Never written to YAML."""
        with self._lock:
            self._forced_profile = name
            self._version += 1
            self._resolved = None

    def active_profile(self, now: datetime | None = None) -> ProfileConfig | None:
        """The profile in force, or None when the base config stands alone."""
        # Local: profiles.py reads this module, so it cannot be imported at the top.
        from .profiles import active_profile

        return active_profile(self.get(), now, forced=self._forced_profile)

    def active(self, now: datetime | None = None) -> Config:
        """The config the board is actually running: the file, plus any profile.

        Cached on the profile in force, because the poller asks for this on
        every tick and the answer changes four times a day.
        """
        profile = self.active_profile(now)
        if profile is None:
            return self.get()
        key = (self._version, profile.name)
        with self._lock:
            if self._resolved is not None and self._resolved[0] == key:
                return self._resolved[1]
        resolved = merge_profile(self.get(), profile)
        with self._lock:
            self._resolved = (key, resolved)
        return resolved
