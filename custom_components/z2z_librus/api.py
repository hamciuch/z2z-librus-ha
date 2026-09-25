from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import Counter
from datetime import date, datetime, timedelta
from typing import Any, Callable

from librus_apix.client import new_client
from librus_apix.exceptions import AuthorizationError, TokenError

from .const import GRADE_SYMBOLS

_LOGGER = logging.getLogger(__name__)

# Regular school grade 1-6 with an optional + / - (e.g. "5", "4+", "3-").
GRADE_NUMERIC_RE = re.compile(r"^[1-6][+-]?$")

TEST_KEYWORDS = ("kartkówka", "kartkowka", "klasówka", "klasowka")

# Technical entries that should not appear in the school-events calendar.
# Only title + subject are checked. We intentionally do NOT scan event.data,
# because valid events often contain metadata such as "Nauczyciel".
SCHEDULE_NOISE_KEYWORDS = (
    "nauczyciel:",
    "wakat",
    "zastępstwo",
    "zastepstwo",
    "nieobecność nauczyciela",
    "nieobecnosc nauczyciela",
    "na lekcji nr:",
    "na lekcji nr ",
)

# Entries that are never treated as noise, even if their text also contains a
# noise keyword (e.g. a parents' meeting listing "Nauczyciel: ..."). Checked
# against title, subject and the tooltip metadata (except the teacher name).
PROTECTED_KEYWORDS = (
    "zebranie",
    "wywiadówk",
    "wywiadowk",
    "rodzic",
    "konsultacj",
    "przypomnie",
)

# "nauczyciel:" alone is a weak noise signal (many real events name the
# teacher). When only this keyword matched, the event's detail page decides.
WEAK_NOISE_KEYWORDS = ("nauczyciel:",)

# Detail page ("Szczegóły") of a schedule entry: Data, Nr lekcji, Nauczyciel,
# Rodzaj, Przedmiot, Opis, Data dodania. Cached because it rarely changes.
DETAIL_TTL_SECONDS = 6 * 3600
DETAIL_FAIL_TTL_SECONDS = 30 * 60
DETAIL_MAX_FETCH = 25  # new detail pages fetched per refresh (spreads the load)
REMINDER_MARKER = "przypomnie"  # Rodzaj: Przypomnienie

# "Czas: 17:00 - 18:00" (or just "Czas: 17:00") line in a schedule cell.
EVENT_TIME_RE = re.compile(
    r"czas\s*:\s*(\d{1,2}):(\d{2})(?:\s*[-–—]\s*(\d{1,2}):(\d{2}))?",
    re.IGNORECASE,
)

# "Odwołane zajęcia" entries from the Librus schedule (terminarz), e.g.:
#   Odwołane zajęcia
#   Justyna Burzyńska na lekcji nr: 1 (Edukacja wczesnoszkolna)
# They are not shown as school events; instead they mark the matching
# timetable lessons as cancelled.
CANCELLATION_KEYWORDS = ("odwołane zajęcia", "odwolane zajecia")
CANCELLATION_TITLE_RE = re.compile(
    r"(?:(?P<teacher>[^\n(]+?)\s+)?na\s+lekcji\s+nr:?\s*(?P<number>\d+)"
    r"(?:\s*\((?P<subject>[^)]*)\))?",
    re.IGNORECASE,
)
# Text Librus itself may put in the timetable cell of a cancelled lesson.
TIMETABLE_CANCELLED_MARKERS = ("odwołan", "odwolan")

# Replying to messages (opt-in). The recipient list is cached because it hardly
# ever changes; a failed download is retried after a short pause.
RECIPIENTS_TTL_SECONDS = 6 * 3600
RECIPIENTS_FAIL_TTL_SECONDS = 30 * 60
# librus-apix' send_message() always reports failure (it reads .status_code from
# a BeautifulSoup object), so the server's own answer text is interpreted here.
SEND_FAILURE_RE = re.compile(
    r"nie\s+zosta|b[łl][ąa]d|niepoprawn|nie\s+mo[żz]na|nie\s+wybrano|brak\s+dost",
    re.IGNORECASE,
)
SEND_SUCCESS_RE = re.compile(r"wys[łl]an", re.IGNORECASE)


