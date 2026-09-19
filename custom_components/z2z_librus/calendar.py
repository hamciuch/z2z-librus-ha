from __future__ import annotations

from datetime import date, datetime, time, timedelta
import re

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

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


# School events with an explicit "Czas: HH:MM - HH:MM" shorter than this are
# shown at that time (e.g. a parents' meeting); longer ones (trips) stay all-day.
ALL_DAY_MIN_HOURS = 6
# Length used when only a start time is given.
DEFAULT_EVENT_MINUTES = 60


def _sort_key(event):
    """Sort key that can compare all-day (date) and timed (datetime) events."""
    start = event.start
    if isinstance(start, datetime):
        return start if start.tzinfo else start.astimezone()
    return datetime.combine(start, time.min).astimezone()


def _strike(text: str) -> str:
    """Strike text through using U+0336 (works in any plain-text calendar UI)."""
    return "".join(f"{char}̶" for char in str(text))


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
    _attr_name = "Wydarzenia szkolne"
    _attr_icon = "mdi:calendar-star"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "agenda")

    def _lesson_times_for_event(self, event):
        event_date = str(event.get("date") or "")
        number = event.get("number")

        if not event_date or number in (None, "", 0):
            return None

        try:
            number = int(number)
        except (TypeError, ValueError):
            return None

        for lesson in self.coordinator.data.get("timetable", []):
            try:
                lesson_number = int(lesson.get("number"))
            except (TypeError, ValueError):
                continue

            if (
                str(lesson.get("date") or "") == event_date
                and lesson_number == number
                and lesson.get("date_from")
                and lesson.get("date_to")
            ):
                return lesson.get("date_from"), lesson.get("date_to")

        return None

    @staticmethod
    def _explicit_times(event, tz):
        """Start/end from an explicit 'Czas: HH:MM - HH:MM', or None.

        None for tests (they follow the lesson), for missing/invalid times and
        for events lasting ALL_DAY_MIN_HOURS or more (trips stay all-day).
        """
        if event.get("is_test"):
            return None

        time_from = event.get("time_from")
        if not time_from:
            return None

        try:
            dt_start = datetime.fromisoformat(
                f"{event['date']}T{time_from}"
            ).replace(tzinfo=tz)
            time_to = event.get("time_to")
            if time_to:
                dt_end = datetime.fromisoformat(
                    f"{event['date']}T{time_to}"
                ).replace(tzinfo=tz)
            else:
                dt_end = dt_start + timedelta(minutes=DEFAULT_EVENT_MINUTES)
        except Exception:
            return None

        if dt_end <= dt_start:
            return None
        if dt_end - dt_start >= timedelta(hours=ALL_DAY_MIN_HOURS):
            return None

        return dt_start, dt_end

    def _events(self, start, end):
        out = []
        tz = datetime.now().astimezone().tzinfo

        for x in self.coordinator.data.get("school_events", []):
            try:
                d = datetime.fromisoformat(x["date"]).date()
            except Exception:
                continue

            if not _in_range(d, start, end):
                continue

            description = _test_description(x.get("data"))

            explicit = self._explicit_times(x, tz)
            if explicit:
                out.append(
                    CalendarEvent(
                        summary=x.get("title") or x.get("kind") or "Wydarzenie szkolne",
                        start=explicit[0],
                        end=explicit[1],
                        description=description,
                    )
                )
                continue

            lesson_times = self._lesson_times_for_event(x)

            if lesson_times:
                date_from, date_to = lesson_times
                try:
                    dt_start = datetime.fromisoformat(
                        f"{x['date']}T{date_from}"
                    ).replace(tzinfo=tz)
                    dt_end = datetime.fromisoformat(
                        f"{x['date']}T{date_to}"
                    ).replace(tzinfo=tz)

                    out.append(
                        CalendarEvent(
                            summary=x.get("title") or x.get("kind") or "Wydarzenie szkolne",
                            start=dt_start,
                            end=dt_end,
                            description=description,
                        )
                    )
                    continue
                except Exception:
                    pass

            # No reliable hour/lesson number -> keep as all-day.
            out.append(
                CalendarEvent(
                    summary=x.get("title") or x.get("kind") or "Wydarzenie szkolne",
                    start=d,
                    end=d + timedelta(days=1),
                    description=description,
                )
            )

        return sorted(out, key=_sort_key)


class TimetableCalendar(BaseCalendar):
    _attr_name = "Plan lekcji"
    _attr_icon = "mdi:calendar-clock"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "timetable")

    def _lunch_events(self, start, end, tz):
        if not self.entry.options.get(CONF_LUNCH_ENABLED, DEFAULT_LUNCH_ENABLED):
            return []

        lunch_options = {
            0: CONF_LUNCH_TIME_MONDAY,
            1: CONF_LUNCH_TIME_TUESDAY,
            2: CONF_LUNCH_TIME_WEDNESDAY,
            3: CONF_LUNCH_TIME_THURSDAY,
            4: CONF_LUNCH_TIME_FRIDAY,
        }

        first_day = start.date() if isinstance(start, datetime) else start
        last_day = end.date() if isinstance(end, datetime) else end

        out = []
        day = first_day

        while day <= last_day:
            option_key = lunch_options.get(day.weekday())

            if option_key is not None:
                lunch_time = _parse_lunch_time(
                    self.entry.options.get(option_key, DEFAULT_LUNCH_TIME)
                )
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

    @property
    def event(self):
        """Current/next event; cancelled lessons never count as an event."""
        now = datetime.now().astimezone()
        events = self._events(now, now + timedelta(days=120), include_cancelled=False)
        return events[0] if events else None

    def _events(self, start, end, include_cancelled=True):
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

            summary = _lesson_title(x.get("subject"), x.get("number"))
            description = _clean_teacher_room(x.get("teacher_and_classroom"))

            if x.get("cancelled"):
                if not include_cancelled:
                    continue
                # Cancelled lesson: strike the title through and say why.
                summary = _strike(summary)
                reason = x.get("cancel_reason") or "Odwołane"
                description = f"{reason} · {description}" if description else reason

            out.append(
                CalendarEvent(
                    summary=summary,
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
