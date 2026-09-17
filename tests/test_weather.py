"""Open-Meteo forecasts: the pure parser, the WMO map, and the cache/refresh
loop in WeatherService. No network — Addendum 4's rule holds for every
upstream, not only RTT, so the HTTP layer is always mocked."""

from datetime import datetime, timedelta

import httpx
import pytest

from describer.config import Config, StationConfig, WeatherConfig
from describer.weather import (
    FORECAST_HOURS,
    WeatherClient,
    WeatherError,
    WeatherService,
    condition_for,
    location_key,
    parse_forecast,
)
from tests.conftest import load_fixture

FORECAST = "openmeteo_forecast.json"


def test_parses_hours_and_the_current_block():
    forecast = parse_forecast(load_fixture(FORECAST))

    assert forecast.latitude == 51.5
    assert forecast.longitude == -0.18
    assert len(forecast.hours) == FORECAST_HOURS
    assert forecast.hours[0].time == "14:00"
    assert forecast.hours[0].temperature == 14.0
    assert forecast.hours[0].precip_chance == 0
    assert forecast.hours[3].condition == "rain"
    assert forecast.hours[7].is_day is False
    assert forecast.current is not None
    assert forecast.current.time == "14:00"
    assert forecast.current.condition == "cloudy"


def test_missing_precipitation_probability_is_none_not_zero():
    raw = load_fixture(FORECAST)
    del raw["hourly"]["precipitation_probability"]

    forecast = parse_forecast(raw)

    assert all(hour.precip_chance is None for hour in forecast.hours)


def test_extra_hours_before_the_current_one_are_dropped():
    """Defensive trimming: forecast_hours=24 is a request, not a guarantee."""
    raw = load_fixture(FORECAST)
    hourly = raw["hourly"]
    # Prepend three hours that are already in the past relative to "current".
    past_times = ["2026-09-17T11:00", "2026-09-17T12:00", "2026-09-17T13:00"]
    hourly["time"] = past_times + hourly["time"]
    hourly["temperature_2m"] = [9.0, 9.5, 10.0] + hourly["temperature_2m"]
    hourly["precipitation_probability"] = [0, 0, 0] + hourly["precipitation_probability"]
    hourly["weather_code"] = [0, 0, 0] + hourly["weather_code"]
    hourly["is_day"] = [1, 1, 1] + hourly["is_day"]

    forecast = parse_forecast(raw)

    assert len(forecast.hours) == FORECAST_HOURS
    assert forecast.hours[0].time == "14:00"  # not one of the three past hours


def test_a_missing_lat_lon_is_a_weather_error():
    raw = load_fixture(FORECAST)
    del raw["latitude"]

    with pytest.raises(WeatherError):
        parse_forecast(raw)


#: Every WMO code Open-Meteo documents for weather_code.
DOCUMENTED_WMO_CODES = [
    0,
    1,
    2,
    3,
    45,
    48,
    51,
    53,
    55,
    56,
    57,
    61,
    63,
    65,
    66,
    67,
    71,
    73,
    75,
    77,
    80,
    81,
    82,
    85,
    86,
    95,
    96,
    99,
]
VOCABULARY = {
    "clear",
    "partly-cloudy",
    "cloudy",
    "fog",
    "drizzle",
    "rain",
    "heavy-rain",
    "sleet",
    "snow",
    "showers",
    "thunder",
    "unknown",
}


@pytest.mark.parametrize("code", DOCUMENTED_WMO_CODES)
def test_every_documented_wmo_code_maps_somewhere_sensible(code):
    assert condition_for(code) in VOCABULARY
    assert condition_for(code) != "unknown"


def test_an_undocumented_code_is_unknown_not_a_crash():
    assert condition_for(12345) == "unknown"


def test_location_key_rounds_so_nearby_stations_share_a_cache_entry():
    assert location_key(51.5033, -0.1276) == location_key(51.5041, -0.1281)
    assert location_key(51.50, -0.12) != location_key(51.60, -0.12)


# --------------------------------------------------------------- the client


def _transport(handler):
    return httpx.MockTransport(handler)


async def test_client_fetch_parses_the_response():
    def handler(request):
        return httpx.Response(200, json=load_fixture(FORECAST))

    client = WeatherClient("https://api.open-meteo.com/v1/forecast", transport=_transport(handler))
    forecast = await client.fetch(51.5, -0.18)

    assert forecast.latitude == 51.5
    assert len(forecast.hours) == FORECAST_HOURS
    await client.aclose()


async def test_client_raises_on_http_error():
    def handler(request):
        return httpx.Response(503, text="down for maintenance")

    client = WeatherClient("https://api.open-meteo.com/v1/forecast", transport=_transport(handler))
    with pytest.raises(WeatherError):
        await client.fetch(51.5, -0.18)
    await client.aclose()


async def test_client_raises_on_malformed_json():
    def handler(request):
        return httpx.Response(200, text="not json")

    client = WeatherClient("https://api.open-meteo.com/v1/forecast", transport=_transport(handler))
    with pytest.raises(WeatherError):
        await client.fetch(51.5, -0.18)
    await client.aclose()


# --------------------------------------------------------------- the service


