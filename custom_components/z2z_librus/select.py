from __future__ import annotations

from homeassistant.components.select import SelectEntity

from .const import CONF_REPLIES_ENABLED, DEFAULT_REPLIES_ENABLED, DOMAIN
from .reply import PLACEHOLDER, build_targets
from .reply_base import ReplyEntity


async def async_setup_entry(hass, entry, async_add_entities):
    if not entry.options.get(CONF_REPLIES_ENABLED, DEFAULT_REPLIES_ENABLED):
        return
    c = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([ReplyTargetSelect(c, entry)])


class ReplyTargetSelect(ReplyEntity, SelectEntity):
    _attr_name = "Odbiorca wiadomości"
    _attr_icon = "mdi:account-arrow-right"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "reply_target")

    @property
    def options(self):
        return list(build_targets(self.coordinator.data))

    @property
    def current_option(self):
        target = self.coordinator.draft.target
        return target if target in build_targets(self.coordinator.data) else PLACEHOLDER

    async def async_select_option(self, option: str) -> None:
        if option in build_targets(self.coordinator.data):
            self.coordinator.draft.target = option
        self.async_write_ha_state()
