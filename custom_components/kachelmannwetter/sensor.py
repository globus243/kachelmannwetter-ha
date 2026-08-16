"""Sensors for Kachelmannwetter: analysis, station, forecast aggregates,
3-day ECMWF, 14-day trend, astronomy and API budget.

`key` is the raw API field and feeds the unique_id; `translation_key` only
decides the display name. Renaming an entity therefore never moves its
entity_id.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    DEGREE,
    PERCENTAGE,
    EntityCategory,
    UnitOfLength,
    UnitOfPrecipitationDepth,
    UnitOfPressure,
    UnitOfSpeed,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import ATTRIBUTION, DOMAIN, KNOTS_TO_MS
from .coordinator import KmwConfigEntry, KmwCoordinator, KmwData

PARALLEL_UPDATES = 0


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True, kw_only=True)
class KmwValueDescription(SensorEntityDescription):
    """A value read straight out of an API response."""

    scale: float | None = None


@dataclass(frozen=True, kw_only=True)
class KmwAggDescription(SensorEntityDescription):
    """A value aggregated from the hourly forecast."""

    agg_fn: Callable[[list[dict[str, Any]]], float | None] = lambda rows: None


def _temp(key: str, translation_key: str) -> KmwValueDescription:
    return KmwValueDescription(
        key=key,
        translation_key=translation_key,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    )


def _wind(
    key: str, translation_key: str, scale: float | None = None
) -> KmwValueDescription:
    return KmwValueDescription(
        key=key,
        translation_key=translation_key,
        native_unit_of_measurement=UnitOfSpeed.METERS_PER_SECOND,
        device_class=SensorDeviceClass.WIND_SPEED,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        scale=scale,
    )


CURRENT_SENSORS: tuple[KmwValueDescription, ...] = (
    _temp("temp", "temperature"),
    _temp("dewpoint", "dew_point"),
    KmwValueDescription(
        key="humidityRelative",
        translation_key="humidity",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.HUMIDITY,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
    ),
    KmwValueDescription(
        key="pressureMsl",
        translation_key="pressure_msl",
        native_unit_of_measurement=UnitOfPressure.HPA,
        device_class=SensorDeviceClass.ATMOSPHERIC_PRESSURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    _wind("windSpeed", "wind_speed"),
    _wind("windGust", "wind_gust"),
    KmwValueDescription(
        key="windDirection",
        translation_key="wind_direction",
        native_unit_of_measurement=DEGREE,
        icon="mdi:compass-outline",
        suggested_display_precision=0,
    ),
    KmwValueDescription(
        key="cloudCoverage",
        translation_key="cloud_coverage",
        native_unit_of_measurement=PERCENTAGE,
        icon="mdi:weather-cloudy",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
    ),
    KmwValueDescription(
        key="prec1h",
        translation_key="precipitation_1h",
        native_unit_of_measurement=UnitOfPrecipitationDepth.MILLIMETERS,
        device_class=SensorDeviceClass.PRECIPITATION,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    KmwValueDescription(
        key="sunHours",
        translation_key="sun_hours_1h",
        native_unit_of_measurement=UnitOfTime.HOURS,
        icon="mdi:white-balance-sunny",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
    KmwValueDescription(
        key="snowAmount",
        translation_key="snow_amount",
        native_unit_of_measurement=UnitOfPrecipitationDepth.MILLIMETERS,
        icon="mdi:snowflake",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    KmwValueDescription(
        key="snowHeight",
        translation_key="snow_height",
        native_unit_of_measurement=UnitOfLength.CENTIMETERS,
        icon="mdi:snowflake-thermometer",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        scale=100.0,  # the API reports metres here
    ),
    KmwValueDescription(
        key="wmoCode",
        translation_key="wmo_code",
        icon="mdi:numeric",
        suggested_display_precision=0,
    ),
    KmwValueDescription(
        key="weatherSymbol",
        translation_key="weather_symbol",
        icon="mdi:weather-partly-cloudy",
    ),
)

# Parameters /current only delivers for some locations. Created only when the
# setup snapshot actually contains them, same idea as the station fields.
CURRENT_OPTIONAL_SENSORS: tuple[KmwValueDescription, ...] = (
    KmwValueDescription(
        key="precCurrent",
        translation_key="precipitation_current",
        native_unit_of_measurement=UnitOfPrecipitationDepth.MILLIMETERS,
        device_class=SensorDeviceClass.PRECIPITATION,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    KmwValueDescription(
        key="prec6h",
        translation_key="precipitation_6h",
        native_unit_of_measurement=UnitOfPrecipitationDepth.MILLIMETERS,
        device_class=SensorDeviceClass.PRECIPITATION,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    KmwValueDescription(
        key="prec24h",
        translation_key="precipitation_24h",
        native_unit_of_measurement=UnitOfPrecipitationDepth.MILLIMETERS,
        device_class=SensorDeviceClass.PRECIPITATION,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    _wind("windGust3h", "wind_gust_3h"),
)

# Every documented field of the station observations. Wind arrives in knots!
STATION_SENSORS: tuple[KmwValueDescription, ...] = (
    _temp("temp", "station_temperature"),
    _temp("dewpoint", "station_dew_point"),
    _temp("wetBulbTemp", "station_wet_bulb_temp"),
    KmwValueDescription(
        key="humidityRelative",
        translation_key="station_humidity",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.HUMIDITY,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
    ),
    KmwValueDescription(
        key="pressureMsl",
        translation_key="station_pressure_msl",
        native_unit_of_measurement=UnitOfPressure.HPA,
        device_class=SensorDeviceClass.ATMOSPHERIC_PRESSURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    KmwValueDescription(
        key="pressure",
        translation_key="station_pressure_qfe",
        native_unit_of_measurement=UnitOfPressure.HPA,
        device_class=SensorDeviceClass.PRESSURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    KmwValueDescription(
        key="pressureChange",
        translation_key="station_pressure_change",
        native_unit_of_measurement=UnitOfPressure.HPA,
        icon="mdi:gauge",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    _wind("windSpeed", "station_wind_speed", scale=KNOTS_TO_MS),
    KmwValueDescription(
        key="windDirection",
        translation_key="station_wind_direction",
        native_unit_of_measurement=DEGREE,
        icon="mdi:compass-outline",
        suggested_display_precision=0,
    ),
    _wind("windGust", "station_wind_gust", scale=KNOTS_TO_MS),
    _wind("windGust10m", "station_wind_gust_10m", scale=KNOTS_TO_MS),
    _wind("windGust1h", "station_wind_gust_1h", scale=KNOTS_TO_MS),
    _temp("temp5cm", "station_temp_5cm"),
    _temp("temp5cmMin", "station_temp_5cm_min"),
    _temp("tempMin", "station_temp_min_12h"),
    _temp("tempMax", "station_temp_max_12h"),
    _temp("tempMin1h", "station_temp_min_1h"),
    _temp("tempMax1h", "station_temp_max_1h"),
    KmwValueDescription(
        key="snowHeight",
        translation_key="station_snow_height",
        native_unit_of_measurement=UnitOfLength.CENTIMETERS,
        icon="mdi:snowflake-thermometer",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
    ),
    _temp("soilTemp10", "station_soil_temp_10"),
    _temp("soilTemp20", "station_soil_temp_20"),
    _temp("soilTemp80", "station_soil_temp_80"),
    _temp("soilTemp120", "station_soil_temp_120"),
    KmwValueDescription(
        key="prec",
        translation_key="station_precipitation",
        native_unit_of_measurement=UnitOfPrecipitationDepth.MILLIMETERS,
        device_class=SensorDeviceClass.PRECIPITATION,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    KmwValueDescription(
        key="prec10m",
        translation_key="station_precipitation_10m",
        native_unit_of_measurement=UnitOfPrecipitationDepth.MILLIMETERS,
        device_class=SensorDeviceClass.PRECIPITATION,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    KmwValueDescription(
        key="prec1h",
        translation_key="station_precipitation_1h",
        native_unit_of_measurement=UnitOfPrecipitationDepth.MILLIMETERS,
        device_class=SensorDeviceClass.PRECIPITATION,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    KmwValueDescription(
        key="prec6h",
        translation_key="station_precipitation_6h",
        native_unit_of_measurement=UnitOfPrecipitationDepth.MILLIMETERS,
        device_class=SensorDeviceClass.PRECIPITATION,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    KmwValueDescription(
        key="prec24h",
        translation_key="station_precipitation_24h",
        native_unit_of_measurement=UnitOfPrecipitationDepth.MILLIMETERS,
        device_class=SensorDeviceClass.PRECIPITATION,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    KmwValueDescription(
        key="globalRadiation10m",
        translation_key="station_global_radiation_10m",
        native_unit_of_measurement="kJ/m²",
        icon="mdi:sun-wireless",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
    ),
    KmwValueDescription(
        key="globalRadiation1h",
        translation_key="station_global_radiation_1h",
        native_unit_of_measurement="kJ/m²",
        icon="mdi:sun-wireless",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
    ),
    KmwValueDescription(
        key="globalRadiation24h",
        translation_key="station_global_radiation_24h",
        native_unit_of_measurement="J/cm²",
        icon="mdi:sun-wireless",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
    ),
    KmwValueDescription(
        key="leafWet",
        translation_key="station_leaf_wet",
        native_unit_of_measurement=PERCENTAGE,
        icon="mdi:leaf",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
    ),
    KmwValueDescription(
        key="soilHumidity10",
        translation_key="station_soil_humidity_10",
        native_unit_of_measurement=PERCENTAGE,
        icon="mdi:water-percent",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
    ),
    KmwValueDescription(
        key="soilHumidity20",
        translation_key="station_soil_humidity_20",
        native_unit_of_measurement=PERCENTAGE,
        icon="mdi:water-percent",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
    ),
    KmwValueDescription(
        key="soilHumidity30",
        translation_key="station_soil_humidity_30",
        native_unit_of_measurement=PERCENTAGE,
        icon="mdi:water-percent",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
    ),
    KmwValueDescription(
        key="soilHumidity60",
        translation_key="station_soil_humidity_60",
        native_unit_of_measurement=PERCENTAGE,
        icon="mdi:water-percent",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
    ),
    KmwValueDescription(
        key="soilWaterPot10",
        translation_key="station_soil_water_pot_10",
        native_unit_of_measurement=UnitOfPressure.KPA,
        icon="mdi:water-alert",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
    ),
    KmwValueDescription(
        key="soilWaterPot30",
        translation_key="station_soil_water_pot_30",
        native_unit_of_measurement=UnitOfPressure.KPA,
        icon="mdi:water-alert",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
    ),
    KmwValueDescription(
        key="soilWaterPot60",
        translation_key="station_soil_water_pot_60",
        native_unit_of_measurement=UnitOfPressure.KPA,
        icon="mdi:water-alert",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
    ),
)

# Fields that can be missing at a given observation time but should be
# created anyway. (Global radiation deliberately not: plenty of stations never
# report it — those sensors only appear if the field is present at setup.)
STATION_ALWAYS_KEYS = {
    "prec1h",
    "prec6h",
    "prec24h",
    "tempMin1h",
    "tempMax1h",
}


def _agg(field: str, fn: Callable[[list[float]], float]) -> Callable[..., float | None]:
    def inner(rows: list[dict[str, Any]]) -> float | None:
        values = [v for row in rows if (v := _num(row.get(field))) is not None]
        return round(fn(values), 2) if values else None

    return inner


AGG_SENSORS: tuple[KmwAggDescription, ...] = (
    KmwAggDescription(
        key="prec_sum_24h",
        translation_key="prec_sum_24h",
        native_unit_of_measurement=UnitOfPrecipitationDepth.MILLIMETERS,
        device_class=SensorDeviceClass.PRECIPITATION,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        agg_fn=_agg("precCurrent", sum),
    ),
    KmwAggDescription(
        key="radiation_sum_24h",
        translation_key="radiation_sum_24h",
        native_unit_of_measurement="Wh/m²",
        icon="mdi:sun-wireless",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        agg_fn=_agg("globalRadiation", sum),
    ),
    KmwAggDescription(
        key="gust_max_24h",
        translation_key="gust_max_24h",
        native_unit_of_measurement=UnitOfSpeed.METERS_PER_SECOND,
        device_class=SensorDeviceClass.WIND_SPEED,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        agg_fn=_agg("windGust", max),
    ),
    KmwAggDescription(
        key="temp_max_24h",
        translation_key="temp_max_24h",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        agg_fn=_agg("temp", max),
    ),
    KmwAggDescription(
        key="temp_min_24h",
        translation_key="temp_min_24h",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        agg_fn=_agg("temp", min),
    ),
)

# Fields from the 3-day ECMWF forecast as individual sensors (today/tomorrow).
# translation_key is a base here, the day suffix gets appended per entity.
DAY3_FIELDS: tuple[KmwValueDescription, ...] = (
    _temp("tempMax", "temp_max"),
    _temp("tempMin", "temp_min"),
    KmwValueDescription(
        key="precCurrent",
        translation_key="precipitation",
        native_unit_of_measurement=UnitOfPrecipitationDepth.MILLIMETERS,
        device_class=SensorDeviceClass.PRECIPITATION,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    KmwValueDescription(
        key="sunHours",
        translation_key="sun_hours",
        native_unit_of_measurement=UnitOfTime.HOURS,
        icon="mdi:white-balance-sunny",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
)

# Behave like sun.sun: always show the *next* occurrence, not today's.
ASTRO_TODAY_TIMESTAMPS: tuple[tuple[str, str, str], ...] = (
    ("sunrise", "next_sunrise", "mdi:weather-sunset-up"),
    ("sunset", "next_sunset", "mdi:weather-sunset-down"),
    ("transit", "next_solar_noon", "mdi:sun-angle"),
    ("civilDawn", "next_civil_dawn", "mdi:weather-sunset-up"),
    ("civilDusk", "next_civil_dusk", "mdi:weather-sunset-down"),
    ("nauticalDawn", "next_nautical_dawn", "mdi:weather-sunset-up"),
    ("nauticalDusk", "next_nautical_dusk", "mdi:weather-sunset-down"),
    ("astronomicalDawn", "next_astronomical_dawn", "mdi:weather-night"),
    ("astronomicalDusk", "next_astronomical_dusk", "mdi:weather-night"),
    ("moonRise", "next_moonrise", "mdi:moon-waxing-crescent"),
    ("moonSet", "next_moonset", "mdi:moon-waning-crescent"),
)

ASTRO_ROOT_TIMESTAMPS: tuple[tuple[str, str, str], ...] = (
    ("nextFullMoon", "next_full_moon", "mdi:moon-full"),
    ("nextNewMoon", "next_new_moon", "mdi:moon-new"),
)


def _tz_of(data: dict[str, Any] | None) -> ZoneInfo:
    try:
        return ZoneInfo((data or {}).get("timeZone") or "UTC")
    except (KeyError, ValueError):
        return ZoneInfo("UTC")


def _target_date(data: dict[str, Any] | None, offset: int) -> str:
    """Date of today+offset in the payload's own local time zone."""
    return (dt_util.now(_tz_of(data)).date() + timedelta(days=offset)).isoformat()


