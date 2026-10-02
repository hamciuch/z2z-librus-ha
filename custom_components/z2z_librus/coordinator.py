from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import LibrusAuthError, LibrusClient
from .const import (
    CONF_REPLIES_ENABLED,
    CONF_SCAN_INTERVAL,
    DEFAULT_REPLIES_ENABLED,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from .notifier import NewItemTracker
from .reply import ReplyDraft

_LOGGER = logging.getLogger(__name__)

API_STATE_VERSION = 1
API_STATE_SAVE_DELAY = 60  # seconds; coalesces writes


def api_state_key(entry_id: str) -> str:
    return f"{DOMAIN}.{entry_id}.api"


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
        self.tracker = NewItemTracker(hass, entry.entry_id)
        # Last good API-only data (point grades, notes) survives restarts,
        # so an API outage right after a restart does not empty them (0.9.0).
        self._api_store = Store(hass, API_STATE_VERSION, api_state_key(entry.entry_id))
        self._api_state_loaded = False

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
        if not self._api_state_loaded:
            self._api_state_loaded = True
            try:
                self.client.import_state(await self._api_store.async_load())
            except Exception:  # a broken cache file must never block startup
                _LOGGER.exception("Unable to load saved Librus API data")
        data = await self._fetch_with_relogin()
        if self.client.state_changed:
            state = self.client.export_state()
            self._api_store.async_delay_save(lambda: state, API_STATE_SAVE_DELAY)
        try:
            await self.tracker.async_process(data)
        except Exception:  # notifications are optional; never break the refresh
            _LOGGER.exception("Unable to detect new Librus items")
        return data

    async def _fetch_with_relogin(self):
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
