from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta
from typing import Any, Callable

from librus_apix.client import new_client
from librus_apix.exceptions import AuthorizationError, TokenError

_LOGGER = logging.getLogger(__name__)

TEST_KEYWORDS = ("kartkówka", "kartkowka", "klasówka", "klasowka")


class LibrusError(Exception):
    """Base Librus exception."""


class LibrusAuthError(LibrusError):
    """Authentication failed."""


class LibrusClient:
    """Async wrapper around librus-apix."""

    def __init__(self, username: str, password: str) -> None:
        self.username = username
        self.password = password
        self._client = None
        self._token = None
        self._auth_lock = asyncio.Lock()

    async def login(self) -> None:
        async with self._auth_lock:
            try:
                self._client = await asyncio.to_thread(new_client)
                self._token = await asyncio.to_thread(
                    self._client.get_token,
                    self.username,
                    self.password,
                )
            except AuthorizationError as err:
                self._client = None
                self._token = None
                raise LibrusAuthError(str(err)) from err
            except Exception as err:
                self._client = None
                self._token = None
                raise LibrusAuthError(f"Librus authentication failed: {err}") from err

        if not self._token:
            raise LibrusAuthError("Librus did not return an authentication token")

    async def _ensure_login(self) -> None:
        if self._client is None or self._token is None:
            await self.login()

    async def _run(self, func: Callable, *args):
        await self._ensure_login()
        for attempt in range(2):
            try:
                return await asyncio.to_thread(func, self._client, *args)
            except TokenError:
                self._client = None
                self._token = None
                if attempt == 0:
                    await self.login()
                    continue
                raise LibrusAuthError("Librus session expired")
            except AuthorizationError as err:
                self._client = None
                self._token = None
                raise LibrusAuthError(str(err)) from err

    async def _optional(self, name: str, func: Callable, *args, default=None):
        try:
            return await self._run(func, *args)
        except Exception as err:
            _LOGGER.warning("Unable to fetch Librus %s: %s", name, err)
            return default

    async def get_student(self) -> dict[str, Any]:
        from librus_apix.student_information import get_student_information

        info = await self._run(get_student_information)
        name = (info.name or "").strip()
        parts = name.split(" ", 1)
        return {
            "FirstName": parts[0] if parts else "",
            "LastName": parts[1] if len(parts) > 1 else "",
            "Name": name,
            "Class": info.class_name,
            "Number": info.number,
            "Tutor": info.tutor,
            "School": info.school,
            "LuckyNumber": info.lucky_number,
            "Login": self.username,
            "AccountId": self.username,
        }

    @staticmethod
    def _grade_to_dict(grade: Any, grade_type: str, subject_fallback: str = "") -> dict[str, Any]:
        return {
            "subject_name": str(getattr(grade, "subject", "") or subject_fallback or ""),
            "display_value": str(getattr(grade, "grade", "") or ""),
            "date": str(getattr(grade, "date", "") or ""),
            "category_name": str(
                getattr(grade, "category", "")
                or getattr(grade, "desc", "")
                or ""
            ).split("\n")[0],
            "teacher": str(getattr(grade, "teacher", "") or ""),
            "semester": getattr(grade, "semester", None),
            "type": grade_type,
        }

    async def get_grades(self) -> list[dict[str, Any]]:
        from librus_apix.grades import get_grades

        numeric, _averages, descriptive = await self._run(get_grades, "all")
        result: list[dict[str, Any]] = []

        for subject_group in numeric or []:
            for subject, grades in subject_group.items():
                for grade in grades:
                    result.append(self._grade_to_dict(grade, "numeric", subject))

        for subject_group in descriptive or []:
            for subject, grades in subject_group.items():
                for grade in grades:
                    result.append(self._grade_to_dict(grade, "descriptive", subject))

        return result

    async def get_homework(self) -> list[dict[str, Any]]:
        from librus_apix.homework import get_homework

        today = date.today()
        rows = await self._optional(
            "homework",
            get_homework,
            today.strftime("%Y-%m-%d"),
            (today + timedelta(days=90)).strftime("%Y-%m-%d"),
            default=[],
        )
        return [
            {
                "lesson": x.lesson,
                "teacher": x.teacher,
                "subject": x.subject,
                "category": x.category,
                "task_date": x.task_date,
                "completion_date": x.completion_date,
                "href": x.href,
            }
            for x in (rows or [])
        ]

    async def get_messages(self, limit: int = 10) -> list[dict[str, Any]]:
        """Get message list, including full content for the newest 3."""
        from librus_apix.messages import get_received, message_content

        rows = await self._optional("messages", get_received, 0, default=[])
        result = []

        for idx, x in enumerate((rows or [])[:limit]):
            item = {
                "author": x.author,
                "title": x.title.strip(),
                "date": x.date,
                "href": x.href,
                "unread": bool(x.unread),
                "has_attachment": bool(x.has_attachment),
                "content": None,
            }

            # Fetch complete body only for the three newest messages.
            if idx < 3 and x.href:
                body = await self._optional(
                    f"message content {x.href}",
                    message_content,
                    x.href,
                    default=None,
                )
                if body is not None:
                    item["content"] = getattr(body, "content", None)
                    # Prefer the detail values if available.
                    item["author"] = getattr(body, "author", None) or item["author"]
                    item["title"] = (getattr(body, "title", None) or item["title"]).strip()
                    item["date"] = getattr(body, "date", None) or item["date"]

            result.append(item)

        return result

    async def get_attendance(self) -> dict[str, Any]:
        from librus_apix.attendance import get_attendance, get_attendance_frequency

        grouped = await self._optional("attendance", get_attendance, "all", default=[[], []])
        freq = await self._optional(
            "attendance frequency",
            get_attendance_frequency,
            default=(None, None, None),
        )

        records = []
        for semester_rows in grouped or []:
            for x in semester_rows or []:
                records.append(
                    {
                        "symbol": x.symbol,
                        "semester": x.semester,
                        "date": x.date,
                        "type": x.type,
                        "teacher": x.teacher,
                        "period": x.period,
                        "excursion": x.excursion,
                        "topic": x.topic,
                        "subject": x.subject,
                    }
                )

        def pct(value):
            return None if value is None else round(float(value) * 100, 2)

        return {
            "records": records,
            "semester_1_percent": pct(freq[0]) if freq else None,
            "semester_2_percent": pct(freq[1]) if freq else None,
            "overall_percent": pct(freq[2]) if freq else None,
        }

    @staticmethod
    def _monday_for(day: date) -> datetime:
        monday = day - timedelta(days=day.weekday())
        return datetime.combine(monday, datetime.min.time())

    async def get_timetable(self) -> list[dict[str, Any]]:
        from librus_apix.timetable import get_timetable

        # Pull 4 weeks. This gives us a fuller subject list even before the first grade.
        mondays = [
            self._monday_for(date.today() + timedelta(days=7 * offset))
            for offset in range(4)
        ]

        result = []
        for monday in mondays:
            week = await self._optional(
                f"timetable {monday.date()}",
                get_timetable,
                monday,
                default=[],
            )
            for day_rows in week or []:
                for x in day_rows or []:
                    if not x.subject:
                        continue
                    result.append(
                        {
                            "subject": x.subject.strip(),
                            "teacher_and_classroom": x.teacher_and_classroom,
                            "date": x.date,
                            "date_from": x.date_from,
                            "date_to": x.date_to,
                            "weekday": x.weekday,
                            "info": x.info,
                            "number": x.number,
                        }
                    )

        seen = set()
        unique = []
        for row in result:
            key = (row["date"], row["date_from"], row["date_to"], row["subject"])
            if key not in seen:
                seen.add(key)
                unique.append(row)
        return unique

    @staticmethod
    def _is_test_event(event: Any) -> bool:
        """Keep only kartkówka / klasówka events; drop teacher/vacancy/substitution noise."""
        parts = [
            getattr(event, "title", ""),
            getattr(event, "subject", ""),
            str(getattr(event, "data", "") or ""),
        ]
        haystack = " ".join(str(x) for x in parts).lower()
        return any(keyword in haystack for keyword in TEST_KEYWORDS)

    @staticmethod
    def _test_kind(event: Any) -> str:
        text = " ".join(
            [
                str(getattr(event, "title", "") or ""),
                str(getattr(event, "subject", "") or ""),
                str(getattr(event, "data", "") or ""),
            ]
        ).lower()
        if "kartkówka" in text or "kartkowka" in text:
            return "Kartkówka"
        if "klasówka" in text or "klasowka" in text:
            return "Klasówka"
        return "Sprawdzian"

    async def get_schedule(self) -> list[dict[str, Any]]:
        """Return only kartkówki / klasówki from Librus schedule."""
        from librus_apix.schedule import get_schedule

        today = date.today()
        months = set()
        for offset in (0, 31, 62, 93):
            d = today + timedelta(days=offset)
            months.add((d.year, d.month))

        result = []
        for year, month in sorted(months):
            schedule = await self._optional(
                f"schedule {year}-{month:02d}",
                get_schedule,
                f"{month:02d}",
                str(year),
                False,
                default={},
            )
            for day_num, events in (schedule or {}).items():
                for x in events or []:
                    if not self._is_test_event(x):
                        continue

                    kind = self._test_kind(x)
                    subject = (x.subject or "").strip()
                    title = (x.title or "").strip()

                    # Avoid ugly "Nauczyciel: ..." as the HA event title.
                    summary = kind
                    if subject and "nauczyciel" not in subject.lower():
                        summary = f"{kind} – {subject}"
                    elif title and "nauczyciel" not in title.lower():
                        summary = f"{kind} – {title}"

                    result.append(
                        {
                            "date": f"{year:04d}-{month:02d}-{int(day_num):02d}",
                            "kind": kind,
                            "title": summary,
                            "raw_title": title,
                            "subject": subject,
                            "data": x.data,
                            "day": x.day,
                            "number": x.number,
                            "hour": x.hour,
                            "href": x.href,
                        }
                    )

        result.sort(key=lambda x: x["date"])
        return result

    async def fetch_core(self) -> dict[str, Any]:
        student = await self.get_student()

        grades, homework, messages, attendance, timetable, schedule = await asyncio.gather(
            self.get_grades(),
            self.get_homework(),
            self.get_messages(),
            self.get_attendance(),
            self.get_timetable(),
            self.get_schedule(),
        )

        return {
            "me": {"Me": student},
            "grades": grades,
            "homework": homework,
            "messages": messages,
            "attendance": attendance,
            "timetable": timetable,
            "schedule": schedule,
        }
