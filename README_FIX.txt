Z2Z Librus v0.1.1 - login fix

Replace these files in your repository:
custom_components/z2z_librus/api.py
custom_components/z2z_librus/config_flow.py
custom_components/z2z_librus/__init__.py
custom_components/z2z_librus/coordinator.py
custom_components/z2z_librus/manifest.json

Then commit/push, reinstall/update via HACS and restart Home Assistant.

Main change:
Authentication now uses librus-apix>=1.5.1:
new_client() -> client.get_token(username, password)

This is the same authentication mechanism used by LukMaverick/LibrusSynergiaHA.
