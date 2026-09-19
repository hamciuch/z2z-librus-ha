from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from .const import CONF_REPLIES_ENABLED, DEFAULT_REPLIES_ENABLED, DOMAIN
from .reply import STATUS_FAILED, ReplyError, async_send_from_draft
from .reply_base import ReplyEntity


async def async_setup_entry(hass, entry, async_add_entities):
    if not entry.options.get(CONF_REPLIES_ENABLED, DEFAULT_REPLIES_ENABLED):
        return
    c = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([SendMessageButton(c, entry)])


class SendMessageButton(ReplyEntity, ButtonEntity):
    _attr_name = "Wyślij wiadomość"
    _attr_icon = "mdi:send"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "reply_send")

    async def async_press(self) -> None:
        try:
            result = await async_send_from_draft(self.coordinator)
        except ReplyError as err:
            self.coordinator.draft.set_status(STATUS_FAILED, message=str(err))
            raise ServiceValidationError(str(err)) from err
        if result["status"] == "failed":
            raise HomeAssistantError(
                f"Librus: {result.get('message') or 'nie udało się wysłać wiadomości'}"
            )
