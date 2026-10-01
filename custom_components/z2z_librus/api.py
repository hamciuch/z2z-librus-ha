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
FUTURE_WEEK_TTL_SECONDS = 2 * 3600
PAGE_GRADES_TTL_SECONDS = 6 * 3600
SLOW_DATA_TTL_SECONDS = 3600  # student info, homework, attendance
FAR_MONTH_TTL_SECONDS = 2 * 3600  # schedule months beyond FRESH_SCHEDULE_DAYS
FRESH_SCHEDULE_DAYS = 14
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


# --- Uwagi (notes), 0.6.0 -------------------------------------------------
# librus-apix has no notes module. The gateway API (same session as
# attendance) gives structured data incl. positive/negative; the /uwagi page
# is the fallback when the gateway is unavailable.
NOTES_URL = "/uwagi"
GATEWAY_NOTES = "/gateway/api/2.0/Notes"
NOTE_TYPES = {1: "pozytywna", 0: "negatywna"}
NOTE_TYPE_WORDS = (
    ("pozytyw", "pozytywna"),
    ("pochwał", "pozytywna"),
    ("pochwal", "pozytywna"),
    ("negatyw", "negatywna"),
    ("nagan", "negatywna"),
)


def _note_type_from_flag(value: Any) -> str | None:
    """Librus API 'Positive' flag: 1/true = pozytywna, 0/false = negatywna."""
    if isinstance(value, bool):
        return "pozytywna" if value else "negatywna"
    text = str(value).strip().casefold()
    if text in ("1", "true", "tak"):
        return "pozytywna"
    if text in ("0", "false", "nie"):
        return "negatywna"
    return None


def _raw(value: Any) -> Any:
    """Compact copy of an API object for diagnostics (no long texts, no URLs)."""
    if isinstance(value, dict):
        return {k: _raw(v) for k, v in value.items() if k not in ("Text", "Url")}
    if isinstance(value, list):
        return [_raw(v) for v in value[:5]]
    return value


def _note_type_from_text(*texts: str) -> str:
    joined = " ".join(texts).casefold()
    for word, kind in NOTE_TYPE_WORDS:
        if word in joined:
            return kind
    return "neutralna"


def _gateway_json(client, path: str) -> dict[str, Any]:
    response = client.get(client.BASE_URL + path)
    response.raise_for_status()
    return response.json()


def _gateway_rows(client, path: str) -> list[dict[str, Any]]:
    """The list from a gateway answer; raises when there is none.

    An expired token can come back as 200 with an error body - that must
    not look like "no grades" (everything would vanish).
    """
    data = _gateway_json(client, path)
    rows = next((v for v in data.values() if isinstance(v, list)), None) if isinstance(data, dict) else None
    if rows is None:
        raise ValueError(f"Unexpected API answer for {path}: {str(data)[:120]}")
    return rows


def grade_content_key(g: dict[str, Any]) -> str:
    """Same grade from the API and from the grades page gives the same key."""
    return "|".join(
        (
            str(g.get("subject_name") or "").strip().casefold(),
            str(g.get("date") or "")[:10],
            str(g.get("display_value") or "").strip().casefold(),
        )
    )


# Dictionaries (subjects, categories, teachers) hardly ever change: keep them
# for a few hours instead of asking Librus on every refresh (0.7.0).
API_DICT_TTL_SECONDS = 6 * 3600
ApiCache = dict[str, tuple[float, Any]]


def _gateway_cached(client, path: str, cache: ApiCache | None) -> dict[str, Any]:
    now = time.monotonic()
    if cache is not None:
        hit = cache.get(path)
        if hit and hit[0] > now:
            return hit[1]
    data = _gateway_json(client, path)
    if cache is not None:
        cache[path] = (now + API_DICT_TTL_SECONDS, data)
    return data


