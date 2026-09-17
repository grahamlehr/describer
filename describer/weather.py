"""24-hour forecasts from Open-Meteo, for the modern and thameslink boards.

Same split as the rail clients: a pure parser that works on recorded JSON
(:func:`parse_forecast`), and a thin async HTTP wrapper around it
(:class:`WeatherClient`). Open-Meteo needs no key and no account, so unlike
the rail sources there is no credential plumbing here at all — see
CLAUDE.md's Addendum 15.

:class:`WeatherService` owns the cache and the refresh loop, the same shape
as :class:`~describer.rail.poller.Poller` but far simpler: one board slot can
share a forecast with another at the same or a neighbouring station, nothing
here is ever announced, and a stale forecast is dropped rather than shown
with a warning, because weather that old is merely wrong, not a fact worth
qualifying.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from datetime import datetime, timedelta

import httpx
from pydantic import BaseModel, Field

from . import stationlist
from .config import Config, StationConfig, WeatherConfig
from .schedule import is_display_on

log = logging.getLogger(__name__)

#: Open-Meteo fields for the hourly and current blocks. `is_day` lets the
#: icon pick a moon over a sun without us doing our own sunrise arithmetic.
HOURLY_FIELDS = "temperature_2m,precipitation_probability,weather_code,is_day"
CURRENT_FIELDS = "temperature_2m,weather_code,is_day"
FORECAST_HOURS = 24

#: Backoff multipliers on refresh_interval after consecutive failures, as
#: the poller's own BACKOFF_STEPS.
BACKOFF_STEPS: tuple[float, ...] = (1, 2, 4, 8, 16, 30)

#: Coordinates rounded to this many places when keying the cache, so two
#: boards at the same or a neighbouring station share one request.
CACHE_PRECISION = 2

#: How often the service's own loop wakes to check what is wanted, regardless
#: of any one location's own due time. Cheap: it is a dict scan, not a fetch.
TICK_SECONDS = 30.0

LocationKey = tuple[float, float]


class WeatherError(RuntimeError):
    """A forecast could not be fetched or made sense of."""


class WeatherHour(BaseModel):
    #: "HH:MM", Europe/London (the query's own timezone parameter).
    time: str
    temperature: float
    #: 0-100, None when Open-Meteo has no figure for this hour.
    precip_chance: int | None = None
    condition: str
    is_day: bool = True


class Forecast(BaseModel):
    latitude: float
    longitude: float
    fetched_at: datetime
    current: WeatherHour | None = None
    hours: list[WeatherHour] = Field(default_factory=list)

    def to_dict(self) -> dict:
        return self.model_dump(mode="json")


#: WMO weather codes Open-Meteo documents, mapped to our small closed
#: vocabulary. A code not listed here (there should be none) reads "unknown"
#: rather than raising: a forecast icon is decoration, not a fact worth
#: crashing a board over.
_CONDITIONS: dict[int, str] = {
    0: "clear",
    1: "clear",  # "mainly clear"
    2: "partly-cloudy",
    3: "cloudy",  # "overcast"
    45: "fog",
    48: "fog",  # depositing rime fog
    51: "drizzle",
    53: "drizzle",
    55: "drizzle",
    56: "sleet",  # freezing drizzle, light
    57: "sleet",  # freezing drizzle, dense
    61: "rain",
    63: "rain",
    65: "heavy-rain",
    66: "sleet",  # freezing rain, light
    67: "sleet",  # freezing rain, heavy
    71: "snow",
    73: "snow",
    75: "snow",  # heavy snowfall; no separate "heavy-snow" role
    77: "snow",  # snow grains
    80: "showers",
    81: "showers",
    82: "heavy-rain",  # violent rain showers
    85: "snow",  # snow showers, slight
    86: "snow",  # snow showers, heavy
    95: "thunder",
    96: "thunder",  # thunderstorm with slight hail
    99: "thunder",  # thunderstorm with heavy hail
}


def condition_for(code: int) -> str:
    """One of the closed vocabulary of conditions a theme can draw an icon for."""
    return _CONDITIONS.get(code, "unknown")


def _hour(
    iso_time: str, temperature: object, precip: object, code: object, is_day: object
) -> WeatherHour:
    return WeatherHour(
        time=str(iso_time)[11:16],
        temperature=float(temperature) if temperature is not None else 0.0,
        precip_chance=int(precip) if precip is not None else None,
        condition=condition_for(int(code)) if code is not None else "unknown",
        is_day=bool(is_day) if is_day is not None else True,
    )


def parse_forecast(raw: dict) -> Forecast:
    """Open-Meteo's response into our own shape. Pure: works on recorded JSON.

    ``forecast_hours=24`` is asked for in the query, but the parser trims to
    24 (and drops anything before the current hour, when Open-Meteo's own
    ``current`` block says what that is) rather than trusting the upstream to
    have honoured it — the same defensiveness the rail parsers show towards
    their own feeds.
    """
    try:
        latitude = float(raw["latitude"])
        longitude = float(raw["longitude"])
    except (KeyError, TypeError, ValueError) as exc:
        raise WeatherError("Forecast response missing latitude/longitude") from exc

    hourly = raw.get("hourly") or {}
    times = hourly.get("time") or []
    temps = hourly.get("temperature_2m") or []
    precip = hourly.get("precipitation_probability") or []
    codes = hourly.get("weather_code") or []
    is_day = hourly.get("is_day") or []

    current_raw = raw.get("current") or None
    current_time = current_raw.get("time") if current_raw else None

    hours: list[WeatherHour] = []
    for i, iso in enumerate(times):
        if current_time and str(iso) < str(current_time):
            continue  # a stray hour from earlier today the query should not have sent
        hours.append(
            _hour(
                iso,
                temps[i] if i < len(temps) else None,
                precip[i] if i < len(precip) else None,
                codes[i] if i < len(codes) else None,
                is_day[i] if i < len(is_day) else None,
            )
        )
        if len(hours) >= FORECAST_HOURS:
            break

    current = None
    if current_raw and current_raw.get("time"):
        current = _hour(
            current_raw["time"],
            current_raw.get("temperature_2m"),
            None,
            current_raw.get("weather_code"),
            current_raw.get("is_day"),
        )

    return Forecast(
        latitude=latitude,
        longitude=longitude,
        fetched_at=datetime.now().astimezone(),
        current=current,
        hours=hours,
    )


class WeatherClient:
    """Async Open-Meteo client. One instance is shared by :class:`WeatherService`."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url
        self._client = httpx.AsyncClient(
            timeout=timeout,
            transport=transport,
            headers={"Accept": "application/json", "User-Agent": "describer/1.0"},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def fetch(self, latitude: float, longitude: float) -> Forecast:
        """Fetch one location's forecast. Raises :class:`WeatherError` on failure."""
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "hourly": HOURLY_FIELDS,
            "current": CURRENT_FIELDS,
            "forecast_hours": FORECAST_HOURS,
            "timezone": "Europe/London",
        }
        try:
            response = await self._client.get(self._base_url, params=params)
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPStatusError as exc:
            raise WeatherError(
                f"HTTP {exc.response.status_code} for {latitude},{longitude}"
            ) from exc
        except httpx.HTTPError as exc:
            raise WeatherError(f"{type(exc).__name__} for {latitude},{longitude}") from exc
        except ValueError as exc:
            raise WeatherError(f"Malformed JSON for {latitude},{longitude}") from exc

        if not isinstance(payload, dict):
            raise WeatherError(f"Unexpected payload type for {latitude},{longitude}")
        return parse_forecast(payload)


