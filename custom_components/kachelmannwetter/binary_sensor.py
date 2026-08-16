"""Binary sensors for Kachelmannwetter."""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTRIBUTION, DOMAIN
from .coordinator import KmwConfigEntry, KmwCoordinator

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: KmwConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([KmwIsDaySensor(entry)])


class KmwIsDaySensor(CoordinatorEntity[KmwCoordinator], BinarySensorEntity):
    """isDay from /current."""

    _attr_attribution = ATTRIBUTION
    _attr_has_entity_name = True
    _attr_translation_key = "is_day"
    _attr_icon = "mdi:theme-light-dark"

    def __init__(self, entry: KmwConfigEntry) -> None:
        super().__init__(entry.runtime_data.current)
        self._attr_unique_id = f"{entry.entry_id}_is_day"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Kachelmannwetter",
            manufacturer="Meteologix AG",
            model="Kachelmannwetter API v2",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def is_on(self) -> bool | None:
        data = (self.coordinator.data or {}).get("data") or {}
        entry = data.get("isDay")
        if isinstance(entry, dict):
            value = entry.get("value")
            return bool(value) if value is not None else None
        return None
