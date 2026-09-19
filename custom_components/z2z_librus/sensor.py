from __future__ import annotations

from collections import Counter
from datetime import datetime

from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


def _me(data):
    m = data.get("me", {})
    return m.get("Me", m) if isinstance(m, dict) else {}


def _student_name(data):
    m = _me(data)
    return (
        m.get("Name")
        or " ".join(filter(None, [m.get("FirstName"), m.get("LastName")]))
        or str(m.get("Login") or "Librus")
    )


def _ident(data, fallback):
    m = _me(data)
    return str(m.get("AccountId") or m.get("Login") or fallback)


def _all_subjects(data):
    """Subjects from both grades and timetable, so zero-grade subjects also exist."""
    subjects = set()
    for g in data.get("grades", []):
        if g.get("subject_name"):
            subjects.add(g["subject_name"].strip())
    for lesson in data.get("timetable", []):
        if lesson.get("subject"):
            # Multiple subjects can occasionally be joined with " / ".
            for subject in lesson["subject"].split(" / "):
                subject = subject.strip()
                if subject:
                    subjects.add(subject)
    return sorted(subjects, key=str.casefold)


async def async_setup_entry(hass, entry, async_add_entities):
    c = hass.data[DOMAIN][entry.entry_id]

    entities = [
        StudentInfoEntity(c, entry),
        LuckyNumberEntity(c, entry),
        GradesEntity(c, entry),
        AttendanceEntity(c, entry),
        HomeworkEntity(c, entry),
        MessagesEntity(c, entry),
        RecentMessageEntity(c, entry, 0),
        RecentMessageEntity(c, entry, 1),
        RecentMessageEntity(c, entry, 2),
        NextLessonEntity(c, entry),
        NextEventEntity(c, entry),
    ]

    entities += [
        SubjectGradesEntity(c, entry, subject)
        for subject in _all_subjects(c.data)
    ]

    async_add_entities(entities)


class Base(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, entry):
        super().__init__(coordinator)
        self.entry = entry

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


class StudentInfoEntity(Base):
    _attr_name = "Uczeń"
    _attr_icon = "mdi:account-school"

    @property
    def unique_id(self):
        return f"{_ident(self.coordinator.data, self.entry.entry_id)}_student"

    @property
    def native_value(self):
        return _student_name(self.coordinator.data)

    @property
    def extra_state_attributes(self):
        m = _me(self.coordinator.data)
        return {
            "class": m.get("Class"),
            "number": m.get("Number"),
            "tutor": m.get("Tutor"),
            "school": m.get("School"),
            "login": m.get("Login"),
        }


class LuckyNumberEntity(Base):
    _attr_name = "Szczęśliwy numerek"
    _attr_icon = "mdi:clover"

    @property
    def unique_id(self):
        return f"{_ident(self.coordinator.data, self.entry.entry_id)}_lucky_number"

    @property
    def native_value(self):
        return _me(self.coordinator.data).get("LuckyNumber")


class GradesEntity(Base):
    _attr_name = "Wszystkie oceny"
    _attr_icon = "mdi:format-list-numbered"

    @property
    def unique_id(self):
        return f"{_ident(self.coordinator.data, self.entry.entry_id)}_grades"

    @property
    def native_value(self):
        return len(self.coordinator.data.get("grades", []))

    @property
    def extra_state_attributes(self):
        grades = self.coordinator.data.get("grades", [])
        values = [g.get("display_value") for g in grades]
        counts = Counter(values)
        return {
            "student": _student_name(self.coordinator.data),
            "grades": grades[-250:],
            "values": values,
            "plus_count": counts.get("+", 0),
            "minus_count": counts.get("-", 0),
            "np_count": counts.get("np", 0),
            "bz_count": counts.get("bz", 0),
        }


class SubjectGradesEntity(Base):
    _attr_icon = "mdi:book-education"

    def __init__(self, coordinator, entry, subject):
        super().__init__(coordinator, entry)
        self.subject = subject
        self._attr_name = f"Oceny – {subject}"

    @property
    def unique_id(self):
        return f"{_ident(self.coordinator.data, self.entry.entry_id)}_subject_{self.subject}"

    def rows(self):
        return [
            g for g in self.coordinator.data.get("grades", [])
            if g.get("subject_name") == self.subject
        ]

    @property
    def native_value(self):
        rows = self.rows()
        return ", ".join(g.get("display_value", "") for g in rows) if rows else "brak"

    @property
    def extra_state_attributes(self):
        rows = self.rows()
        return {
            "subject": self.subject,
            "grade_count": len(rows),
            "grades": rows[-150:],
            "values": [g.get("display_value") for g in rows],
        }


