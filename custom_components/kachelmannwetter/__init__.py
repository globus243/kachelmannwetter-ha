"""Kachelmannwetter integration for Home Assistant."""
from __future__ import annotations

import asyncio

from datetime import datetime

from homeassistant.const import CONF_API_KEY, CONF_LATITUDE, CONF_LONGITUDE, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_change

from .api import KmwApiClient
from .const import (
    CONF_STATION_ID,
    CONF_STATION_NAME,
    DEFAULT_MODEL_3DAY,
    FULL_REFRESH_HOURS,
    FULL_REFRESH_MINUTE,
    STATION_NONE,
    UPDATE_3DAY,
    UPDATE_ASTRO,
    UPDATE_CURRENT,
    UPDATE_FC1H,
    UPDATE_FC3H,
    UPDATE_FC6H,
    UPDATE_STATION,
    UPDATE_TREND,
)
from .coordinator import KmwConfigEntry, KmwCoordinator, KmwData

PLATFORMS = [Platform.BINARY_SENSOR, Platform.SENSOR, Platform.WEATHER]


async def async_setup_entry(hass: HomeAssistant, entry: KmwConfigEntry) -> bool:
    """Set the integration up from a config entry."""
    api = KmwApiClient(async_get_clientsession(hass), entry.data[CONF_API_KEY])
    lat: float = entry.data[CONF_LATITUDE]
    lon: float = entry.data[CONF_LONGITUDE]
    station_id: str | None = entry.data.get(CONF_STATION_ID)
    if station_id == STATION_NONE:
        station_id = None

    data = KmwData(
        api=api,
        latitude=lat,
        longitude=lon,
        current=KmwCoordinator(
            hass, entry, "current", UPDATE_CURRENT, lambda: api.current(lat, lon)
        ),
        fc1h=KmwCoordinator(
            hass, entry, "forecast_1h", UPDATE_FC1H,
            lambda: api.forecast(lat, lon, "advanced", "1h"),
        ),
        fc3h=KmwCoordinator(
            hass, entry, "forecast_3h", UPDATE_FC3H,
            lambda: api.forecast(lat, lon, "advanced", "3h"),
        ),
        fc6h=KmwCoordinator(
            hass, entry, "forecast_6h", UPDATE_FC6H,
            lambda: api.forecast(lat, lon, "advanced", "6h"),
        ),
        day3=KmwCoordinator(
            hass, entry, "forecast_3day", UPDATE_3DAY,
            lambda: api.forecast_3day(lat, lon, DEFAULT_MODEL_3DAY),
        ),
        trend=KmwCoordinator(
            hass, entry, "trend14days", UPDATE_TREND,
            lambda: api.trend14days(lat, lon),
        ),
        astro=KmwCoordinator(
            hass, entry, "astronomy", UPDATE_ASTRO,
            lambda: api.astronomy(lat, lon),
        ),
        station=(
            KmwCoordinator(
                hass, entry, "station", UPDATE_STATION,
                lambda: api.station_latest(station_id),
            )
            if station_id
            else None
        ),
        station_id=station_id,
        station_name=entry.data.get(CONF_STATION_NAME),
    )

    # current has to work, otherwise ConfigEntryNotReady. The rest is
    # tolerant and picks its data up at the next interval at the latest.
    await data.current.async_config_entry_first_refresh()
    optional = [c for c in data.all_coordinators() if c is not data.current]
    await asyncio.gather(*(c.async_refresh() for c in optional))

    entry.runtime_data = data

    @callback
    def _full_refresh(now: datetime) -> None:
        """Reload every endpoint together (midnight and noon)."""
        for coord in data.all_coordinators():
            entry.async_create_background_task(
                hass, coord.async_refresh(), f"{coord.name} full refresh"
            )

    entry.async_on_unload(
        async_track_time_change(
            hass,
            _full_refresh,
            hour=FULL_REFRESH_HOURS,
            minute=FULL_REFRESH_MINUTE,
            second=0,
        )
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: KmwConfigEntry) -> bool:
    """Unload the integration."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
