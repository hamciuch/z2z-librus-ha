from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from .const import DOMAIN, PLATFORMS
from .api import LibrusClient
from .coordinator import LibrusCoordinator

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    client=LibrusClient(async_get_clientsession(hass), entry.data["username"], entry.data["password"])
    await client.login()
    coordinator=LibrusCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    hass.data.setdefault(DOMAIN,{})[entry.entry_id]=coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    ok=await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok: hass.data[DOMAIN].pop(entry.entry_id, None)
    return ok
