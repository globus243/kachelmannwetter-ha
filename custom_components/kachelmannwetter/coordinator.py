"""Update coordinators and runtime data of the integration."""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import KmwApiClient, KmwError
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

type KmwConfigEntry = ConfigEntry[KmwData]


class KmwCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """One coordinator per endpoint, each with its own interval."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        name: str,
        interval: timedelta,
        fetch: Callable[[], Awaitable[dict[str, Any]]],
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {name}",
            update_interval=interval,
        )
        self._fetch = fetch

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            return await self._fetch()
        except KmwError as err:
            raise UpdateFailed(str(err)) from err


@dataclass
class KmwData:
    """All coordinators plus metadata belonging to one config entry."""

    api: KmwApiClient
    latitude: float
    longitude: float
    current: KmwCoordinator
    fc1h: KmwCoordinator
    fc3h: KmwCoordinator
    fc6h: KmwCoordinator
    day3: KmwCoordinator
    trend: KmwCoordinator
    astro: KmwCoordinator
    station: KmwCoordinator | None = None
    station_id: str | None = None
    station_name: str | None = None

    def all_coordinators(self) -> list[KmwCoordinator]:
        """Every active coordinator (station only when one is configured)."""
        return [
            coord
            for coord in (
                self.current,
                self.fc1h,
                self.fc3h,
                self.fc6h,
                self.day3,
                self.trend,
                self.astro,
                self.station,
            )
            if coord is not None
        ]