def _fetch_notes_gateway_sync(client, cache: ApiCache | None = None) -> list[dict[str, Any]]:
    """Notes from the API. The caller refreshes the OAuth token first."""
    notes = _gateway_rows(client, GATEWAY_NOTES)

    categories: dict[str, str] = {}
    category_types: dict[str, dict[str, Any]] = {}
    try:
        for c in _gateway_cached(client, GATEWAY_NOTES + "/Categories", cache).get("Categories") or []:
            categories[str(c.get("Id"))] = _clean_text(c.get("Name") or c.get("CategoryName"))
            category_types[str(c.get("Id"))] = c
    except Exception as err:  # names are a nicety
        _LOGGER.debug("Notes categories unavailable: %s", err)

    result = []
    for n in notes:
        teacher_id = str((n.get("Teacher") or {}).get("Id") or "")
        category_id = str((n.get("Category") or {}).get("Id"))
        category = categories.get(category_id, "")
        kind = (
            _note_type_from_flag(n.get("Positive"))
            or _note_type_from_flag((category_types.get(category_id) or {}).get("Positive"))
            or _note_type_from_text(category)
        )
        result.append(
            {
                "id": str(n.get("Id") or "") or None,
                "date": str(n.get("Date") or "")[:10],
                "added": str(n.get("AddDate") or ""),
                "text": _clean_text(n.get("Text")),
                "category": category,
                "teacher": _gateway_user_name(client, teacher_id, cache),
                "type": kind,
                "positive": kind == "pozytywna",
                "negative": kind == "negatywna",
                "source": "api",
                # Diagnostics (0.6.1): API fields without the note text.
                "raw": {
                    "note": _raw(n),
                    "category": _raw(category_types.get(category_id)),
                },
            }
        )
    return result


def _fetch_notes_html_sync(client) -> list[dict[str, Any]]:
    """Parse the /uwagi page; columns are matched by their header text."""
    from bs4 import BeautifulSoup
    from librus_apix.helpers import no_access_check

    soup = no_access_check(
        BeautifulSoup(client.get(client.BASE_URL + NOTES_URL).text, "lxml")
    )
    fields = (
        ("treść", "text"), ("tresc", "text"), ("kategoria", "category"),
        ("data", "date"), ("nauczyciel", "teacher"), ("dodał", "teacher"),
        ("typ", "kind"), ("rodzaj", "kind"),
    )
    result = []
    for table in soup.select("table"):
        header = table.select_one("thead tr") or table.select_one("tr")
        if header is None:
            continue
        columns: dict[int, str] = {}
        for idx, cell in enumerate(header.find_all(["th", "td"])):
            label = _clean_text(cell.get_text(" ")).casefold()
            for word, field in fields:
                if label.startswith(word) and field not in columns.values():
                    columns[idx] = field
                    break
        if "text" not in columns.values():
            continue
        headers = [
            _clean_text(c.get_text(" ")) for c in header.find_all(["th", "td"])
        ]
        body_rows = table.select("tbody tr") or table.find_all("tr")[1:]
        for row in body_rows:
            cells = row.find_all("td")
            if len(cells) < 2:
                continue
            item = {f: _clean_text(cells[i].get_text(" ")) for i, f in columns.items() if i < len(cells)}
            if not item.get("text"):
                continue
            markers = " ".join(
                [" ".join(row.get("class", [])), _clean_text(row.get("title", ""))]
                + [
                    " ".join(el.get("class", [])) + " " + _clean_text(el.get("title", ""))
                    + " " + _clean_text(el.get("alt", ""))
                    for el in row.find_all(True)
                ]
            )
            kind = _note_type_from_text(
                item.get("kind", ""), item.get("category", ""), markers,
            )
            date_match = re.search(r"\d{4}-\d{2}-\d{2}", item.get("date", ""))
            result.append(
                {
                    "id": None,
                    "date": date_match.group(0) if date_match else item.get("date", ""),
                    "added": item.get("date", ""),
                    "text": item["text"],
                    "category": item.get("category", ""),
                    "teacher": item.get("teacher", ""),
                    "type": kind,
                    "positive": kind == "pozytywna",
                    "negative": kind == "negatywna",
                    "source": "html",
                    # Diagnostics (0.6.1): page structure without the text.
                    "raw": {
                        "headers": headers,
                        "cells": [
                            _clean_text(c.get_text(" "))[:40]
                            for i, c in enumerate(cells)
                            if columns.get(i) != "text"
                        ],
                        "markers": markers[:300],
                    },
                }
            )
    return result