def _row_for_day(
    data: dict[str, Any] | None, offset: int, list_key: str = "data"
) -> dict[str, Any] | None:
    """Find the entry for today+offset by date (in the payload's time zone)."""
    if not data:
        return None
    target = _target_date(data, offset)
    for row in data.get(list_key) or []:
        if str(row.get("dateTime") or "")[:10] == target:
            return row
    return None


class KmwSensor(CoordinatorEntity[KmwCoordinator], SensorEntity):
    """Base class: coordinator + device + description."""

    _attr_attribution = ATTRIBUTION
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: KmwCoordinator,
        unique_id: str,
        device_info: DeviceInfo,
        description: SensorEntityDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = unique_id
        self._attr_device_info = device_info

    def _scaled(self, value: Any) -> Any:
        num = _num(value)
        if num is None:
            return value
        scale = getattr(self.entity_description, "scale", None)
        return num * scale if scale else num


class KmwCurrentSensor(KmwSensor):
    """A single value from /current (data.<key>.value)."""

    @property
    def native_value(self) -> Any:
        data = (self.coordinator.data or {}).get("data") or {}
        entry = data.get(self.entity_description.key)
        if not isinstance(entry, dict):
            return None
        return self._scaled(entry.get("value"))

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        data = (self.coordinator.data or {}).get("data") or {}
        entry = data.get(self.entity_description.key)
        if not isinstance(entry, dict):
            return None
        attrs: dict[str, Any] = {}
        if "dateTime" in entry:
            attrs["measured_at"] = entry["dateTime"]
        if "source" in entry:
            attrs["source"] = entry["source"]
        return attrs or None


