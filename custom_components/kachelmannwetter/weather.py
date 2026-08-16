"""Weather entity built from the Kachelmannwetter data.

Hourly forecast: advanced/1h (24 h) + advanced/3h (up to 120 h)
+ advanced/6h (up to 240 h), stitched together.
Daily forecast: trend14days (14 days, multi-model).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from homeassistant.components.weather import (
    Forecast,
    WeatherEntity,
    WeatherEntityFeature,
)
from homeassistant.const import (
    UnitOfPrecipitationDepth,
    UnitOfPressure,
    UnitOfSpeed,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTRIBUTION, DOMAIN, symbol_to_condition
from .coordinator import KmwConfigEntry, KmwCoordinator

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: KmwConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([KmwWeather(entry)])


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# Extra per-row fields carried into the forecast items (api key -> attr name).
# HA's Forecast is a TypedDict, so additional keys pass through
# weather.get_forecasts untouched.
HOURLY_EXTRAS = {
    "isDay": "is_day",
    "weatherSymbol": "weather_symbol",
    "wmoCode": "wmo_code",
    "cloudCoverageLow": "cloud_coverage_low",
    "cloudCoverageMedium": "cloud_coverage_medium",
    "cloudCoverageHigh": "cloud_coverage_high",
    "globalRadiation": "global_radiation",
    "sunHours": "sun_hours",
    "prec6h": "prec_6h",
    "prec12h": "prec_12h",
    "prec24h": "prec_24h",
    "precTotal": "prec_total",
    "snowAmount": "snow_amount",
    "snowAmount6h": "snow_amount_6h",
    "snowAmount12h": "snow_amount_12h",
    "snowAmount24h": "snow_amount_24h",
    "snowHeight": "snow_height",
    "tempMin6h": "temp_min_6h",
    "tempMax6h": "temp_max_6h",
    "tempMin12h": "temp_min_12h",
    "tempMax12h": "temp_max_12h",
    "windGust3h": "wind_gust_3h",
}

DAY3_EXTRAS = {
    "weatherSymbol": "weather_symbol",
    "sunHours": "sun_hours",
    "precProb10mm": "prec_prob_10mm",
    "risks": "risks",
}

TREND_EXTRAS = {
    "weatherSymbol": "weather_symbol",
    "weekday": "weekday",
    "isWeekend": "is_weekend",
    "tempMaxLow": "temp_max_low",
    "tempMaxHigh": "temp_max_high",
    "tempMinLow": "temp_min_low",
    "tempMinHigh": "temp_min_high",
    "precLow": "prec_low",
    "precHigh": "prec_high",
    "precProb10mm": "prec_prob_10mm",
    "precType": "prec_type",
    "precIntensity": "prec_intensity",
    "precWord": "prec_word",
    "windGustLow": "wind_gust_low",
    "windGustHigh": "wind_gust_high",
    "sunHours": "sun_hours",
    "sunHoursRelative": "sun_hours_relative",
    "sunHoursLow": "sun_hours_low",
    "sunHoursHigh": "sun_hours_high",
    "sunMaxPos": "sun_max_pos",
    "cloudWord": "cloud_word",
    "thunderStorm": "thunderstorm",
}


def _with_extras(
    item: Forecast, row: dict[str, Any], extras: dict[str, str]
) -> Forecast:
    """Attach the extra row fields to a forecast item, skipping empty ones."""
    for api_key, attr in extras.items():
        value = row.get(api_key)
        if value is not None:
            item[attr] = value  # type: ignore[literal-required]
    return item


class KmwWeather(CoordinatorEntity[KmwCoordinator], WeatherEntity):
    """Current conditions from /current, forecasts from the forecast endpoints."""

    _attr_attribution = ATTRIBUTION
    _attr_has_entity_name = True
    _attr_name = None
    _attr_supported_features = (
        WeatherEntityFeature.FORECAST_DAILY | WeatherEntityFeature.FORECAST_HOURLY
    )
    _attr_native_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_native_pressure_unit = UnitOfPressure.HPA
    _attr_native_wind_speed_unit = UnitOfSpeed.METERS_PER_SECOND
    _attr_native_precipitation_unit = UnitOfPrecipitationDepth.MILLIMETERS

    def __init__(self, entry: KmwConfigEntry) -> None:
        data = entry.runtime_data
        super().__init__(data.current)
        self._data = data
        self._attr_unique_id = f"{entry.entry_id}_weather"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Kachelmannwetter",
            manufacturer="Meteologix AG",
            model="Kachelmannwetter API v2",
            entry_type=DeviceEntryType.SERVICE,
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        for coord in (
            self._data.fc1h,
            self._data.fc3h,
            self._data.fc6h,
            self._data.day3,
            self._data.trend,
        ):
            self.async_on_remove(coord.async_add_listener(self._forecast_updated))

    @callback
    def _forecast_updated(self) -> None:
        self.async_write_ha_state()
        self.hass.async_create_task(self.async_update_listeners(("daily", "hourly")))

    def _value(self, key: str) -> Any:
        data = (self.coordinator.data or {}).get("data") or {}
        entry = data.get(key)
        return entry.get("value") if isinstance(entry, dict) else None

    @property
    def condition(self) -> str | None:
        is_day = self._value("isDay")
        return symbol_to_condition(
            self._value("weatherSymbol"), True if is_day is None else bool(is_day)
        )

    @property
    def native_temperature(self) -> float | None:
        return _num(self._value("temp"))

    @property
    def native_dew_point(self) -> float | None:
        return _num(self._value("dewpoint"))

    @property
    def humidity(self) -> float | None:
        return _num(self._value("humidityRelative"))

    @property
    def native_pressure(self) -> float | None:
        return _num(self._value("pressureMsl"))

    @property
    def native_wind_speed(self) -> float | None:
        return _num(self._value("windSpeed"))

    @property
    def native_wind_gust_speed(self) -> float | None:
        return _num(self._value("windGust"))

    @property
    def wind_bearing(self) -> float | None:
        return _num(self._value("windDirection"))

    @property
    def cloud_coverage(self) -> float | None:
        return _num(self._value("cloudCoverage"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {}
        for label, coord in (
            ("1h", self._data.fc1h),
            ("3h", self._data.fc3h),
            ("6h", self._data.fc6h),
            ("trend", self._data.trend),
        ):
            if coord.data:
                attrs[f"model_run_{label}"] = coord.data.get("run")
                if resolution := coord.data.get("resolution"):
                    attrs[f"resolution_{label}"] = resolution
        return attrs

    # ---------- Forecasts ----------

    def _merged_hourly_rows(self) -> list[dict[str, Any]]:
        """Stitch the 1h, 3h and 6h series together without overlap.

        Every dateTime comes with a +00:00 offset, which makes the
        lexicographic comparison chronologically correct.
        """
        rows: list[dict[str, Any]] = []
        last = ""
        for coord in (self._data.fc1h, self._data.fc3h, self._data.fc6h):
            if not coord.data:
                continue
            for row in coord.data.get("data", []):
                if (row.get("dateTime") or "") > last:
                    rows.append(row)
            if rows:
                last = rows[-1].get("dateTime") or last
        return rows

    async def async_forecast_hourly(self) -> list[Forecast] | None:
        rows = self._merged_hourly_rows()
        if not rows:
            return None
        return [
            _with_extras(
                Forecast(
                    datetime=row["dateTime"],
                    condition=symbol_to_condition(
                        row.get("weatherSymbol"), bool(row.get("isDay", True))
                    ),
                    native_temperature=_num(row.get("temp")),
                    native_dew_point=_num(row.get("dewpoint")),
                    native_pressure=_num(row.get("pressureMsl")),
                    humidity=_num(row.get("humidityRelative")),
                    native_wind_speed=_num(row.get("windSpeed")),
                    native_wind_gust_speed=_num(row.get("windGust")),
                    wind_bearing=_num(row.get("windDirection")),
                    cloud_coverage=_num(row.get("cloudCoverage")),
                    native_precipitation=_num(row.get("precCurrent")),
                ),
                row,
                HOURLY_EXTRAS,
            )
            for row in rows
            if row.get("dateTime")
        ]

    async def async_forecast_daily(self) -> list[Forecast] | None:
        """trend14days plus any missing days from the 3-day ECMWF forecast.

        Depending on the time of day the trend starts at *tomorrow* — "today"
        then comes from the 3day forecast, so the daily forecast does not
        begin with tomorrow.
        """
        trend = self._data.trend.data or {}
        day3 = self._data.day3.data or {}
        if not trend and not day3:
            return None
        try:
            tz = ZoneInfo(
                trend.get("timeZone") or day3.get("timeZone") or "UTC"
            )
        except (KeyError, ValueError):
            tz = ZoneInfo("UTC")

        def day_iso(date_str: str) -> str:
            try:
                day = datetime.fromisoformat(date_str)
                if day.tzinfo is None:
                    day = day.replace(tzinfo=tz)
                return day.isoformat()
            except ValueError:
                return date_str

        out: list[Forecast] = []
        trend_dates = {
            str(row.get("dateTime") or "")[:10]
            for row in trend.get("data", [])
        }

        for row in day3.get("data", []):
            date_str = str(row.get("dateTime") or "")[:10]
            if not date_str or date_str in trend_dates:
                continue
            out.append(
                _with_extras(
                    Forecast(
                        datetime=day_iso(date_str),
                        condition=symbol_to_condition(row.get("weatherSymbol"), True),
                        native_temperature=_num(row.get("tempMax")),
                        native_templow=_num(row.get("tempMin")),
                        native_precipitation=_num(row.get("precCurrent")),
                        precipitation_probability=_num(row.get("precProb")),
                        native_wind_speed=_num(row.get("windSpeed")),
                        native_wind_gust_speed=_num(row.get("windGust")),
                        wind_bearing=_num(row.get("windDirection")),
                        cloud_coverage=_num(row.get("cloudCoverage")),
                    ),
                    row,
                    DAY3_EXTRAS,
                )
            )

        for row in trend.get("data", []):
            date_str = row.get("dateTime")
            if not date_str:
                continue
            eighths = _num(row.get("cloudCoverageEighths"))
            out.append(
                _with_extras(
                    Forecast(
                        datetime=day_iso(date_str),
                        condition=symbol_to_condition(row.get("weatherSymbol"), True),
                        native_temperature=_num(row.get("tempMax")),
                        native_templow=_num(row.get("tempMin")),
                        native_precipitation=_num(row.get("prec")),
                        precipitation_probability=_num(row.get("precProb1mm")),
                        native_wind_gust_speed=_num(row.get("windGust")),
                        cloud_coverage=eighths * 12.5 if eighths is not None else None,
                    ),
                    row,
                    TREND_EXTRAS,
                )
            )

        out.sort(key=lambda f: f["datetime"])
        return out