class AttendanceEntity(Base):
    _attr_name = "Frekwencja"
    _attr_icon = "mdi:account-check"

    @property
    def unique_id(self):
        return f"{_ident(self.coordinator.data, self.entry.entry_id)}_attendance"

    @property
    def native_value(self):
        return self.coordinator.data.get("attendance", {}).get("overall_percent")

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data.get("attendance", {})
        return {
            "semester_1_percent": data.get("semester_1_percent"),
            "semester_2_percent": data.get("semester_2_percent"),
            "records": data.get("records", [])[-250:],
        }


class HomeworkEntity(Base):
    _attr_name = "Zadania domowe"
    _attr_icon = "mdi:book-open-page-variant"

    @property
    def unique_id(self):
        return f"{_ident(self.coordinator.data, self.entry.entry_id)}_homework"

    @property
    def native_value(self):
        return len(self.coordinator.data.get("homework", []))

    @property
    def extra_state_attributes(self):
        return {"homework": self.coordinator.data.get("homework", [])[:100]}


class MessagesEntity(Base):
    _attr_name = "Wiadomości"
    _attr_icon = "mdi:email"

    @property
    def unique_id(self):
        return f"{_ident(self.coordinator.data, self.entry.entry_id)}_messages"

    @property
    def native_value(self):
        return len(self.coordinator.data.get("messages", []))

    @property
    def extra_state_attributes(self):
        messages = self.coordinator.data.get("messages", [])
        return {
            "total_loaded": len(messages),
            "latest_href": messages[0].get("href") if messages else None,
            "latest_title": messages[0].get("title") if messages else None,
            # Only expose the newest three to keep state attributes compact.
            "messages": messages[:3],
        }


class RecentMessageEntity(Base):
    _attr_icon = "mdi:email-open-outline"

    def __init__(self, coordinator, entry, index):
        super().__init__(coordinator, entry)
        self.index = index
        self._attr_name = f"Wiadomość {index + 1}"

    @property
    def unique_id(self):
        return f"{_ident(self.coordinator.data, self.entry.entry_id)}_message_{self.index + 1}"

    def _msg(self):
        rows = self.coordinator.data.get("messages", [])
        return rows[self.index] if len(rows) > self.index else None

    @property
    def native_value(self):
        msg = self._msg()
        return msg.get("title") if msg else "brak"

    @property
    def extra_state_attributes(self):
        msg = self._msg()
        if not msg:
            return {}
        return {
            "author": msg.get("author"),
            "date": msg.get("date"),
            "content": msg.get("content"),
            "message_id": msg.get("href"),
            "has_attachment": msg.get("has_attachment"),
        }


class NextLessonEntity(Base):
    _attr_name = "Następna lekcja"
    _attr_icon = "mdi:calendar-clock"

    @property
    def unique_id(self):
        return f"{_ident(self.coordinator.data, self.entry.entry_id)}_next_lesson"

    def _next(self):
        now = datetime.now()
        candidates = []
        for x in self.coordinator.data.get("timetable", []):
            if x.get("cancelled"):
                continue
            try:
                start = datetime.fromisoformat(f"{x['date']}T{x['date_from']}")
            except Exception:
                continue
            if start >= now:
                candidates.append((start, x))
        return min(candidates, key=lambda t: t[0]) if candidates else None

    @property
    def native_value(self):
        row = self._next()
        return row[1].get("subject") if row else "brak"

    @property
    def extra_state_attributes(self):
        row = self._next()
        if not row:
            return {}
        start, x = row
        return {
            "start": start.isoformat(),
            "end": f"{x.get('date')}T{x.get('date_to')}",
            "teacher_and_classroom": x.get("teacher_and_classroom"),
            "lesson_number": x.get("number"),
            "info": x.get("info"),
        }


class NextEventEntity(Base):
    _attr_name = "Najbliższa kartkówka lub klasówka"
    _attr_icon = "mdi:clipboard-text-clock"

    @property
    def unique_id(self):
        return f"{_ident(self.coordinator.data, self.entry.entry_id)}_next_test"

    def _next(self):
        today = datetime.now().date()
        candidates = []
        for x in self.coordinator.data.get("schedule", []):
            try:
                d = datetime.fromisoformat(x["date"]).date()
            except Exception:
                continue
            if d >= today:
                candidates.append((d, x))
        return min(candidates, key=lambda t: t[0]) if candidates else None

    @property
    def native_value(self):
        row = self._next()
        return row[1].get("title") if row else "brak"

    @property
    def extra_state_attributes(self):
        row = self._next()
        if not row:
            return {}
        d, x = row
        return {
            "date": d.isoformat(),
            "kind": x.get("kind"),
            "subject": x.get("subject"),
            "lesson_number": x.get("number"),
            "hour": x.get("hour"),
            "details": x.get("data"),
        }