class KmwStationSensor(KmwSensor, RestoreSensor):
    """A single value from the station observations.

    Stations do not report every parameter in every observation (prec6h/24h
    only at synoptic hours, tempMin1h/Max1h only once an hour). The last known
    value therefore stays put — across Home Assistant restarts too, via
    RestoreSensor. The ``measured_at`` attribute says when it was taken.
    """

    _last_value: Any = None
    _last_measured: str | None = None

    def _entry(self) -> dict[str, Any] | None:
        data = (self.coordinator.data or {}).get("data") or {}
        entry = data.get(self.entity_description.key)
        return entry if isinstance(entry, dict) else None

    @callback
    def _refresh_cache(self) -> None:
        if (entry := self._entry()) is not None:
            self._last_value = self._scaled(entry.get("value"))
            self._last_measured = entry.get("dateTime")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._refresh_cache()
        if self._last_value is None:
            last = await self.async_get_last_sensor_data()
            if last is not None and last.native_value is not None:
                self._last_value = last.native_value
                if (state := await self.async_get_last_state()) is not None:
                    self._last_measured = state.attributes.get("measured_at")

    @callback
    def _handle_coordinator_update(self) -> None:
        self._refresh_cache()
        super()._handle_coordinator_update()

    @property
    def native_value(self) -> Any:
        return self._last_value

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self._last_measured:
            return {"measured_at": self._last_measured}
        return None


