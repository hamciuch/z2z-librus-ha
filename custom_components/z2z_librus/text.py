from __future__ import annotations

from homeassistant.components.text import TextEntity, TextMode

from .const import CONF_REPLIES_ENABLED, DEFAULT_REPLIES_ENABLED, DOMAIN
from .reply import MAX_TITLE
from .reply_base import ReplyEntity

# A Home Assistant state is limited to 255 characters; longer messages can be
# sent with the z2z_librus.reply_message / send_message services.
TEXT_MAX = 255


async def async_setup_entry(hass, entry, async_add_entities):
    if not entry.options.get(CONF_REPLIES_ENABLED, DEFAULT_REPLIES_ENABLED):
        return
    c = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([ReplyTitleText(c, entry), ReplyContentText(c, entry)])


class ReplyTitleText(ReplyEntity, TextEntity):
    _attr_name = "Temat wiadomości"
    _attr_icon = "mdi:format-title"
    _attr_mode = TextMode.TEXT
    _attr_native_min = 0
    _attr_native_max = MAX_TITLE

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "reply_title")

    @property
    def native_value(self):
        return self.coordinator.draft.title

    async def async_set_value(self, value: str) -> None:
        self.coordinator.draft.title = value
        self.async_write_ha_state()


class ReplyContentText(ReplyEntity, TextEntity):
    _attr_name = "Treść wiadomości"
    _attr_icon = "mdi:message-text"
    _attr_mode = TextMode.TEXT
    _attr_native_min = 0
    _attr_native_max = TEXT_MAX

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "reply_content")

    @property
    def native_value(self):
        return self.coordinator.draft.content

    async def async_set_value(self, value: str) -> None:
        self.coordinator.draft.content = value
        self.async_write_ha_state()
