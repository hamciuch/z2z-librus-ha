import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_USERNAME, CONF_PASSWORD
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from .const import DOMAIN
from .api import LibrusClient, LibrusAuthError

class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION=1
    async def async_step_user(self, user_input=None):
        errors={}
        if user_input:
            client=LibrusClient(async_get_clientsession(self.hass), user_input[CONF_USERNAME], user_input[CONF_PASSWORD])
            try:
                await client.login(); me=await client.get("Me")
                account=str((me.get("Me") or me).get("AccountId") or (me.get("Me") or me).get("Id") or user_input[CONF_USERNAME])
                await self.async_set_unique_id(account); self._abort_if_unique_id_configured()
                return self.async_create_entry(title=f"Librus {user_input[CONF_USERNAME]}", data=user_input)
            except LibrusAuthError: errors["base"]="invalid_auth"
            except Exception: errors["base"]="cannot_connect"
        schema=vol.Schema({vol.Required(CONF_USERNAME):str,vol.Required(CONF_PASSWORD):str})
        return self.async_show_form(step_id="user",data_schema=schema,errors=errors)