class KmwForecastAggSensor(KmwSensor):
    """An aggregate over the hourly 24 h forecast."""

    entity_description: KmwAggDescription

    @property
    def native_value(self) -> float | None:
        rows = (self.coordinator.data or {}).get("data") or []
        return self.entity_description.agg_fn(rows)


class KmwDay3Sensor(KmwSensor, RestoreSensor):
    """Precipitation probability plus day details from the 3-day ECMWF forecast.

    The 3day forecast drops "today" from its response in the late afternoon,
    so the last seen day entry stays valid until midnight and survives a
    restart via RestoreSensor.
    """

    _unrecorded_attributes = frozenset({"time_of_day"})

    ATTR_KEYS = (
        "dateTime", "tempMax", "tempMin", "precCurrent", "precProb1mm",
        "precProb10mm", "windSpeed", "windGust", "windDirection",
        "sunHours", "cloudCoverage", "weatherSymbol",
    )

    def __init__(
        self,
        coordinator: KmwCoordinator,
        unique_id: str,
        device_info: DeviceInfo,
        offset: int,
        suffix: str,
    ) -> None:
        description = SensorEntityDescription(
            key=f"day3_{offset}",
            translation_key=f"precip_probability_{suffix}",
            native_unit_of_measurement=PERCENTAGE,
            icon="mdi:weather-pouring",
            state_class=SensorStateClass.MEASUREMENT,
            suggested_display_precision=0,
        )
        super().__init__(coordinator, unique_id, device_info, description)
        self._offset = offset
        self._cache_row: dict[str, Any] | None = None

    @callback
    def _refresh_cache(self) -> None:
        if (row := _row_for_day(self.coordinator.data, self._offset)) is not None:
            self._cache_row = row

    def _valid_row(self) -> dict[str, Any] | None:
        row = self._cache_row
        target = _target_date(self.coordinator.data, self._offset)
        if row and str(row.get("dateTime") or "")[:10] == target:
            return row
        return None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._refresh_cache()
        if self._cache_row is None:
            state = await self.async_get_last_state()
            if (
                state is not None
                and state.state not in ("unknown", "unavailable")
                and state.attributes.get("dateTime")
            ):
                row = {k: state.attributes.get(k) for k in self.ATTR_KEYS}
                row["precProb"] = _num(state.state)
                if (tod := state.attributes.get("time_of_day")) is not None:
                    row["timeOfDay"] = tod
                self._cache_row = row

    @callback
    def _handle_coordinator_update(self) -> None:
        self._refresh_cache()
        super()._handle_coordinator_update()

    @property
    def native_value(self) -> float | None:
        row = self._valid_row()
        return _num(row.get("precProb")) if row else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        row = self._valid_row()
        if not row:
            return None
        attrs = {k: row.get(k) for k in self.ATTR_KEYS if row.get(k) is not None}
        if tod := row.get("timeOfDay"):
            attrs["time_of_day"] = tod
        return attrs or None


