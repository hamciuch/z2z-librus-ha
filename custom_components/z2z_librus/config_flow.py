from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.data_entry_flow import FlowResult

from .api import LibrusAuthError, LibrusClient
from .const import DOMAIN


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 2

    async def async_step_user(self, user_input=None) -> FlowResult:
        errors = {}

        if user_input is not None:
            client = LibrusClient(
                user_input[CONF_USERNAME],
                user_input[CONF_PASSWORD],
            )
            try:
                await client.login()
                student = await client.get_student()

                # Multi-student support: each Librus account/login is a separate entry.
                unique = str(student.get("AccountId") or user_input[CONF_USERNAME])
                await self.async_set_unique_id(unique)
                self._abort_if_unique_id_configured()

                title = student.get("Name") or user_input[CONF_USERNAME]
                return self.async_create_entry(
                    title=f"Librus – {title}",
                    data=user_input,
                )
            except LibrusAuthError:
                errors["base"] = "invalid_auth"
            except Exception:
                errors["base"] = "cannot_connect"

        schema = vol.Schema(
            {
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): str,
            }
        )
        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
        )
