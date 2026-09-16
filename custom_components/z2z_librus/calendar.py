from __future__ import annotations

from datetime import datetime, timedelta

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


def _me(data):
    m = data.get("me", {})
    return m.get("Me", m) if isinstance(m, dict) else {}


def _student_name(data):
    m = _me(data)
    return m.get("Name") or str(m.get("Login") or "Librus")


def _ident(data, fallback):
    m = _me(data)
    return str(m.get("AccountId") or m.get("Login") or fallback)


def _in_range(event_start, start, end):
    start_date = start.date() if isinstance(start, datetime) else start
    end_date = end.date() if isinstance(end, datetime) else end
    e_date = event_start.date() if isinstance(event_start, datetime) else event_start
    return start_date <= e_date <= end_date


async def async_setup_entry(hass, entry, async_add_entities):
    c = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            AgendaCalendar(c, entry),
            TimetableCalendar(c, entry),
            HomeworkCalendar(c, entry),
        ]
    )


class BaseCalendar(CoordinatorEntity, CalendarEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, entry, suffix):
        super().__init__(coordinator)
        self.entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{suffix}"

    @property
    def device_info(self):
        ident = _ident(self.coordinator.data, self.entry.entry_id)
        return DeviceInfo(
            identifiers={(DOMAIN, ident)},
            name=f"Librus – {_student_name(self.coordinator.data)}",
            manufacturer="Librus (unofficial)",
            model="Synergia",
            configuration_url="https://synergia.librus.pl/",
        )

    @property
    def event(self):
        now = datetime.now().astimezone()
        events = self._events(now, now + timedelta(days=120))
        return events[0] if events else None

    async def async_get_events(self, hass, start_date, end_date):
        return self._events(start_date, end_date)


class AgendaCalendar(BaseCalendar):
    _attr_name = "Kartkówki i klasówki"
    _attr_icon = "mdi:clipboard-text-clock"

    def __init__(self, coordinator, entry):
        # Keep the old unique-id so existing calendar entity is reused.
        super().__init__(coordinator, entry, "agenda")

    def _events(self, start, end):
        out = []
        for x in self.coordinator.data.get("schedule", []):
            try:
                d = datetime.fromisoformat(x["date"]).date()
            except Exception:
                continue
            if not _in_range(d, start, end):
                continue

            description_parts = []
            if x.get("subject"):
                description_parts.append(f"Przedmiot: {x['subject']}")
            if x.get("number") not in (None, "", "unknown"):
                description_parts.append(f"Lekcja: {x['number']}")
            if x.get("data"):
                description_parts.append(str(x["data"]))

            out.append(
                CalendarEvent(
                    summary=x.get("title") or x.get("kind") or "Kartkówka / klasówka",
                    start=d,
                    end=d + timedelta(days=1),
                    description="\n".join(description_parts),
                )
            )
        return sorted(out, key=lambda e: e.start)


class TimetableCalendar(BaseCalendar):
    _attr_name = "Plan lekcji"
    _attr_icon = "mdi:calendar-clock"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "timetable")

    def _events(self, start, end):
        out = []
        tz = datetime.now().astimezone().tzinfo

        for x in self.coordinator.data.get("timetable", []):
            try:
                dt_start = datetime.fromisoformat(
                    f"{x['date']}T{x['date_from']}"
                ).replace(tzinfo=tz)
                dt_end = datetime.fromisoformat(
                    f"{x['date']}T{x['date_to']}"
                ).replace(tzinfo=tz)
            except Exception:
                continue

            if not _in_range(dt_start, start, end):
                continue

            desc = []
            if x.get("teacher_and_classroom"):
                desc.append(x["teacher_and_classroom"])
            if x.get("number") is not None:
                desc.append(f"Lekcja nr {x['number']}")
            if x.get("info"):
                desc.append(str(x["info"]))

            out.append(
                CalendarEvent(
                    summary=x.get("subject") or "Lekcja",
                    start=dt_start,
                    end=dt_end,
                    description="\n".join(desc),
                )
            )

        return sorted(out, key=lambda e: e.start)


class HomeworkCalendar(BaseCalendar):
    _attr_name = "Zadania domowe"
    _attr_icon = "mdi:book-open-page-variant"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "homework")

    def _events(self, start, end):
        out = []
        for x in self.coordinator.data.get("homework", []):
            raw = x.get("completion_date") or x.get("task_date")
            if not raw:
                continue
            try:
                d = datetime.fromisoformat(str(raw).replace("Z", "+00:00")).date()
            except Exception:
                continue
            if not _in_range(d, start, end):
                continue

            summary = x.get("subject") or x.get("lesson") or "Zadanie domowe"
            if x.get("category"):
                summary = f"{summary} – {x['category']}"

            out.append(
                CalendarEvent(
                    summary=summary,
                    start=d,
                    end=d + timedelta(days=1),
                    description="\n".join(
                        p for p in [
                            x.get("lesson"),
                            f"Nauczyciel: {x.get('teacher')}" if x.get("teacher") else None,
                        ] if p
                    ),
                )
            )

        return sorted(out, key=lambda e: e.start)