def _gateway_user_name(client, user_id: str, cache: ApiCache | None) -> str:
    if not user_id:
        return ""
    try:
        user = _gateway_cached(client, f"/gateway/api/2.0/Users/{user_id}", cache).get("User") or {}
    except Exception:
        return ""
    return _clean_text(f"{user.get('FirstName', '')} {user.get('LastName', '')}")


def _grade_comment(client, g: dict[str, Any], cache: ApiCache | None) -> str | None:
    texts = []
    for c in g.get("Comments") or []:
        cid = (c or {}).get("Id")
        if not cid:
            continue
        try:
            data = _gateway_cached(client, f"/gateway/api/2.0/Grades/Comments/{cid}", cache)
            text = _clean_text((data.get("Comment") or {}).get("Text"))
        except Exception:
            text = ""
        if text:
            texts.append(text)
    return " / ".join(texts) or None


def _fetch_grades_gateway_sync(
    client, skip_subjects: set[str], cache: ApiCache | None = None
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Regular grades of subjects the grades page parser missed (0.6.2).

    librus-apix silently skips some rows of the grades page (seen with
    "wychowanie fizyczne"). Only subjects completely absent from the page
    result are taken from the API, so nothing can be listed twice.
    """
    grades = _gateway_rows(client, "/gateway/api/2.0/Grades")
    subjects = {
        str(x.get("Id")): _clean_text(x.get("Name"))
        for x in _gateway_cached(client, "/gateway/api/2.0/Subjects", cache).get("Subjects") or []
    }
    categories: dict[str, dict[str, Any]] = {}
    try:
        for c in _gateway_cached(client, "/gateway/api/2.0/Grades/Categories", cache).get("Categories") or []:
            categories[str(c.get("Id"))] = c
    except Exception as err:
        _LOGGER.debug("Grade categories unavailable: %s", err)

    users = cache
    result = []
    per_subject: dict[str, dict[str, int]] = {}
    samples: list[dict[str, Any]] = []
    for g in grades:
        subject = subjects.get(str((g.get("Subject") or {}).get("Id")), "")
        stats = per_subject.setdefault(subject or f"?{(g.get('Subject') or {}).get('Id')}", {
            "api": 0, "semestral": 0, "on_page": 0, "added": 0,
        })
        stats["api"] += 1
        if "fiz" in subject.casefold() and len(samples) < 5:
            samples.append(_raw(g))
        if any(g.get(f) for f in (
            "IsSemester", "IsSemesterProposition", "IsFinal", "IsFinalProposition",
        )):
            stats["semestral"] += 1
            continue  # only regular (bieżące) grades, like the page parser
        if not subject or subject.casefold() in skip_subjects:
            stats["on_page"] += 1
            continue
        stats["added"] += 1
        value = _clean_text(g.get("Grade"))
        symbol = value.casefold()
        if GRADE_NUMERIC_RE.match(value):
            kind = "grade"
        elif symbol in ("+", "-"):
            kind = "plus" if symbol == "+" else "minus"
        else:
            kind = "symbol" if value else "empty"
        category = categories.get(str((g.get("Category") or {}).get("Id"))) or {}
        weight = category.get("Weight")
        counts = category.get("CountToTheAverage")
        result.append(
            {
                "subject_name": subject,
                "display_value": value,
                "date": str(g.get("Date") or "")[:10],
                "category_name": _clean_text(category.get("Name")),
                "teacher": _gateway_user_name(
                    client, str((g.get("AddedBy") or {}).get("Id") or ""), users
                ),
                "semester": g.get("Semester"),
                "type": "numeric",
                "id": str(g.get("Id") or "") or None,
                "kind": kind,
                "label": GRADE_SYMBOLS.get(symbol) if kind != "grade" else None,
                "weight": weight if isinstance(weight, int) and weight > 0 else None,
                "counts": counts if isinstance(counts, bool) else None,
                "comment": _grade_comment(client, g, cache),
                "source": "api",
            }
        )

    # Point grades (oceny punktowe, e.g. WF in some schools) live outside
    # /Grades and on a separate table of the grades page that librus-apix
    # does not read - always take them from the API (0.6.4).
    point_samples: list[dict[str, Any]] = []
    try:
        points = _gateway_rows(client, "/gateway/api/2.0/PointGrades")
        point_categories: dict[str, dict[str, Any]] = {}
        try:
            data = _gateway_cached(client, "/gateway/api/2.0/PointGrades/Categories", cache)
            for c in next((v for v in data.values() if isinstance(v, list)), []):
                point_categories[str(c.get("Id"))] = c
        except Exception as err:
            _LOGGER.debug("Point grade categories unavailable: %s", err)
        for g in points:
            if len(point_samples) < 3:
                point_samples.append(_raw(g))
            subject = subjects.get(str((g.get("Subject") or {}).get("Id")), "")
            if not subject:
                continue
            value = next(
                (_clean_text(g.get(f)) for f in ("Grade", "GradeValue", "Value", "Points")
                 if g.get(f) not in (None, "")),
                "",
            )
            category = point_categories.get(str((g.get("Category") or {}).get("Id"))) or {}
            max_points = next(
                (category.get(f) for f in ("MaxPoints", "MaxPointsValue", "Max")
                 if category.get(f) not in (None, "")),
                None,
            )
            weight = category.get("Weight")
            stats = per_subject.setdefault(subject, {
                "api": 0, "semestral": 0, "on_page": 0, "added": 0,
            })
            stats["points"] = stats.get("points", 0) + 1
            result.append(
                {
                    "subject_name": subject,
                    "display_value": value,
                    "date": str(g.get("Date") or "")[:10],
                    "category_name": _clean_text(category.get("Name")),
                    "teacher": _gateway_user_name(
                        client, str((g.get("AddedBy") or {}).get("Id") or ""), users
                    ),
                    "semester": g.get("Semester"),
                    "type": "points",
                    "id": f"pt{g['Id']}" if g.get("Id") else None,
                    "kind": "points",
                    "label": "ocena punktowa",
                    "max_points": max_points,
                    "weight": weight if isinstance(weight, int) and weight > 0 else None,
                    "counts": None,
                    "comment": None,
                    "source": "api",
                }
            )
    except Exception as err:
        status = getattr(getattr(err, "response", None), "status_code", None)
        if status != 404:
            raise  # retried by _gateway(); last good data is kept on failure
        _LOGGER.debug("No point grades endpoint for this school: %s", err)

    diag: dict[str, Any] = {
        "status": "ok",
        "point_samples": point_samples,
        "api_grades": len(grades),
        "subjects": per_subject,
        "wf_samples": samples,
    }
    return result, diag


def _fetch_api_sync(client, skip_subjects: set[str], cache: ApiCache):
    """All gateway-API data in one pass; OAuth refreshed only when needed.

    Returns (extra grades, grade diagnostics, notes or None when notes failed).
    """
    try:
        grades, diag = _fetch_grades_gateway_sync(client, skip_subjects, cache)
    except Exception as err:
        # The OAuth token lives longer than one refresh; renew it only when
        # the API refuses (0.8.0) and try once more.
        _LOGGER.debug("API refused (%s), refreshing OAuth token", err)
        client.refresh_oauth()
        grades, diag = _fetch_grades_gateway_sync(client, skip_subjects, cache)
    try:
        notes = _fetch_notes_gateway_sync(client, cache)
    except Exception as err:
        diag["notes_error"] = str(err)[:200]
        notes = None
    return grades, diag, notes


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
        self.grades_api_info: dict[str, Any] = {}
        self._last_api_grades: list[dict[str, Any]] = []
        self._last_notes: list[dict[str, Any]] | None = None
        self._api_cache: ApiCache = {}
        self._page_grades_cache: tuple[float, list[dict[str, Any]]] | None = None
        self._ttl_cache: dict[str, tuple[float, Any]] = {}
        self._month_cache: dict[tuple[int, int], tuple[float, Any]] = {}
        self._message_cache: dict[str, dict[str, Any]] = {}
        self._week_cache: dict[str, tuple[float, Any]] = {}

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

        # 0.5.0: chronological across all subjects (oldest first), so the
        # newest grade is always grades[-1] - Librus returns them per subject.
        result.sort(key=self._grade_sort_key)
        return result

    @staticmethod
    def _grade_sort_key(g: dict[str, Any]) -> tuple[str, int]:
        try:
            gid = int(g.get("id") or 0)
        except (TypeError, ValueError):
            gid = 0
        return (str(g.get("date") or ""), gid)

    async def _gateway(self, func: Callable, *args):
        """Run a gateway-API fetch with one retry (0.6.6).

        Not via _run(): a gateway/OAuth refusal must neither drop the
        Synergia session nor fail the whole refresh. Callers run these
        one after another - parallel refresh_oauth() calls invalidate each
        other's token, which made API data (e.g. WF point grades) flicker.
        """
        last_err: Exception | None = None
        for attempt in range(2):
            try:
                await self._ensure_login()
                return await asyncio.to_thread(func, self._client, *args)
            except Exception as err:
                last_err = err
                if attempt == 0:
                    await asyncio.sleep(2)
        raise last_err  # type: ignore[misc]

    async def _page_grades(self, force: bool = False) -> list[dict[str, Any]]:
        """Grades page, at most every PAGE_GRADES_TTL_SECONDS (0.8.0).

        Since 0.8.0 the API is the main grade source; the page only adds
        what the API does not have (e.g. semestral grades) and is the
        fallback when the API fails.
        """
        now = time.monotonic()
        cached = self._page_grades_cache
        if not force and cached and cached[0] > now:
            return cached[1]
        try:
            grades = await self.get_grades()
        except Exception:
            if cached:
                return cached[1]
            raise
        self._page_grades_cache = (now + PAGE_GRADES_TTL_SECONDS, grades)
        return grades

    @staticmethod
    def _merge_grades(
        api: list[dict[str, Any]], page: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """API grades + page grades the API does not have (no duplicates)."""
        remaining = Counter(grade_content_key(g) for g in api)
        merged = list(api)
        for g in page:
            key = grade_content_key(g)
            if remaining[key] > 0:
                remaining[key] -= 1
                continue
            merged.append({**g, "source": "page"})
        return merged

    async def fetch_api_parts(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """(grades, notes): API first, grades page every few hours.

        If the API fails even after a retry, the grades page is read right
        away, the last good API-only grades (point grades) and notes are
        kept, and notes fall back to the /uwagi page only when nothing was
        fetched before.
        """
        notes: list[dict[str, Any]] | None
        try:
            api_grades, self.grades_api_info, notes = await self._gateway(
                _fetch_api_sync, set(), self._api_cache
            )
            self._last_api_grades = api_grades
            page = await self._page_grades()
            grades = self._merge_grades(api_grades, page)
        except Exception as err:
            _LOGGER.warning("Librus API unavailable, using the grades page: %s", err)
            page = await self._page_grades(force=True)
            kept = [g for g in self._last_api_grades if g.get("kind") == "points"]
            grades = self._merge_grades(kept, page)
            self.grades_api_info = {
                **(self.grades_api_info or {}),
                "status": f"błąd (strona ocen + poprzednie dane): {str(err)[:200]}",
            }
            notes = None

        if notes is not None:
            self._last_notes = notes
        elif self._last_notes is not None:
            notes = self._last_notes
        else:
            notes = await self._optional("notes", _fetch_notes_html_sync, default=[])

        grades.sort(key=self._grade_sort_key)
        notes = list(notes or [])
        notes.sort(key=lambda n: (n.get("date") or "", n.get("added") or "", n.get("id") or ""))
        return grades, notes

    async def _cached(self, key: str, ttl: float, factory: Callable):
        """Result of factory() kept for ttl seconds; stale copy on errors."""
        now = time.monotonic()
        hit = self._ttl_cache.get(key)
        if hit and hit[0] > now:
            return hit[1]
        try:
            value = await factory()
        except Exception:
            if hit:
                return hit[1]
            raise
        self._ttl_cache[key] = (now + ttl, value)
        return value

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
                # A message never changes: its content is fetched once (0.7.0).
                body = self._message_cache.get(x.href)
                if body is None:
                    body = await self._optional(
                        f"message content {x.href}",
                        message_content,
                        x.href,
                        default=None,
                    )
                    if body is not None:
                        self._message_cache[x.href] = body
                        while len(self._message_cache) > 50:
                            self._message_cache.pop(next(iter(self._message_cache)))
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
        now = time.monotonic()
        for offset, monday in enumerate(mondays):
            # Current week every refresh; later weeks at most every
            # FUTURE_WEEK_TTL_SECONDS (0.7.0).
            key = monday.date().isoformat()
            cached = self._week_cache.get(key)
            if offset > 0 and cached and cached[0] > now:
                week = cached[1]
            else:
                week = await self._optional(
                    f"timetable {monday.date()}",
                    get_timetable,
                    monday,
                    default=None,
                )
                if week is not None:
                    self._week_cache[key] = (now + FUTURE_WEEK_TTL_SECONDS, week)
                elif cached:
                    week = cached[1]  # keep the last good copy on an error
            for old_key in [k for k in self._week_cache if k < mondays[0].date().isoformat()]:
                self._week_cache.pop(old_key, None)
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
        fresh_months = {
            ((today + timedelta(days=d)).year, (today + timedelta(days=d)).month)
            for d in range(FRESH_SCHEDULE_DAYS + 1)
        }
        for key in [k for k in self._month_cache if k not in months]:
            self._month_cache.pop(key, None)
        for year, month in sorted(months):
            cached = self._month_cache.get((year, month))
            if (year, month) not in fresh_months and cached and cached[0] > now_mono:
                schedule = cached[1]
            else:
                schedule = await self._optional(
                    f"schedule {year}-{month:02d}",
                    get_schedule,
                    f"{month:02d}",
                    str(year),
                    False,
                    default=None,
                )
                if schedule is not None:
                    self._month_cache[(year, month)] = (
                        now_mono + FAR_MONTH_TTL_SECONDS, schedule
                    )
                elif cached:
                    schedule = cached[1]
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
        student = await self._cached("student", SLOW_DATA_TTL_SECONDS, self.get_student)

        homework, messages, attendance, timetable, schedule = await asyncio.gather(
            self._cached("homework", SLOW_DATA_TTL_SECONDS, self.get_homework),
            self.get_messages(),
            self._cached("attendance", SLOW_DATA_TTL_SECONDS, self.get_attendance),
            self.get_timetable(),
            self.get_schedule(),
        )
        # Gateway-API parts strictly after the rest (see _gateway()).
        grades, notes = await self.fetch_api_parts()
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
            "notes": notes,
            "grades_api": self.grades_api_info,
        }