class KmwDay3FieldSensor(KmwSensor, RestoreSensor):
    """A single field (Tmax, Tmin, precipitation, sunshine) for today/tomorrow.

    Same idea as the day sensor above: the last seen value stays valid until
    its date no longer matches today+offset, and is restored after a restart.
    """

    def __init__(
        self,
        coordinator: KmwCoordinator,
        unique_id: str,
        device_info: DeviceInfo,
        description: KmwValueDescription,
        offset: int,
        suffix: str,
    ) -> None:
        per_day = KmwValueDescription(
            key=description.key,
            translation_key=f"{description.translation_key}_{suffix}",
            native_unit_of_measurement=description.native_unit_of_measurement,
            device_class=description.device_class,
            state_class=description.state_class,
            icon=description.icon,
            suggested_display_precision=description.suggested_display_precision,
        )
        super().__init__(coordinator, unique_id, device_info, per_day)
        self._offset = offset
        self._cache_date: str | None = None
        self._cache_value: float | None = None

    @callback
    def _refresh_cache(self) -> None:
        row = _row_for_day(self.coordinator.data, self._offset)
        if row is None:
            return
        value = _num(row.get(self.entity_description.key))
        if value is not None:
            self._cache_value = value
            self._cache_date = str(row.get("dateTime") or "")[:10]

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._refresh_cache()
        if self._cache_value is None:
            last = await self.async_get_last_sensor_data()
            state = await self.async_get_last_state()
            date = state.attributes.get("date") if state else None
            if (
                last is not None
                and isinstance(last.native_value, (int, float))
                and date
            ):
                self._cache_value = float(last.native_value)
                self._cache_date = date

    @callback
    def _handle_coordinator_update(self) -> None:
        self._refresh_cache()
        super()._handle_coordinator_update()

    @property
    def native_value(self) -> float | None:
        if self._cache_date == _target_date(self.coordinator.data, self._offset):
            return self._cache_value
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self._cache_date == _target_date(self.coordinator.data, self._offset):
            return {"date": self._cache_date}
        return None