class LibrusError(Exception):
    """Base Librus exception."""


class LibrusAuthError(LibrusError):
    """Authentication failed."""


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\xa0", " ")).strip()


def classify_send_result(text: str) -> str | None:
    """'failed' / 'sent' from Librus' answer text, or None when it is unclear."""
    if not text:
        return None
    if SEND_FAILURE_RE.search(text):
        return "failed"
    if SEND_SUCCESS_RE.search(text):
        return "sent"
    return None


def _fetch_recipients_sync(client) -> list[dict[str, str]]:
    """All people the parent may write to: [{"id", "name", "group"}]."""
    from bs4 import BeautifulSoup
    from librus_apix.helpers import no_access_check

    soup = no_access_check(
        BeautifulSoup(client.get(client.RECIPIENT_GROUPS_URL).text, "lxml")
    )
    groups: list[str] = []
    for radio in soup.select("input.recipiantTypeRadio"):
        value = str(radio.attrs.get("value", "")).strip()
        if value and value not in groups:
            groups.append(value)

    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for group in groups:
        payload = {
            "typAdresata": group,
            "poprzednia": "5",
            "tabZaznaczonych": "",
            "czyWirtualneKlasy": False,
            "idGrupy": "0",
        }
        group_soup = no_access_check(
            BeautifulSoup(client.post(client.RECIPIENTS_URL, data=payload).text, "lxml")
        )
        for label in group_soup.select("label"):
            name = _clean_text(label.get_text(" "))
            rid = str(label.attrs.get("for", "_")).split("_")[-1].strip()
            if not name or not re.fullmatch(r"\w+", rid) or rid in seen:
                continue
            seen.add(rid)
            result.append({"id": rid, "name": name, "group": group})
    return result


def _sent_snapshot_sync(client) -> list[tuple[str, str]]:
    """(title, date) of the first page of the 'Wysłane' folder."""
    from librus_apix.messages import get_sent

    return [
        (_clean_text(x.title), _clean_text(x.date)) for x in get_sent(client, 0)
    ]


def _post_message_sync(client, recipient_id: str, title: str, content: str) -> str:
    """Same request as librus-apix' send_message(); returns the answer HTML."""
    payload = {
        "filtrUzytkownikow": "0",
        "idPojemnika": "",
        "DoKogo": [recipient_id],
        "Rodzaj": "0",
        "temat": title,
        "tresc": content,
        "poprzednia": "5",
        "fileStorageIdentifier": "",
        "wyslij": "Wyślij",
    }
    return client.post(client.SEND_MESSAGE_URL, data=payload).text


