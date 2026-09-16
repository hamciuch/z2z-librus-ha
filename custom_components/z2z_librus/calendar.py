from __future__ import annotations

from datetime import date, datetime, time, timedelta
import re

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_LUNCH_ENABLED,
    CONF_LUNCH_TIME,
    DEFAULT_LUNCH_ENABLED,
    DEFAULT_LUNCH_TIME,
    DOMAIN,
    LUNCH_DURATION_MINUTES,
)


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


def _clean_teacher_room(value: str | None) -> str:
    """Keep only useful teacher/room text and remove repeated whitespace."""
    if not value:
        return ""
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text


def _lesson_title(subject, number) -> str:
    """Return calendar title like '1. religia'."""
    subject = str(subject or "Lekcja").strip()
    try:
        number = int(number)
        return f"{number}. {subject}"
    except (TypeError, ValueError):
        return subject


def _test_description(details) -> str:
    """Extract only the scope/description of a test."""
    if isinstance(details, dict):
        return str(details.get("Opis") or details.get("opis") or "").strip()
    if details:
        text = str(details)
        match = re.search(
            r"['\"]Opis['\"]\s*:\s*['\"](.+?)['\"](?:,\s*['\"]Data dodania|,\s*['\"]Nauczyciel|}$)",
            text,
        )
        if match:
            return match.group(1).strip()
        return text.strip()
    return ""


def _parse_lunch_time(value) -> time:
    """Accept HA time-selector values such as 11:45 or 11:45:00."""
    if isinstance(value, time):
        return value.replace(second=0, microsecond=0)

    text = str(value or DEFAULT_LUNCH_TIME).strip()
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            parsed = datetime.strptime(text, fmt).time()
            return parsed.replace(second=0, microsecond=0)
        except ValueError:
            pass

    return time(11, 45)


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

            description = _test_description(x.get("data"))

            out.append(
                CalendarEvent(
                    summary=x.get("title") or x.get("kind") or "Kartkówka / klasówka",
                    start=d,
                    end=d + timedelta(days=1),
                    description=description,
                )
            )
        return sorted(out, key=lambda e: e.start)


class TimetableCalendar(BaseCalendar):
    _attr_name = "Plan lekcji"
    _attr_icon = "mdi:calendar-clock"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "timetable")

    def _lunch_events(self, start, end, tz):
        if not self.entry.options.get(CONF_LUNCH_ENABLED, DEFAULT_LUNCH_ENABLED):
            return []

        lunch_time = _parse_lunch_time(
            self.entry.options.get(CONF_LUNCH_TIME, DEFAULT_LUNCH_TIME)
        )

        first_day = start.date() if isinstance(start, datetime) else start
        last_day = end.date() if isinstance(end, datetime) else end

        out = []
        day = first_day

        while day <= last_day:
            # School lunch: Monday-Friday.
            if day.weekday() < 5:
                dt_start = datetime.combine(day, lunch_time).replace(tzinfo=tz)
                dt_end = dt_start + timedelta(minutes=LUNCH_DURATION_MINUTES)

                out.append(
                    CalendarEvent(
                        summary="🍽️ Obiad",
                        start=dt_start,
                        end=dt_end,
                    )
                )

            day += timedelta(days=1)

        return out

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

            description = _clean_teacher_room(x.get("teacher_and_classroom"))

            out.append(
                CalendarEvent(
                    summary=_lesson_title(x.get("subject"), x.get("number")),
                    start=dt_start,
                    end=dt_end,
                    description=description,
                )
            )

        out.extend(self._lunch_events(start, end, tz))
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
                    description=str(x.get("lesson") or "").strip(),
                )
            )

        return sorted(out, key=lambda e: e.start)