class KmwRisksSensor(KmwSensor, RestoreSensor):
    """Weather risks (thunderstorm, storm, ...) for today from the 3-day
    forecast. State is the number of risks; the risk objects themselves are
    attributes. Same caching rules as the other day-scoped sensors, since
    "today" drops out of the 3day response in the late afternoon.
    """

    def __init__(
        self,
        coordinator: KmwCoordinator,
        unique_id: str,
        device_info: DeviceInfo,
    ) -> None:
        description = SensorEntityDescription(
            key="risks_today",
            translation_key="risks_today",
            icon="mdi:alert-outline",
        )
        super().__init__(coordinator, unique_id, device_info, description)
        self._cache_date: str | None = None
        self._cache_risks: list[dict[str, Any]] = []

    @callback
    def _refresh_cache(self) -> None:
        row = _row_for_day(self.coordinator.data, 0)
        if row is None:
            return
        self._cache_risks = row.get("risks") or []
        self._cache_date = str(row.get("dateTime") or "")[:10]

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._refresh_cache()
        if self._cache_date is None:
            state = await self.async_get_last_state()
            if (
                state is not None
                and state.state not in ("unknown", "unavailable")
                and state.attributes.get("date")
            ):
                self._cache_date = state.attributes.get("date")
                self._cache_risks = state.attributes.get("risks") or []

    @callback
    def _handle_coordinator_update(self) -> None:
        self._refresh_cache()
        super()._handle_coordinator_update()

    def _valid(self) -> bool:
        return self._cache_date == _target_date(self.coordinator.data, 0)

    @property
    def native_value(self) -> int | None:
        return len(self._cache_risks) if self._valid() else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if not self._valid():
            return None
        attrs: dict[str, Any] = {"date": self._cache_date, "risks": self._cache_risks}
        if (tomorrow := _row_for_day(self.coordinator.data, 1)) is not None:
            attrs["risks_tomorrow"] = tomorrow.get("risks") or []
        return attrs


class KmwTrendSensor(KmwSensor):
    """Precipitation total over the 14 trend days; full day list as attribute."""

    _unrecorded_attributes = frozenset({"forecast"})

    @property
    def native_value(self) -> float | None:
        rows = (self.coordinator.data or {}).get("data") or []
        values = [v for r in rows if (v := _num(r.get("prec"))) is not None]
        return round(sum(values), 1) if values else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        data = self.coordinator.data or {}
        if not data:
            return None
        return {
            "run": data.get("run"),
            "next_run": data.get("nextRun"),
            "forecast": data.get("data"),
        }