def _parse_send_result(html: str) -> tuple[str, bool]:
    """(text of div.container-background > p, session-expired flag)."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    heading = soup.select_one("h2.inside")
    if heading is not None and "Brak dostępu" in heading.get_text():
        return "", True
    paragraph = soup.select_one("div.container-background > p")
    return (_clean_text(paragraph.get_text(" ")) if paragraph else ""), False


class LibrusClient:
    """Async wrapper around librus-apix."""

    def __init__(self, username: str, password: str) -> None:
        self.username = username
        self.password = password
        self._client = None
        self._token = None
        self._auth_lock = asyncio.Lock()
        self._detail_cache: dict[str, tuple[float, dict[str, str]]] = {}
        self._detail_budget = DETAIL_MAX_FETCH
        self._recipients_cache: tuple[float, list[dict[str, str]]] | None = None

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
        value = str(getattr(grade, "grade", "") or "").strip()
        symbol = value.casefold()
        desc = str(getattr(grade, "desc", "") or "")
        href = str(getattr(grade, "href", "") or "")

        if GRADE_NUMERIC_RE.match(value):
            kind = "grade"
        elif symbol in ("+", "-"):
            kind = "plus" if symbol == "+" else "minus"
        elif value:
            kind = "symbol"
        else:
            kind = "empty"

        comment_match = re.search(r"Komentarz:\s*(.+)", desc, re.S)
        id_match = re.search(r"/szczegoly/(\d+)", href)
        weight = getattr(grade, "weight", None)
        counts = getattr(grade, "counts", None)

        return {
            "subject_name": str(getattr(grade, "subject", "") or subject_fallback or ""),
            "display_value": value,
            "date": str(getattr(grade, "date", "") or ""),
            "category_name": str(
                getattr(grade, "category", "")
                or desc
                or ""
            ).split("\n")[0],
            "teacher": str(getattr(grade, "teacher", "") or ""),
            "semester": getattr(grade, "semester", None),
            "type": grade_type,
            # 0.4.1: details librus-apix already parses but we used to drop.
            "id": id_match.group(1) if id_match else None,
            "kind": kind,
            "label": GRADE_SYMBOLS.get(symbol) if kind != "grade" else None,
            "weight": weight if isinstance(weight, int) and weight > 0 else None,
            # librus-apix reports False also when Librus does not show
            # "Licz do średniej" at all - treat that as unknown.
            "counts": (
                counts
                if isinstance(counts, bool) and "Licz do średniej" in desc
                else None
            ),
            "comment": comment_match.group(1).strip() if comment_match else None,
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

            if idx < 3 and x.href:
                body = await self._optional(
                    f"message content {x.href}",
                    message_content,
                    x.href,
                    default=None,
                )
                if body is not None:
                    item["content"] = getattr(body, "content", None)
                    item["author"] = getattr(body, "author", None) or item["author"]
                    item["title"] = (getattr(body, "title", None) or item["title"]).strip()
                    item["date"] = getattr(body, "date", None) or item["date"]

            result.append(item)

        return result

    async def get_recipients(self, force: bool = False) -> list[dict[str, str]]:
        """Recipients the parent may write to (cached)."""
        now = time.monotonic()
        cached = self._recipients_cache
        if cached and not force and cached[0] > now:
            return cached[1]

        rows = await self._optional("recipients", _fetch_recipients_sync, default=None)
        if rows is None:
            old = cached[1] if cached else []
            self._recipients_cache = (now + RECIPIENTS_FAIL_TTL_SECONDS, old)
            return old

        self._recipients_cache = (now + RECIPIENTS_TTL_SECONDS, rows)
        return rows

    async def send_message(
        self, recipient_id: str, title: str, content: str
    ) -> dict[str, Any]:
        """Send a message to one recipient.

        Returns {"status": "sent" | "failed" | "unknown", "message": str,
        "verified": bool | None}. The POST is sent exactly once - it is never
        retried automatically, because a retry could deliver it twice.
        """
        # Session check + snapshot of the "Wysłane" folder (safe to retry).
        before = await self._optional(
            "sent messages (before)", _sent_snapshot_sync, default=None
        )
        await self._ensure_login()

        try:
            html = await asyncio.to_thread(
                _post_message_sync, self._client, recipient_id, title, content
            )
        except (TokenError, AuthorizationError):
            self._client = None
            self._token = None
            return {
                "status": "failed",
                "message": "Sesja Librus wygasła - wiadomość nie została wysłana, spróbuj ponownie.",
                "verified": None,
            }
        except Exception as err:  # network error: it may or may not have arrived
            _LOGGER.warning("Librus send_message request failed: %s", err)
            return {
                "status": "unknown",
                "message": f"Błąd połączenia podczas wysyłania ({err}). Sprawdź folder Wysłane w Librusie.",
                "verified": None,
            }

        text, no_access = await asyncio.to_thread(_parse_send_result, html)
        if no_access:
            self._client = None
            self._token = None
            return {
                "status": "failed",
                "message": "Librus odmówił dostępu (sesja wygasła) - wiadomość nie została wysłana.",
                "verified": None,
            }

        verdict = classify_send_result(text)
        if verdict == "failed":
            return {"status": "failed", "message": text, "verified": None}
        if verdict == "sent":
            return {"status": "sent", "message": text, "verified": None}

        # Unclear answer: look for the message in the "Wysłane" folder.
        after = await self._optional(
            "sent messages (after)", _sent_snapshot_sync, default=None
        )
        if before is not None and after is not None:
            new_rows = Counter(after) - Counter(before)
            wanted = _clean_text(title).casefold()
            if any(row_title.casefold() == wanted for row_title, _ in new_rows):
                return {"status": "sent", "message": text, "verified": True}

        _LOGGER.warning("Librus send_message: unclear answer %r", text[:200])
        return {
            "status": "unknown",
            "message": (text or "Librus nie zwrócił czytelnej odpowiedzi")
            + " - nie udało się potwierdzić wysłania, sprawdź folder Wysłane.",
            "verified": False,
        }

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
    def _is_schedule_noise(event: Any) -> bool:
        """Drop technical entries, but preserve actual school events."""
        title = str(getattr(event, "title", "") or "").strip().lower()
        subject = str(getattr(event, "subject", "") or "").strip().lower()
        haystack = f"{title} {subject}"
        return any(keyword in haystack for keyword in SCHEDULE_NOISE_KEYWORDS)

    @staticmethod
    def _is_protected_event(event: Any) -> bool:
        """True for events that must never be dropped as noise (e.g. wywiadówka)."""
        parts = [
            str(getattr(event, "title", "") or ""),
            str(getattr(event, "subject", "") or ""),
        ]
        data = getattr(event, "data", None)
        if isinstance(data, dict):
            parts.extend(
                str(value)
                for key, value in data.items()
                if str(key).strip().lower() not in ("nauczyciel", "data dodania")
            )
        haystack = " ".join(parts).lower()
        return any(keyword in haystack for keyword in PROTECTED_KEYWORDS)

    @staticmethod
    def _parse_event_time(event: Any) -> tuple[str | None, str | None]:
        """Read 'Czas: HH:MM - HH:MM' from a schedule entry -> ('HH:MM', 'HH:MM'|None)."""
        parts = [
            str(getattr(event, "subject", "") or ""),
            str(getattr(event, "title", "") or ""),
        ]
        data = getattr(event, "data", None)
        if isinstance(data, dict):
            parts.extend(str(value) for value in data.values())

        for text in parts:
            match = EVENT_TIME_RE.search(text)
            if match is None:
                continue

            hour, minute = int(match.group(1)), int(match.group(2))
            if hour > 23 or minute > 59:
                continue

            time_to = None
            if match.group(3):
                end_hour, end_minute = int(match.group(3)), int(match.group(4))
                if end_hour <= 23 and end_minute <= 59:
                    time_to = f"{end_hour:02d}:{end_minute:02d}"

            return f"{hour:02d}:{minute:02d}", time_to

        return None, None

    @staticmethod
    def _pick_summary(title: str, subject: str) -> str:
        """Title for a non-test event, skipping 'Czas:' / 'Nauczyciel:' lines."""
        for text in (title, subject):
            text = (text or "").strip()
            if not text:
                continue
            if text.lower().startswith(("czas:", "nauczyciel:")):
                continue
            return text
        return "Wydarzenie szkolne"

    @staticmethod
    def _is_weak_noise(event: Any) -> bool:
        """True when the noise filter matched only on a weak keyword ('nauczyciel:')."""
        title = str(getattr(event, "title", "") or "").strip().lower()
        subject = str(getattr(event, "subject", "") or "").strip().lower()
        haystack = f"{title} {subject}"
        return not any(
            keyword in haystack
            for keyword in SCHEDULE_NOISE_KEYWORDS
            if keyword not in WEAK_NOISE_KEYWORDS
        )

    async def _detail_for(self, href: str) -> dict[str, str] | None:
        """Detail page ('Szczegóły') of a schedule entry, cached; None if unavailable."""
        if not href or "/" not in href:
            return None

        now = time.monotonic()
        cached = self._detail_cache.get(href)
        if cached and cached[0] > now:
            return cached[1] or None

        if self._detail_budget <= 0:
            return None
        self._detail_budget -= 1

        from librus_apix.schedule import schedule_detail

        prefix, suffix = href.split("/", 1)
        detail = await self._optional(
            f"schedule detail {href}",
            schedule_detail,
            prefix,
            suffix,
            default=None,
        )

        if isinstance(detail, dict) and detail:
            clean = {str(k).strip(): str(v).strip() for k, v in detail.items()}
            self._detail_cache[href] = (now + DETAIL_TTL_SECONDS, clean)
            return clean

        self._detail_cache[href] = (now + DETAIL_FAIL_TTL_SECONDS, {})
        return None

    @staticmethod
    def _apply_detail(event: dict[str, Any], detail: dict[str, str]) -> None:
        """Merge the detail page (Rodzaj, Przedmiot, Nr lekcji, Opis) into an event."""
        kind = detail.get("Rodzaj", "")
        subject = detail.get("Przedmiot", "")
        lesson = detail.get("Nr lekcji", "")
        description = "\n".join(
            re.sub(r"[ \t\xa0]+", " ", line).strip()
            for line in detail.get("Opis", "").splitlines()
            if line.strip()
        )

        event["detail"] = {
            key: detail[key]
            for key in ("Rodzaj", "Przedmiot", "Nr lekcji", "Nauczyciel", "Opis")
            if detail.get(key)
        }
        if description:
            event["description"] = description

        try:
            event["number"] = int(lesson)
        except (TypeError, ValueError):
            pass

        if REMINDER_MARKER in kind.lower():
            event["kind"] = kind
            event["title"] = f"{kind} – {subject}" if subject else kind

    async def _enrich_events(self, events: list[dict[str, Any]]) -> None:
        """Add detail-page data to non-test events (upcoming first)."""
        today = date.today().isoformat()
        candidates = [e for e in events if not e.get("is_test") and e.get("href")]
        candidates.sort(key=lambda e: (e["date"] < today, e["date"]))

        for event in candidates:
            detail = await self._detail_for(event["href"])
            if detail:
                self._apply_detail(event, detail)

    @staticmethod
    def _parse_cancellation(event: Any) -> dict[str, Any] | None:
        """Parse an 'Odwołane zajęcia ... na lekcji nr: N' schedule entry.

        Returns None when the entry is not a cancellation, or when it does not
        name a lesson number (such entries keep the previous behaviour).
        """
        title = str(getattr(event, "title", "") or "").strip()
        subject = str(getattr(event, "subject", "") or "").strip()
        text = f"{subject}\n{title}"
        lowered = text.lower()

        if not any(keyword in lowered for keyword in CANCELLATION_KEYWORDS):
            return None

        # Drop the "Odwołane zajęcia" label so that what is left is
        # "<teacher> na lekcji nr: N (<subject>)".
        detail = text
        for keyword in CANCELLATION_KEYWORDS:
            detail = re.sub(re.escape(keyword), " ", detail, flags=re.IGNORECASE)
        detail = re.sub(r"[ \t\xa0]+", " ", detail).strip()

        match = CANCELLATION_TITLE_RE.search(detail)
        if match is None:
            # Fall back to the metadata parsed from the tooltip.
            data = getattr(event, "data", None)
            extra = " ".join(str(v) for v in data.values()) if isinstance(data, dict) else ""
            match = CANCELLATION_TITLE_RE.search(extra)
        if match is None:
            return None

        return {
            "number": int(match.group("number")),
            "teacher": (match.group("teacher") or "").strip(),
            "subject": (match.group("subject") or "").strip(),
            "text": "Odwołane zajęcia",
        }

    @staticmethod
    def _mark_cancelled_lessons(
        timetable: list[dict[str, Any]],
        cancellations: list[dict[str, Any]],
    ) -> None:
        """Flag timetable lessons cancelled in the schedule (in place)."""
        cancelled = {(c["date"], c["number"]): c for c in cancellations}

        for lesson in timetable:
            reason = None

            try:
                number = int(lesson.get("number"))
            except (TypeError, ValueError):
                number = None

            if number is not None:
                match = cancelled.get((str(lesson.get("date") or ""), number))
                if match is not None:
                    reason = match["text"]

            # Librus may also label the lesson itself in the timetable cell.
            if reason is None:
                info = lesson.get("info")
                if isinstance(info, dict) and any(
                    marker in str(key).lower()
                    for key in info
                    for marker in TIMETABLE_CANCELLED_MARKERS
                ):
                    reason = "Odwołane"

            lesson["cancelled"] = reason is not None
            lesson["cancel_reason"] = reason

    @staticmethod
    def _is_test_event(event: Any) -> bool:
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

    async def get_schedule(
        self,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Return (school events, cancelled lessons) from the Librus schedule."""
        from librus_apix.schedule import get_schedule

        today = date.today()
        months = set()
        for offset in (0, 31, 62, 93):
            d = today + timedelta(days=offset)
            months.add((d.year, d.month))

        # New refresh: drop expired detail pages and reset the request budget.
        now_mono = time.monotonic()
        self._detail_cache = {
            href: entry
            for href, entry in self._detail_cache.items()
            if entry[0] > now_mono
        }
        self._detail_budget = DETAIL_MAX_FETCH

        result = []
        cancellations = []
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
                    cancellation = self._parse_cancellation(x)
                    if cancellation is not None:
                        cancellation["date"] = (
                            f"{year:04d}-{month:02d}-{int(day_num):02d}"
                        )
                        cancellations.append(cancellation)
                        continue

                    if not self._is_protected_event(x) and self._is_schedule_noise(x):
                        if not self._is_weak_noise(x):
                            continue

                        # Only "nauczyciel:" matched. Keep the entry when
                        # Librus itself classes it as a reminder (Przypomnienie).
                        detail = await self._detail_for(getattr(x, "href", ""))
                        if not (
                            detail
                            and REMINDER_MARKER in detail.get("Rodzaj", "").lower()
                        ):
                            continue

                    is_test = self._is_test_event(x)
                    kind = self._test_kind(x) if is_test else "Wydarzenie"
                    subject = (x.subject or "").strip()
                    title = (x.title or "").strip()
                    time_from, time_to = self._parse_event_time(x)

                    number = x.number
                    if time_from and not is_test:
                        # librus-apix reads the hour of "Czas: 17:00" as a
                        # lesson number (17); an explicit time wins.
                        number = None

                    if is_test:
                        summary = kind
                        if subject and "nauczyciel" not in subject.lower():
                            summary = f"{kind} – {subject}"
                        elif title and "nauczyciel" not in title.lower():
                            summary = f"{kind} – {title}"
                    else:
                        summary = self._pick_summary(title, subject)

                    result.append(
                        {
                            "date": f"{year:04d}-{month:02d}-{int(day_num):02d}",
                            "kind": kind,
                            "is_test": is_test,
                            "title": summary,
                            "raw_title": title,
                            "subject": subject,
                            "data": x.data,
                            "day": x.day,
                            "number": number,
                            "time_from": time_from,
                            "time_to": time_to,
                            "hour": x.hour,
                            "href": x.href,
                        }
                    )

        await self._enrich_events(result)

        result.sort(key=lambda x: (x["date"], str(x.get("hour") or ""), x["title"]))
        cancellations.sort(key=lambda c: (c["date"], c["number"]))
        return result, cancellations

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
        school_events, cancellations = schedule

        # Mark lessons cancelled in the schedule ("Odwołane zajęcia").
        self._mark_cancelled_lessons(timetable, cancellations)

        # Keep the existing "schedule" key test-only, but remove today's
        # tests immediately after their lesson has ended. The existing
        # NextEventEntity selects by date, so filtering here makes it advance
        # to the next real upcoming test instead of keeping a morning test
        # visible until midnight.
        now = datetime.now()
        tests = []

        for event in school_events:
            if not event.get("is_test"):
                continue

            try:
                event_date = datetime.fromisoformat(event["date"]).date()
            except Exception:
                continue

            if event_date < now.date():
                continue

            if event_date > now.date():
                tests.append(event)
                continue

            # Today's test: resolve its lesson end time from timetable.
            number = event.get("number")
            event_end = None

            try:
                number = int(number)
            except (TypeError, ValueError):
                number = None

            if number is not None:
                for lesson in timetable:
                    try:
                        lesson_number = int(lesson.get("number"))
                    except (TypeError, ValueError):
                        continue

                    if (
                        str(lesson.get("date") or "") == str(event.get("date") or "")
                        and lesson_number == number
                        and lesson.get("date_to")
                    ):
                        try:
                            event_end = datetime.fromisoformat(
                                f"{event['date']}T{lesson['date_to']}"
                            )
                        except Exception:
                            event_end = None
                        break

            # If the lesson time is known, keep the test only until it ends.
            # If timing cannot be resolved, keep it for today rather than
            # guessing and accidentally hiding a valid event.
            if event_end is None or event_end > now:
                tests.append(event)

        return {
            "me": {"Me": student},
            "grades": grades,
            "homework": homework,
            "messages": messages,
            "attendance": attendance,
            "timetable": timetable,
            "schedule": tests,
            "school_events": school_events,
            "cancelled_lessons": cancellations,
        }
