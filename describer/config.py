"""Configuration model: load, validate and save `config.yaml`.

The YAML file is the single source of truth. The admin page reads and writes
the same file through :func:`load_config` / :func:`save_config`.
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

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

    @field_validator("crs")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper()


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


class ThemesConfig(BaseModel):
    crt: CrtThemeConfig = CrtThemeConfig()
    splitflap: SplitflapThemeConfig = SplitflapThemeConfig()


class DisplayConfig(BaseModel):
    #: Active theme; switchable live from /admin.
    theme: Literal["modern", "crt", "splitflap", "1990s"] = "modern"
    #: Show the live clock in each board header.
    clock: bool = True
    #: Expand the first row to scroll its calling points.
    show_calling_points: bool = True
    themes: ThemesConfig = ThemesConfig()


SourceName = Literal["rdm", "rtt"]


class RdmSourceConfig(BaseModel):
    """Rail Data Marketplace LDBWS. Key comes from RDM_API_KEY."""

    #: LDBWS base URL (no trailing slash).
    base_url: str = (
        "https://api1.raildata.org.uk/1010-live-departure-board-dep1_2/LDBWS/api/20220120"
    )
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


class Config(BaseModel):
    stations: list[StationConfig] = Field(min_length=1, max_length=2)
    display: DisplayConfig = DisplayConfig()
    sources: SourcesConfig = SourcesConfig()
    announcements: AnnouncementsConfig = AnnouncementsConfig()
    schedule: ScheduleConfig = ScheduleConfig()

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

    @property
    def path(self) -> Path:
        return self._path

    def get(self) -> Config:
        with self._lock:
            return self._config

    def set(self, config: Config, *, persist: bool = True) -> Config:
        with self._lock:
            self._config = config
        if persist:
            save_config(config, self._path)
        return config

    def reload(self) -> Config:
        return self.set(load_config(self._path), persist=False)
