from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta
from typing import Any, Callable

from librus_apix.client import new_client
from librus_apix.exceptions import AuthorizationError, TokenError

_LOGGER = logging.getLogger(__name__)


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
        """Authenticate with Librus using librus-apix OAuth/cookie flow."""
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
        """Run a blocking librus-apix function and re-authenticate once if needed."""
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
        """Fetch a non-critical module. One broken module must not break the integration."""
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
            # Each Librus login is treated as an independent HA device/config entry.
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
        """Return all ordinary and descriptive grades, preserving + / - / np / bz."""
        from librus_apix.grades import get_grades

        numeric, _averages, descriptive = await self._run(get_grades, "all")
        result: list[dict[str, Any]] = []

        for subject_group in numeric or []:
            for subject, grades in subject_group.items():
                for grade in grades:
                    result.append(self._grade_to_dict(grade, "numeric", subject))

        # Librus puts some non-numeric marks (+, -, np, bz etc.) here.
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

    async def get_messages(self, limit: int = 20) -> list[dict[str, Any]]:
        """Get message headers only. This does not open message contents."""
        from librus_apix.messages import get_received

        rows = await self._optional("messages", get_received, 0, default=[])
        return [
            {
                "author": x.author,
                "title": x.title,
                "date": x.date,
                "href": x.href,
                "unread": bool(x.unread),
                "has_attachment": bool(x.has_attachment),
            }
            for x in (rows or [])[:limit]
        ]

    async def get_attendance(self) -> dict[str, Any]:
        from librus_apix.attendance import get_attendance, get_attendance_frequency

        grouped = await self._optional("attendance", get_attendance, "all", default=[[], []])
        freq = await self._optional(
            "attendance frequency",
            get_attendance_frequency,
            default=(None, None, None),
        )

        records: list[dict[str, Any]] = []
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
            if value is None:
                return None
            return round(float(value) * 100, 2)

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

        # Current + next week, so HA calendar has useful forward data.
        mondays = [
            self._monday_for(date.today()),
            self._monday_for(date.today() + timedelta(days=7)),
        ]
        result: list[dict[str, Any]] = []

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
                            "subject": x.subject,
                            "teacher_and_classroom": x.teacher_and_classroom,
                            "date": x.date,
                            "date_from": x.date_from,
                            "date_to": x.date_to,
                            "weekday": x.weekday,
                            "info": x.info,
                            "number": x.number,
                        }
                    )

        # Deduplicate in case weeks overlap due to a parser/site quirk.
        seen = set()
        unique = []
        for row in result:
            key = (row["date"], row["date_from"], row["date_to"], row["subject"])
            if key not in seen:
                seen.add(key)
                unique.append(row)
        return unique

    async def get_schedule(self) -> list[dict[str, Any]]:
        from librus_apix.schedule import get_schedule

        today = date.today()
        months = {(today.year, today.month)}
        future = today + timedelta(days=45)
        months.add((future.year, future.month))

        result: list[dict[str, Any]] = []
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
                    result.append(
                        {
                            "date": f"{year:04d}-{month:02d}-{int(day_num):02d}",
                            "title": x.title,
                            "subject": x.subject,
                            "data": x.data,
                            "day": x.day,
                            "number": x.number,
                            "hour": x.hour,
                            "href": x.href,
                        }
                    )
        return result

    async def fetch_core(self) -> dict[str, Any]:
        """Fetch all supported modules.

        Student and grades are the core. Optional modules are isolated so that a school
        with one disabled Librus module still gets the rest of the integration.
        """
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
