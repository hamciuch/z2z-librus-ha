from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import LibrusAuthError, LibrusClient
from .const import (
    CONF_REPLIES_ENABLED,
    CONF_SCAN_INTERVAL,
    DEFAULT_REPLIES_ENABLED,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from .reply import ReplyDraft

_LOGGER = logging.getLogger(__name__)


class LibrusCoordinator(DataUpdateCoordinator):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: LibrusClient) -> None:
        super().__init__(
            hass,
            logger=_LOGGER,
            name=f"{DOMAIN}_{entry.entry_id}",
            update_interval=timedelta(
                minutes=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
            ),
        )
        self.entry = entry
        self.client = client
        self.draft = ReplyDraft()
        self.send_lock = asyncio.Lock()

    async def _fetch(self):
        data = await self.client.fetch_core()
        if self.entry.options.get(CONF_REPLIES_ENABLED, DEFAULT_REPLIES_ENABLED):
            try:
                data["recipients"] = await self.client.get_recipients()
            except Exception as err:  # replies are optional; never break the refresh
                _LOGGER.warning("Unable to load Librus recipients: %s", err)
                data["recipients"] = []
        return data

    async def _async_update_data(self):
        try:
            return await self._fetch()
        except LibrusAuthError:
            try:
                await self.client.login()
                return await self._fetch()
            except Exception as err:
                raise UpdateFailed(str(err)) from err
        except Exception as err:
            raise UpdateFailed(str(err)) from err
