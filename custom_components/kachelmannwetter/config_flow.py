"""Config flow for Kachelmannwetter."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_API_KEY, CONF_LATITUDE, CONF_LONGITUDE
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import KmwApiClient, KmwAuthError, KmwError, KmwForbiddenError
from .const import CONF_STATION_ID, CONF_STATION_NAME, DOMAIN, STATION_NONE


class KmwConfigFlow(ConfigFlow, domain=DOMAIN):
    """UI setup: validate key and coordinates, then pick a station."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._stations: list[dict[str, Any]] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            lat = user_input[CONF_LATITUDE]
            lon = user_input[CONF_LONGITUDE]
            api = KmwApiClient(
                async_get_clientsession(self.hass), user_input[CONF_API_KEY]
            )
            try:
                await api.current(lat, lon)
            except KmwAuthError:
                errors["base"] = "invalid_auth"
            except KmwForbiddenError:
                errors["base"] = "location_forbidden"
            except KmwError:
                errors["base"] = "cannot_connect"

            if not errors:
                await self.async_set_unique_id(f"{lat:.4f}_{lon:.4f}")
                self._abort_if_unique_id_configured()
                self._data = dict(user_input)
                try:
                    self._stations = await api.station_search(lat, lon, radius=25)
                except KmwError:
                    self._stations = []
                return await self.async_step_station()

        schema = vol.Schema(
            {
                vol.Required(CONF_API_KEY): str,
                vol.Required(
                    CONF_LATITUDE, default=self.hass.config.latitude
                ): vol.Coerce(float),
                vol.Required(
                    CONF_LONGITUDE, default=self.hass.config.longitude
                ): vol.Coerce(float),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_station(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            station_id = user_input[CONF_STATION_ID]
            if station_id != STATION_NONE:
                self._data[CONF_STATION_ID] = station_id
                self._data[CONF_STATION_NAME] = next(
                    (s["name"] for s in self._stations if s["id"] == station_id),
                    station_id,
                )
            return self.async_create_entry(title="Kachelmannwetter", data=self._data)

        options: dict[str, str] = {}
        for s in self._stations:
            options[s["id"]] = (
                f"{s['name']} ({s.get('distance', '?')} km, "
                f"{s.get('precision', 'unknown')})"
            )
        options[STATION_NONE] = "No weather station"
        default = self._stations[0]["id"] if self._stations else STATION_NONE
        schema = vol.Schema(
            {vol.Required(CONF_STATION_ID, default=default): vol.In(options)}
        )
        return self.async_show_form(step_id="station", data_schema=schema)
