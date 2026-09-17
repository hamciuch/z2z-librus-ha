from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

from .api import LibrusAuthError, LibrusClient
from .const import (
    CONF_LUNCH_ENABLED,
    CONF_LUNCH_TIME_FRIDAY,
    CONF_LUNCH_TIME_MONDAY,
    CONF_LUNCH_TIME_THURSDAY,
    CONF_LUNCH_TIME_TUESDAY,
    CONF_LUNCH_TIME_WEDNESDAY,
    DEFAULT_LUNCH_ENABLED,
    DEFAULT_LUNCH_TIME,
    DOMAIN,
)


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

    @staticmethod
    def async_get_options_flow(config_entry):
        return LibrusOptionsFlow()


class LibrusOptionsFlow(config_entries.OptionsFlowWithReload):
    async def async_step_init(self, user_input=None) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        options = self.config_entry.options

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_LUNCH_ENABLED,
                    default=options.get(CONF_LUNCH_ENABLED, DEFAULT_LUNCH_ENABLED),
                ): selector.BooleanSelector(),
                vol.Optional(
                    CONF_LUNCH_TIME_MONDAY,
                    default=options.get(CONF_LUNCH_TIME_MONDAY, DEFAULT_LUNCH_TIME),
                ): selector.TimeSelector(),
                vol.Optional(
                    CONF_LUNCH_TIME_TUESDAY,
                    default=options.get(CONF_LUNCH_TIME_TUESDAY, DEFAULT_LUNCH_TIME),
                ): selector.TimeSelector(),
                vol.Optional(
                    CONF_LUNCH_TIME_WEDNESDAY,
                    default=options.get(CONF_LUNCH_TIME_WEDNESDAY, DEFAULT_LUNCH_TIME),
                ): selector.TimeSelector(),
                vol.Optional(
                    CONF_LUNCH_TIME_THURSDAY,
                    default=options.get(CONF_LUNCH_TIME_THURSDAY, DEFAULT_LUNCH_TIME),
                ): selector.TimeSelector(),
                vol.Optional(
                    CONF_LUNCH_TIME_FRIDAY,
                    default=options.get(CONF_LUNCH_TIME_FRIDAY, DEFAULT_LUNCH_TIME),
                ): selector.TimeSelector(),
            }
        )

        return self.async_show_form(
            step_id="init",
            data_schema=schema,
        )
