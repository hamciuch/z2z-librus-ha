from __future__ import annotations

import asyncio
from dataclasses import asdict, is_dataclass
from datetime import date, timedelta
from typing import Any

from librus_apix.client import new_client
from librus_apix.exceptions import AuthorizationError, TokenError


class LibrusError(Exception):
    """Base Librus exception."""


class LibrusAuthError(LibrusError):
    """Authentication failed."""


class LibrusClient:
    """Async wrapper around the synchronous librus-apix client."""

    def __init__(self, username: str, password: str) -> None:
        self.username = username
        self.password = password
        self._client = None
        self._token = None
        self._auth_lock = asyncio.Lock()

    async def login(self) -> None:
        """Authenticate using the same flow as librus-apix."""
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

    async def _run(self, func, *args):
        """Run a librus-apix blocking function in a worker thread, reauth once."""
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

    async def get_student(self) -> dict[str, Any]:
        from librus_apix.student_information import get_student_information

        info = await self._run(get_student_information)
        return {
            "FirstName": info.name.split(" ", 1)[0] if info.name else "",
            "LastName": info.name.split(" ", 1)[1] if info.name and " " in info.name else "",
            "Name": info.name,
            "Class": info.class_name,
            "Number": info.number,
            "Tutor": info.tutor,
            "School": info.school,
            "LuckyNumber": info.lucky_number,
            "Login": self.username,
            "AccountId": self.username,
        }

    @staticmethod
    def _grade_to_dict(grade: Any, grade_type: str) -> dict[str, Any]:
        return {
            "subject_name": getattr(grade, "subject", "") or "",
            "display_value": str(getattr(grade, "grade", "") or ""),
            "date": str(getattr(grade, "date", "") or ""),
            "category_name": str(getattr(grade, "category", "") or ""),
            "teacher": str(getattr(grade, "teacher", "") or ""),
            "semester": getattr(grade, "semester", None),
            "type": grade_type,
        }

    async def get_grades(self) -> list[dict[str, Any]]:
        from librus_apix.grades import get_grades

        numeric, averages, descriptive = await self._run(get_grades, "all")

        result: list[dict[str, Any]] = []

        for subject_group in numeric or []:
            for subject, grades in subject_group.items():
                for grade in grades:
                    row = self._grade_to_dict(grade, "numeric")
                    if not row["subject_name"]:
                        row["subject_name"] = subject
                    result.append(row)

        # Descriptive grades can also contain normal values such as +, -, 5+, np, bz.
        for subject_group in descriptive or []:
            for subject, grades in subject_group.items():
                for grade in grades:
                    row = self._grade_to_dict(grade, "descriptive")
                    if not row["subject_name"]:
                        row["subject_name"] = subject
                    result.append(row)

        return result

    async def get_homework(self) -> list[dict[str, Any]]:
        from librus_apix.homework import get_homework

        today = date.today()
        rows = await self._run(
            get_homework,
            today.strftime("%Y-%m-%d"),
            (today + timedelta(days=30)).strftime("%Y-%m-%d"),
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

    async def fetch_core(self) -> dict[str, Any]:
        student = await self.get_student()
        grades = await self.get_grades()

        try:
            homework = await self.get_homework()
        except Exception:
            homework = []

        return {
            "me": {"Me": student},
            "grades": grades,
            "grades_raw": {},
            "homework_raw": {"HomeWorkAssignments": homework},
            "attendances_raw": {},
        }

    async def enrich_grades(self, raw: dict[str, Any]) -> list[dict[str, Any]]:
        # Kept for compatibility with the existing coordinator.
        return await self.get_grades()