def _config(*, show_weather=True, crs="PAD", latitude=51.5, longitude=-0.18):
    return Config(
        stations=[StationConfig(crs=crs, latitude=latitude, longitude=longitude)],
        display={"show_weather": show_weather},
    )


class _FakeClient:
    """Records calls; hands back the fixture, or raises on demand."""

    def __init__(self):
        self.calls: list[tuple[float, float]] = []
        self.fail = False

    async def fetch(self, latitude, longitude):
        self.calls.append((latitude, longitude))
        if self.fail:
            raise WeatherError("simulated failure")
        return parse_forecast(load_fixture(FORECAST))

    async def aclose(self):
        pass


async def test_nothing_is_fetched_while_show_weather_is_off():
    fake = _FakeClient()
    config = _config(show_weather=False)
    service = WeatherService(WeatherConfig(), lambda: config, client=fake)

    await service._poll_once()

    assert fake.calls == []
    assert service.forecast_for(config.stations[0]) is None


async def test_a_wanted_location_is_fetched_promptly():
    fake = _FakeClient()
    config = _config()
    service = WeatherService(WeatherConfig(), lambda: config, client=fake)

    await service._poll_once()

    assert fake.calls == [(51.5, -0.18)]
    forecast = service.forecast_for(config.stations[0])
    assert forecast is not None
    assert forecast.latitude == 51.5


async def test_two_stations_near_each_other_share_one_fetch():
    fake = _FakeClient()
    config = Config(
        stations=[
            StationConfig(crs="PAD", mode="departures", latitude=51.503, longitude=-0.181),
            StationConfig(crs="PAD", mode="arrivals", latitude=51.504, longitude=-0.182),
        ],
        display={"show_weather": True},
    )
    service = WeatherService(WeatherConfig(), lambda: config, client=fake)

    await service._poll_once()

    assert len(fake.calls) == 1  # rounded to the same 2 dp cache key
    assert all(service.forecast_for(s) is not None for s in config.stations)


async def test_a_station_with_no_coordinates_gets_no_forecast_and_no_call(caplog):
    fake = _FakeClient()
    config = Config(stations=[StationConfig(crs="ZZZ")], display={"show_weather": True})
    service = WeatherService(WeatherConfig(), lambda: config, client=fake)

    with caplog.at_level("INFO"):
        await service._poll_once()

    assert fake.calls == []
    assert service.forecast_for(config.stations[0]) is None
    assert "ZZZ" in caplog.text


async def test_a_failure_keeps_the_last_good_forecast_and_backs_off():
    fake = _FakeClient()
    config = _config()
    service = WeatherService(WeatherConfig(), lambda: config, client=fake)

    await service._poll_once()  # first fetch succeeds
    good = service.forecast_for(config.stations[0])
    assert good is not None

    fake.fail = True
    service._next_due.clear()  # force it due again without waiting refresh_interval
    await service._poll_once()

    assert service.forecast_for(config.stations[0]) is good  # unchanged
    assert service.last_error is not None
    key = location_key(51.5, -0.18)
    assert service._next_due[key] > datetime.now() + timedelta(seconds=1)


async def test_a_stale_forecast_is_dropped_not_shown():
    fake = _FakeClient()
    config = _config()
    weather_config = WeatherConfig(stale_after=1800)
    service = WeatherService(weather_config, lambda: config, client=fake)

    await service._poll_once()
    key = location_key(51.5, -0.18)
    # Backdate the fetch beyond stale_after without waiting for it.
    stale = service._forecasts[key].model_copy(
        update={"fetched_at": datetime.now().astimezone() - timedelta(seconds=2000)}
    )
    service._forecasts[key] = stale

    assert service.forecast_for(config.stations[0]) is None


async def test_forecasts_for_state_is_parallel_to_stations():
    fake = _FakeClient()
    config = _config()
    service = WeatherService(WeatherConfig(), lambda: config, client=fake)
    await service._poll_once()

    result = service.forecasts_for_state(config)

    assert len(result) == 1
    assert result[0]["latitude"] == 51.5


async def test_forecasts_for_state_is_all_none_when_show_weather_is_off():
    config = _config(show_weather=False)
    service = WeatherService(WeatherConfig(), lambda: config, client=_FakeClient())

    assert service.forecasts_for_state(config) == [None]


async def test_the_schedule_being_off_stops_fetching_too():
    fake = _FakeClient()
    config = Config(
        stations=[StationConfig(crs="PAD", latitude=51.5, longitude=-0.18)],
        display={"show_weather": True},
        schedule={"enabled": True, "on_time": "06:00", "off_time": "06:00"},
    )
    # start == end is "all day" for schedule.py, so use a per_weekday off-all-day
    # override for today instead, guaranteeing the display reads off regardless
    # of when the suite runs.
    import describer.schedule as schedule_module
    from describer.config import WEEKDAYS

    today = WEEKDAYS[datetime.now().weekday()]
    config = config.model_copy(
        update={"schedule": config.schedule.model_copy(update={"per_weekday": {today: None}})}
    )
    assert schedule_module.is_display_on(config.schedule) is False

    service = WeatherService(WeatherConfig(), lambda: config, client=fake)
    await service._poll_once()

    assert fake.calls == []
