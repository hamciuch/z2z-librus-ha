from __future__ import annotations
from datetime import timedelta
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from .api import LibrusClient, LibrusAuthError
from .const import DOMAIN, CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL

class LibrusCoordinator(DataUpdateCoordinator):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: LibrusClient):
        super().__init__(hass, logger=__import__('logging').getLogger(__name__), name=DOMAIN,
                         update_interval=timedelta(minutes=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)))
        self.entry=entry; self.client=client

    async def _async_update_data(self):
        try:
            try: core=await self.client.fetch_core()
            except LibrusAuthError:
                await self.client.login(); core=await self.client.fetch_core()
            core["grades"] = await self.client.enrich_grades(core.get("grades_raw", {}))
            return core
        except Exception as err:
            raise UpdateFailed(str(err)) from err
