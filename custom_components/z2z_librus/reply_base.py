from __future__ import annotations

from homeassistant.core import callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .sensor import _ident, _student_name


class ReplyEntity(CoordinatorEntity):
    """Common base of the reply entities (select / text / button)."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, entry, suffix):
        super().__init__(coordinator)
        self.entry = entry
        self._suffix = suffix

    @property
    def unique_id(self):
        return f"{_ident(self.coordinator.data, self.entry.entry_id)}_{self._suffix}"

    @property
    def device_info(self):
        ident = _ident(self.coordinator.data, self.entry.entry_id)
        return DeviceInfo(
            identifiers={(DOMAIN, ident)},
            name=f"Librus – {_student_name(self.coordinator.data)}",
            manufacturer="Librus (unofficial)",
            model="Synergia",
            configuration_url="https://synergia.librus.pl/",
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.coordinator.draft.add_listener(self._draft_changed))

    @callback
    def _draft_changed(self) -> None:
        self.async_write_ha_state()