def location_key(latitude: float, longitude: float) -> LocationKey:
    """Coordinates rounded so nearby stations share one cache entry and request."""
    return (round(latitude, CACHE_PRECISION), round(longitude, CACHE_PRECISION))


class WeatherService:
    """Owns the forecast cache and the refresh loop.

    Runs its own tick, independent of the rail poller, so a slow or wedged
    board fetch never holds up the weather (or vice versa). ``config_provider``
    is called on every tick to learn the *resolved* config (profile applied),
    exactly what ``display.show_weather`` and the station list actually are
    right now — the same reason the poller takes one snapshot per tick rather
    than reading the store twice.
    """

    def __init__(
        self,
        config: WeatherConfig,
        config_provider: Callable[[], Config],
        *,
        client: WeatherClient | None = None,
    ) -> None:
        self._config = config
        self._config_provider = config_provider
        self._client = client or WeatherClient(config.base_url, timeout=config.timeout)
        self._forecasts: dict[LocationKey, Forecast] = {}
        self._failures: dict[LocationKey, int] = {}
        self._next_due: dict[LocationKey, datetime] = {}
        self._wanted: set[LocationKey] = set()
        #: CRS codes already logged as coordinate-less, so a station missing
        #: from NaPTAN is not renotified on every tick.
        self._logged_missing: set[str] = set()
        self._task: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        #: Called after a forecast changes, so the poller can publish state
        #: without waiting for its own next board poll.
        self.on_change: Callable[[], None] | None = None
        self.last_error: str | None = None
        self.last_fetch: datetime | None = None

    # -- lifecycle -----------------------------------------------------------

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="describer-weather")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        await self._client.aclose()

    def update_config(self, config: WeatherConfig) -> None:
        """Adopt a new config from /admin; the loop reads it on each pass."""
        self._config = config

    def config_changed(self) -> None:
        """Called by the admin page: re-check straight away, a new station included."""
        self._wake.set()

    # -- coordinates -----------------------------------------------------------

    def _coordinates(self, station: StationConfig) -> tuple[float, float] | None:
        """A station's forecast location: its own override, else NaPTAN's."""
        if station.latitude is not None and station.longitude is not None:
            return (station.latitude, station.longitude)
        coords = stationlist.coordinates_for(station.crs)
        if coords is None and station.crs not in self._logged_missing:
            self._logged_missing.add(station.crs)
            log.info("No coordinates for %s; no forecast for that board", station.crs)
        return coords

    # -- reading, for the board -----------------------------------------------

    def forecast_for(self, station: StationConfig) -> Forecast | None:
        """The station's current forecast, or None if there isn't a fresh one."""
        coords = self._coordinates(station)
        if coords is None:
            return None
        forecast = self._forecasts.get(location_key(*coords))
        if forecast is None:
            return None
        age = (datetime.now().astimezone() - forecast.fetched_at).total_seconds()
        if age > self._config.stale_after:
            return None
        return forecast

    def forecasts_for_state(self, config: Config) -> list[dict | None]:
        """One entry per configured station, parallel to ``Poller.boards()``."""
        if not config.display.show_weather:
            return [None] * len(config.stations)
        return [
            forecast.to_dict() if (forecast := self.forecast_for(station)) else None
            for station in config.stations
        ]

    def status(self) -> dict:
        config = self._config_provider()
        return {
            "enabled": config.display.show_weather,
            "locations": len(self._wanted),
            "last_fetch": self.last_fetch.isoformat() if self.last_fetch else None,
            "last_error": self.last_error,
        }

    # -- the loop --------------------------------------------------------------

    def _wanted_locations(self, config: Config) -> set[LocationKey]:
        if not config.display.show_weather or not is_display_on(config.schedule):
            return set()
        wanted: set[LocationKey] = set()
        for station in config.stations:
            coords = self._coordinates(station)
            if coords is not None:
                wanted.add(location_key(*coords))
        return wanted

    async def _fetch(self, key: LocationKey) -> None:
        latitude, longitude = key
        try:
            forecast = await self._client.fetch(latitude, longitude)
        except WeatherError as exc:
            failures = self._failures.get(key, 0) + 1
            self._failures[key] = failures
            self.last_error = str(exc)
            if failures == 1:
                log.warning("Weather fetch failed for %s: %s", key, exc)
            step = BACKOFF_STEPS[min(failures - 1, len(BACKOFF_STEPS) - 1)]
            self._next_due[key] = datetime.now() + timedelta(
                seconds=self._config.refresh_interval * step
            )
            return

        if self._failures.get(key):
            log.info("Weather fetch recovered for %s", key)
        self._failures[key] = 0
        self._forecasts[key] = forecast
        self.last_fetch = forecast.fetched_at
        self.last_error = None
        self._next_due[key] = datetime.now() + timedelta(seconds=self._config.refresh_interval)
        if self.on_change:
            self.on_change()

    async def _run(self) -> None:
        log.info("Weather service started")
        while True:
            try:
                await self._poll_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Unexpected weather error")
                await asyncio.sleep(5)
                continue
            await self._sleep_until(datetime.now() + timedelta(seconds=TICK_SECONDS))

    async def _poll_once(self) -> None:
        """One pass: adopt the wanted locations, fetch whatever is due.

        Split from :meth:`_run` so a test can drive exactly one pass without
        also waiting out the real sleep between them.
        """
        config = self._config_provider()
        wanted = self._wanted_locations(config)
        # A location that stops being wanted (the option turned off, or the
        # station changed) is dropped so it does not keep polling for nothing,
        # and so a stale figure for a place no longer on the board can never
        # be handed out again by mistake if it comes back later unchanged.
        for key in set(self._forecasts) - wanted:
            del self._forecasts[key]
        for key in set(self._next_due) - wanted:
            del self._next_due[key]
        for key in set(self._failures) - wanted:
            del self._failures[key]
        self._wanted = wanted

        now = datetime.now()
        for key in wanted:
            if self._next_due.get(key, now) <= now:
                await self._fetch(key)

    async def _sleep_until(self, target: datetime) -> None:
        delay = max((target - datetime.now()).total_seconds(), 0.5)
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=delay)
        except TimeoutError:
            return
        self._wake.clear()