class KmwTrendHeatDaysSensor(KmwSensor):
    """Number of days with Tmax >= 30 °C in the next 14 days."""

    HEAT_THRESHOLD = 30.0

    @property
    def native_value(self) -> int | None:
        rows = (self.coordinator.data or {}).get("data") or []
        if not rows:
            return None
        return sum(
            1
            for r in rows
            if (v := _num(r.get("tempMax"))) is not None and v >= self.HEAT_THRESHOLD
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        rows = (self.coordinator.data or {}).get("data") or []
        hot = [
            {"date": r.get("dateTime"), "temp_max": r.get("tempMax")}
            for r in rows
            if (v := _num(r.get("tempMax"))) is not None and v >= self.HEAT_THRESHOLD
        ]
        return {"threshold": self.HEAT_THRESHOLD, "days": hot}


class KmwAstroTimestampSensor(KmwSensor):
    """An astronomical instant — always the *next* one, like sun.sun.

    The API returns 14 days; the earliest instant still in the future wins.
    A timer on exactly that instant then advances to the following event, so
    the sensor does not hang in the past until the next coordinator update.
    """

    def __init__(
        self,
        coordinator: KmwCoordinator,
        unique_id: str,
        device_info: DeviceInfo,
        key: str,
        translation_key: str,
        icon: str,
        from_root: bool = False,
    ) -> None:
        description = SensorEntityDescription(
            key=key,
            translation_key=translation_key,
            icon=icon,
            device_class=SensorDeviceClass.TIMESTAMP,
        )
        super().__init__(coordinator, unique_id, device_info, description)
        self._from_root = from_root
        self._unsub_rollover: CALLBACK_TYPE | None = None

    @property
    def native_value(self) -> datetime | None:
        data = self.coordinator.data or {}
        key = self.entity_description.key

        if self._from_root:
            # nextFullMoon / nextNewMoon already point at the future.
            return dt_util.parse_datetime(data.get(key) or "")

        now = dt_util.utcnow()
        upcoming: list[datetime] = []
        for row in data.get("dailyData") or []:
            # May be missing: moonrise/moonset on some days, and at high
            # latitudes the astronomical twilight during summer.
            if (value := dt_util.parse_datetime(row.get(key) or "")) and value > now:
                upcoming.append(value)
        return min(upcoming) if upcoming else None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self._cancel_rollover)
        self._schedule_rollover()

    @callback
    def _handle_coordinator_update(self) -> None:
        self._schedule_rollover()
        super()._handle_coordinator_update()

    @callback
    def _cancel_rollover(self) -> None:
        if self._unsub_rollover is not None:
            self._unsub_rollover()
            self._unsub_rollover = None

    @callback
    def _schedule_rollover(self) -> None:
        """Re-evaluate exactly when the current instant has passed."""
        self._cancel_rollover()
        if (value := self.native_value) is None:
            return
        now = dt_util.utcnow()
        target = value + timedelta(seconds=1)
        if target <= now:
            # Value is already in the past — e.g. a full moon whose successor
            # the API has not published yet. Look again later instead of
            # firing immediately in a loop.
            target = now + timedelta(hours=1)
        self._unsub_rollover = async_track_point_in_utc_time(
            self.hass, self._rollover, target
        )

    @callback
    def _rollover(self, now: datetime) -> None:
        self._unsub_rollover = None
        if self._from_root:
            # For full/new moon we don't know the next date — fetch it.
            self.hass.async_create_task(self.coordinator.async_request_refresh())
        else:
            self.async_write_ha_state()
        self._schedule_rollover()


class KmwAstroNumberSensor(KmwSensor):
    """Moon phase / moon illumination for today."""

    @property
    def native_value(self) -> float | None:
        row = _row_for_day(self.coordinator.data, 0, "dailyData")
        return _num(row.get(self.entity_description.key)) if row else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.key != "moonPhase":
            return None
        value = self.native_value
        if value is None:
            return None
        if value == 0:
            phase = "new_moon"
        elif value < 50:
            phase = "waxing"
        elif value == 50:
            phase = "full_moon"
        else:
            phase = "waning"
        return {"phase": phase}


class KmwAstroSummarySensor(KmwSensor):
    """Length of today; all 14 astronomy days as an attribute list."""

    _unrecorded_attributes = frozenset({"days"})

    @property
    def native_value(self) -> float | None:
        row = _row_for_day(self.coordinator.data, 0, "dailyData")
        if not row:
            return None
        sunrise = dt_util.parse_datetime(row.get("sunrise") or "")
        sunset = dt_util.parse_datetime(row.get("sunset") or "")
        if not sunrise or not sunset:
            return None
        return round((sunset - sunrise).total_seconds() / 3600, 2)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        data = self.coordinator.data or {}
        if not data:
            return None
        return {
            "next_full_moon": data.get("nextFullMoon"),
            "next_new_moon": data.get("nextNewMoon"),
            "days": data.get("dailyData"),
        }


class KmwApiBudgetSensor(KmwSensor):
    """Remaining API requests according to the rate-limit headers."""

    def __init__(
        self,
        coordinator: KmwCoordinator,
        unique_id: str,
        device_info: DeviceInfo,
        data: KmwData,
    ) -> None:
        description = SensorEntityDescription(
            key="api_remaining",
            translation_key="api_requests_remaining",
            icon="mdi:counter",
            state_class=SensorStateClass.MEASUREMENT,
            entity_category=EntityCategory.DIAGNOSTIC,
        )
        super().__init__(coordinator, unique_id, device_info, description)
        self._api = data.api

    @property
    def native_value(self) -> int | None:
        return self._api.rate_remaining

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        return {
            "daily_limit": self._api.rate_limit,
            "reset": self._api.rate_retry_after,
        }


