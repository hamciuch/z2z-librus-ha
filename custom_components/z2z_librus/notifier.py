"""Detect new grades / messages / tests and fire Home Assistant events.

Keys of items already seen are persisted in .storage, so a Home Assistant
restart (or an integration reload) never re-announces old items. The very
first run only records a baseline and fires nothing.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState, Event, HomeAssistant, callback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.storage import Store

from .api import grade_content_key
from .const import (
    DOMAIN,
    EVENT_NEW_GRADE,
    EVENT_NEW_MESSAGE,
    EVENT_NEW_NOTE,
    EVENT_NEW_TEST,
)

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
# Bumped when a key format changes; those kinds get a silent new baseline.
KEY_SCHEMA = 3
# 3 (0.8.1): API data of one child could belong to the other (shared cookie
# jar) - grades and notes get a silent new baseline after the fix.
SCHEMA_RESET_KINDS = {2: ("grades",), 3: ("grades", "notes")}
# Seen keys kept per kind (a school year has a few hundred grades at most).
MAX_KEYS = 3000
# More "new" items than this in one refresh is not real news (e.g. Librus
# changed its ids): remember them silently instead of spamming.
BURST_LIMIT = 25
# Events found during Home Assistant startup are held back until HA has
# started plus this delay, so automations are already listening (0.6.5).
STARTUP_DELAY_SECONDS = 30


def _join(item: dict[str, Any], *fields: str) -> str:
    return "|".join(str(item.get(f) or "") for f in fields)


def grade_key(g: dict[str, Any]) -> str:
    """Content-based (0.8.0): the same grade from the API or from the grades
    page has the same key; repeats on one day get #1, #2, ..."""
    return grade_content_key(g)


def message_key(m: dict[str, Any]) -> str:
    return str(m.get("href") or _join(m, "date", "author", "title"))


def note_key(n: dict[str, Any]) -> str:
    if n.get("id"):
        return f"id:{n['id']}"
    return _join(n, "date", "teacher", "text")


def test_key(t: dict[str, Any]) -> str:
    return str(t.get("href") or _join(t, "date", "subject", "title"))


# kind -> (items from coordinator data, key function, event type, newest first?)
KINDS: dict[str, tuple[Callable[[dict], list], Callable[[dict], str], str, bool]] = {
    "grades": (lambda d: d.get("grades", []), grade_key, EVENT_NEW_GRADE, False),
    "messages": (lambda d: d.get("messages", []), message_key, EVENT_NEW_MESSAGE, True),
    "tests": (lambda d: d.get("schedule", []), test_key, EVENT_NEW_TEST, False),
    "notes": (lambda d: d.get("notes", []), note_key, EVENT_NEW_NOTE, False),
}


def student_name(data: dict[str, Any]) -> str:
    m = data.get("me", {})
    m = m.get("Me", m) if isinstance(m, dict) else {}
    return str(
        m.get("Name")
        or " ".join(filter(None, [m.get("FirstName"), m.get("LastName")]))
        or m.get("Login")
        or "Librus"
    )


class NewItemTracker:
    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self.hass = hass
        self.entry_id = entry_id
        self._store = Store(hass, STORAGE_VERSION, storage_key(entry_id))
        self._seen: dict[str, list[str]] | None = None
        self._schema_changed = False

    async def _load(self) -> None:
        stored = await self._store.async_load() or {}
        seen = stored.get("seen", {}) if isinstance(stored, dict) else {}
        self._seen = {k: list(v) for k, v in seen.items() if isinstance(v, list)}
        schema = stored.get("schema", 1) if isinstance(stored, dict) else 1
        for version in range(schema + 1, KEY_SCHEMA + 1):
            for kind in SCHEMA_RESET_KINDS.get(version, ()):
                self._seen.pop(kind, None)  # silent re-baseline below
        self._schema_changed = schema != KEY_SCHEMA

    async def async_process(self, data: dict[str, Any]) -> None:
        if self._seen is None:
            await self._load()

        student = student_name(data)
        changed = False
        to_fire: list[tuple[str, dict[str, Any]]] = []

        for kind, (getter, key_fn, event_type, newest_first) in KINDS.items():
            current: dict[str, dict[str, Any]] = {}
            repeats: dict[str, int] = {}
            for item in getter(data) or []:
                base = key_fn(item)
                n = repeats.get(base, 0)
                repeats[base] = n + 1
                current[f"{base}#{n}" if n else base] = item

            known = self._seen.get(kind)
            if known is None:
                # First run for this kind: remember everything, announce nothing.
                self._seen[kind] = list(current)[-MAX_KEYS:]
                changed = True
                _LOGGER.debug("Baseline for %s: %d items", kind, len(current))
                continue

            known_set = set(known)
            new = [(k, item) for k, item in current.items() if k not in known_set]
            if not new:
                continue

            # An empty/failed fetch never removes keys, so items coming back
            # later are still recognised as old.
            known.extend(k for k, _ in new)
            self._seen[kind] = known[-MAX_KEYS:]
            changed = True

            if len(new) > BURST_LIMIT:
                _LOGGER.warning(
                    "%d new %s at once - treating as a data change, no events fired",
                    len(new), kind,
                )
                continue

            if newest_first:
                new.reverse()  # announce oldest first
            to_fire.extend((event_type, item) for _, item in new)

        if changed or self._schema_changed:
            await self._store.async_save({"seen": self._seen, "schema": KEY_SCHEMA})
            self._schema_changed = False

        if not to_fire:
            return
        payloads = [
            (event_type, {"entry_id": self.entry_id, "student": student, **item})
            for event_type, item in to_fire
        ]
        if self.hass.state is CoreState.running:
            self._fire(payloads)
            return

        @callback
        def _fire_later(_now) -> None:
            self._fire(payloads)

        @callback
        def _started(_event: Event) -> None:
            async_call_later(self.hass, STARTUP_DELAY_SECONDS, _fire_later)

        _LOGGER.debug("Holding %d events until Home Assistant has started", len(payloads))
        self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _started)

    @callback
    def _fire(self, payloads: list[tuple[str, dict[str, Any]]]) -> None:
        for event_type, data in payloads:
            self.hass.bus.async_fire(event_type, data)


def storage_key(entry_id: str) -> str:
    return f"{DOMAIN}.{entry_id}.seen"


async def async_remove_store(hass: HomeAssistant, entry_id: str) -> None:
    await Store(hass, STORAGE_VERSION, storage_key(entry_id)).async_remove()