async def async_setup_entry(
    hass: HomeAssistant,
    entry: KmwConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = entry.runtime_data
    eid = entry.entry_id

    loc_device = DeviceInfo(
        identifiers={(DOMAIN, eid)},
        name="Kachelmannwetter",
        manufacturer="Meteologix AG",
        model="Kachelmannwetter API v2",
        entry_type=DeviceEntryType.SERVICE,
    )

    entities: list[SensorEntity] = [
        KmwCurrentSensor(data.current, f"{eid}_current_{d.key}", loc_device, d)
        for d in CURRENT_SENSORS
    ]
    current_snapshot = (data.current.data or {}).get("data") or {}
    entities += [
        KmwCurrentSensor(data.current, f"{eid}_current_{d.key}", loc_device, d)
        for d in CURRENT_OPTIONAL_SENSORS
        if d.key in current_snapshot
    ]
    entities += [
        KmwForecastAggSensor(data.fc1h, f"{eid}_agg_{d.key}", loc_device, d)
        for d in AGG_SENSORS
    ]
    entities.append(KmwRisksSensor(data.day3, f"{eid}_day3_risks", loc_device))

    day_suffixes = ((0, "today"), (1, "tomorrow"), (2, "day_after_tomorrow"))
    entities += [
        KmwDay3Sensor(
            data.day3, f"{eid}_day3_prob_{offset}", loc_device, offset, suffix
        )
        for offset, suffix in day_suffixes
    ]
    entities += [
        KmwDay3FieldSensor(
            data.day3, f"{eid}_day3_{d.key}_{offset}", loc_device, d, offset, suffix
        )
        for offset, suffix in day_suffixes[:2]
        for d in DAY3_FIELDS
    ]

    entities.append(
        KmwTrendSensor(
            data.trend,
            f"{eid}_trend",
            loc_device,
            SensorEntityDescription(
                key="trend14days",
                translation_key="trend_precip_sum_14d",
                icon="mdi:calendar-range",
                native_unit_of_measurement=UnitOfPrecipitationDepth.MILLIMETERS,
                device_class=SensorDeviceClass.PRECIPITATION,
                suggested_display_precision=1,
            ),
        )
    )
    entities.append(
        KmwTrendHeatDaysSensor(
            data.trend,
            f"{eid}_trend_hitzetage",
            loc_device,
            SensorEntityDescription(
                key="trend_heat_days",
                translation_key="trend_heat_days",
                icon="mdi:thermometer-alert",
                native_unit_of_measurement=UnitOfTime.DAYS,
            ),
        )
    )

    entities += [
        KmwAstroTimestampSensor(
            data.astro, f"{eid}_astro_{key}", loc_device, key, translation_key, icon
        )
        for key, translation_key, icon in ASTRO_TODAY_TIMESTAMPS
    ]
    entities += [
        KmwAstroTimestampSensor(
            data.astro, f"{eid}_astro_{key}", loc_device, key, translation_key, icon,
            from_root=True,
        )
        for key, translation_key, icon in ASTRO_ROOT_TIMESTAMPS
    ]
    entities += [
        KmwAstroNumberSensor(
            data.astro,
            f"{eid}_astro_{d.key}",
            loc_device,
            d,
        )
        for d in (
            KmwValueDescription(
                key="moonIllumination",
                translation_key="moon_illumination",
                native_unit_of_measurement=PERCENTAGE,
                icon="mdi:moon-waxing-gibbous",
                state_class=SensorStateClass.MEASUREMENT,
                suggested_display_precision=0,
            ),
            KmwValueDescription(
                key="moonPhase",
                translation_key="moon_phase",
                native_unit_of_measurement=PERCENTAGE,
                icon="mdi:moon-waning-crescent",
                suggested_display_precision=0,
            ),
        )
    ]
    entities.append(
        KmwAstroSummarySensor(
            data.astro,
            f"{eid}_astro_summary",
            loc_device,
            SensorEntityDescription(
                key="astro_summary",
                translation_key="day_length",
                icon="mdi:sun-clock",
                native_unit_of_measurement=UnitOfTime.HOURS,
                device_class=SensorDeviceClass.DURATION,
                state_class=SensorStateClass.MEASUREMENT,
                suggested_display_precision=2,
            ),
        )
    )

    entities.append(
        KmwApiBudgetSensor(data.current, f"{eid}_api_budget", loc_device, data)
    )

    if data.station is not None:
        station_device = DeviceInfo(
            identifiers={(DOMAIN, f"{eid}_station")},
            name=f"Weather station {data.station_name or data.station_id}",
            manufacturer="Meteologix AG",
            model=f"Station {data.station_id}",
            entry_type=DeviceEntryType.SERVICE,
        )
        snapshot = (data.station.data or {}).get("data") or {}
        if snapshot:
            wanted = set(snapshot) | STATION_ALWAYS_KEYS
            station_descs = [d for d in STATION_SENSORS if d.key in wanted]
        else:
            # Station unreachable during setup -> create every known field.
            station_descs = list(STATION_SENSORS)
        entities += [
            KmwStationSensor(data.station, f"{eid}_station_{d.key}", station_device, d)
            for d in station_descs
        ]

    async_add_entities(entities)
