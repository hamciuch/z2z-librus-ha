"""Grades page parsing, API mapping, account guard, merging, cookie isolation."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from bs4 import BeautifulSoup
from librus_apix.grades import _extract_grades_numeric

from z2z_librus import api
from z2z_librus.api import (
    AccountMismatch,
    LibrusClient,
    _check_account,
    _fetch_api_sync,
    _new_isolated_client,
    build_grade,
    format_points,
    grade_content_key,
)

FIXTURES = Path(__file__).parent / "fixtures"


# --- grades page ---------------------------------------------------------------

def page_grades() -> list[dict]:
    html = (FIXTURES / "grades_page.html").read_text(encoding="utf-8")
    rows = BeautifulSoup(html, "lxml").find_all(
        "tr", attrs={"class": ["line0", "line1"], "id": None}
    )
    semesters, _ = _extract_grades_numeric(rows)
    return [
        LibrusClient._grade_to_dict(g, "numeric", subject)
        for sem in semesters
        for subject, grades in sem.items()
        for g in grades
    ]


def test_page_grades_keep_symbols_and_details():
    grades = {g["id"]: g for g in page_grades()}
    assert grades["1001"]["display_value"] == "5+"
    assert grades["1001"]["kind"] == "grade"
    assert grades["1001"]["comment"] == "Opis siebie."
    assert grades["1002"]["display_value"] == "np"
    assert grades["1002"]["kind"] == "symbol"
    assert grades["1002"]["label"] == "nieprzygotowany"
    assert grades["1003"]["kind"] == "plus"
    assert grades["1001"]["source"] == "page"


def test_counts_unknown_unless_librus_shows_it():
    grades = {g["id"]: g for g in page_grades()}
    assert grades["1001"]["counts"] is None  # no "Licz do średniej"
    assert grades["1005"]["counts"] is True


# --- shared builder -----------------------------------------------------------------

@pytest.mark.parametrize(
    ("raw", "shown"),
    [("4.00", "4"), ("4.50", "4.5"), (0, "0"), ("0.00", "0"), (7, "7"), ("abc", "abc")],
)
def test_format_points(raw, shown):
    assert format_points(raw) == shown


def test_points_grade_text():
    g = build_grade("4.00", subject="WF", date="2026-10-01", grade_type="points", max_points="5.00")
    assert g["display_value"] == "4"
    assert g["kind"] == "points"
    assert g["points_text"] == "4/5 pkt"
    zero = build_grade(0, subject="WF", date="2026-10-01", grade_type="points")
    assert zero["display_value"] == "0" and zero["points_text"] == "0 pkt"


def test_symbol_case_insensitive_label():
    g = build_grade("NP", subject="x", date="2026-10-01")
    assert g["kind"] == "symbol" and g["label"] == "nieprzygotowany"


# --- gateway API -------------------------------------------------------------------

class FakeResponse:
    def __init__(self, data, status=200):
        self._data = data
        self.status_code = status

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests

            err = requests.HTTPError(f"{self.status_code}")
            err.response = self
            raise err


class FakeClient:
    """Answers gateway paths from a dict; tokens are switched by refresh_oauth."""

    BASE_URL = "https://synergia.librus.pl"

    def __init__(self, routes, me_logins):
        self.routes = routes
        self.me_logins = list(me_logins)  # login returned by /Me per token
        self.refreshes = 0

    def refresh_oauth(self):
        self.refreshes += 1

    def get(self, url):
        path = url.replace(self.BASE_URL, "")
        if path == api.GATEWAY_ME:
            login = self.me_logins[min(self.refreshes, len(self.me_logins) - 1)]
            if login is None:
                return FakeResponse({"Status": "Error", "Code": "TokenIsExpired"})
            return FakeResponse({"Me": {"Account": {"Login": login}}})
        value = self.routes.get(path)
        if value is None:
            return FakeResponse({}, 404)
        return FakeResponse(value)


ROUTES = {
    "/gateway/api/2.0/Grades": {"Grades": [
        {"Id": 11, "Grade": "5", "Date": "2026-09-30", "Subject": {"Id": 1},
         "Category": {"Id": 7}, "AddedBy": {"Id": 99}, "Semester": 1},
        {"Id": 12, "Grade": "np", "Date": "2026-09-25", "Subject": {"Id": 2},
         "Category": {"Id": 7}, "AddedBy": {"Id": 99}, "Semester": 1},
        {"Id": 13, "Grade": "5", "Date": "2026-09-30", "Subject": {"Id": 1},
         "IsSemester": True, "Semester": 1},
    ]},
    "/gateway/api/2.0/Subjects": {"Subjects": [
        {"Id": 1, "Name": "matematyka"},
        {"Id": 2, "Name": "język polski"},
        {"Id": 3, "Name": "wychowanie fizyczne"},
    ]},
    "/gateway/api/2.0/Grades/Categories": {"Categories": [
        {"Id": 7, "Name": "kartkówka", "Weight": 2, "CountToTheAverage": True},
    ]},
    "/gateway/api/2.0/Users/99": {"User": {"FirstName": "Anna", "LastName": "Testowa"}},
    "/gateway/api/2.0/PointGrades": {"Grades": [
        {"Id": 5, "Grade": "4.00", "Date": "2026-10-01", "Subject": {"Id": 3},
         "Category": {"Id": 8}, "AddedBy": {"Id": 99}, "Semester": 1},
        {"Id": 6, "Grade": 0, "Date": "2026-10-01", "Subject": {"Id": 3},
         "Category": {"Id": 8}, "AddedBy": {"Id": 99}, "Semester": 1},
    ]},
    "/gateway/api/2.0/PointGrades/Categories": {"Categories": [
        {"Id": 8, "Name": "sprawność", "MaxPoints": "5.00"},
    ]},
    "/gateway/api/2.0/Notes": {"Notes": [
        {"Id": 1, "Text": "Brawo", "Date": "2026-09-29", "Positive": 1,
         "Category": {"Id": 4}, "Teacher": {"Id": 99}},
    ]},
    "/gateway/api/2.0/Notes/Categories": {"Categories": [{"Id": 4, "Name": "pochwała"}]},
}

ACCOUNT = {"login": "1234567u", "name": "Gabriela Testowa"}


def test_api_grades_points_and_notes():
    client = FakeClient(ROUTES, ["1234567u"])
    grades, diag, notes = _fetch_api_sync(client, ACCOUNT, {})
    by_id = {g["id"]: g for g in grades}
    assert "13" not in by_id  # semestral grades come from the page
    assert by_id["11"]["weight"] == 2 and by_id["11"]["counts"] is True
    assert by_id["11"]["teacher"] == "Anna Testowa"
    assert by_id["12"]["label"] == "nieprzygotowany"
    assert by_id["pt5"]["display_value"] == "4"
    assert by_id["pt5"]["points_text"] == "4/5 pkt"
    assert by_id["pt6"]["display_value"] == "0"
    assert diag["subjects"]["wychowanie fizyczne"]["points"] == 2
    assert diag["account_check"] == "login"
    assert notes[0]["type"] == "pozytywna" and "raw" not in notes[0]
    assert client.refreshes == 0  # token still valid: no refresh


def test_expired_token_refreshes_once():
    client = FakeClient(ROUTES, [None, "1234567u"])
    grades, diag, _ = _fetch_api_sync(client, ACCOUNT, {})
    assert client.refreshes == 1
    assert any(g["kind"] == "points" for g in grades)


def test_other_childs_token_is_rejected():
    client = FakeClient(ROUTES, ["7654321u", "7654321u"])
    with pytest.raises(AccountMismatch):
        _fetch_api_sync(client, ACCOUNT, {})


def test_wrong_token_then_own_after_refresh():
    client = FakeClient(ROUTES, ["7654321u", "1234567u"])
    grades, diag, _ = _fetch_api_sync(client, ACCOUNT, {})
    assert diag["account_check"] == "login" and grades


def test_account_by_name_when_no_login():
    cache: dict = {}

    class NameOnly(FakeClient):
        def get(self, url):
            if url.endswith(api.GATEWAY_ME):
                return FakeResponse({"Me": {"User": {"FirstName": "Gabriela", "LastName": "Testowa"}}})
            return super().get(url)

    assert _check_account(NameOnly(ROUTES, ["x"]), {"login": "", "name": "Testowa Gabriela"}, cache) == "name"


class MeClient(FakeClient):
    def __init__(self, me):
        super().__init__(ROUTES, ["x"])
        self.me = me

    def get(self, url):
        if url.endswith(api.GATEWAY_ME):
            return FakeResponse({"Me": self.me})
        return super().get(url)


@pytest.mark.parametrize(
    ("me", "account", "expected"),
    [
        # login written differently by the API - same digits, same account
        ({"Account": {"Login": "1234567"}}, ACCOUNT, "login"),
        # login format unknown but the student name matches
        ({"Account": {"Login": "abc", "FirstName": "Gabriela", "LastName": "Testowa"}}, ACCOUNT, "name"),
        # nothing to compare
        ({"Account": {}}, {"login": "", "name": ""}, "unknown"),
    ],
)
def test_account_check_is_tolerant(me, account, expected):
    assert _check_account(MeClient(me), account, {}) == expected


@pytest.mark.parametrize(
    "me",
    [
        {"Account": {"Login": "7654321u", "FirstName": "Wiktor", "LastName": "Testowy"}},
        {"Account": {"Login": "abc", "FirstName": "Wiktor", "LastName": "Testowy"}},
    ],
)
def test_account_check_rejects_other_child(me):
    with pytest.raises(AccountMismatch):
        _check_account(MeClient(me), ACCOUNT, {})


def test_no_point_grades_endpoint_is_fine():
    routes = {k: v for k, v in ROUTES.items() if "PointGrades" not in k}
    grades, diag, _ = _fetch_api_sync(FakeClient(routes, ["1234567u"]), ACCOUNT, {})
    assert diag["point_grades"].startswith("brak")
    assert not any(g["kind"] == "points" for g in grades)


# --- merge --------------------------------------------------------------------------

def test_merge_page_and_api_without_duplicates():
    api_grades = [
        build_grade("np", subject="Język polski", date="2026-09-25", source="api"),
        build_grade("4", subject="WF", date="2026-10-01", grade_type="points", source="api"),
    ]
    page = [
        build_grade("np", subject="język polski", date="2026-09-25"),
        build_grade("5", subject="język polski", date="2026-09-30", semester=1),
    ]
    merged = LibrusClient._merge_grades(api_grades, page)
    assert len(merged) == 3
    assert [g["source"] for g in merged].count("page") == 1
    assert grade_content_key(api_grades[0]) == grade_content_key(page[0])


# --- client state --------------------------------------------------------------------

def test_clients_do_not_share_cookies():
    a, b = _new_isolated_client(), _new_isolated_client()
    a.cookies.set("oauth_token", "GABRYSIA")
    b.cookies.set("oauth_token", "WIKTOR")
    assert a.cookies is not b.cookies
    assert a.cookies.get("oauth_token") == "GABRYSIA"


def test_state_roundtrip_only_for_same_login():
    client = LibrusClient("1234567u", "x")
    client._last_api_grades = [build_grade("4", subject="WF", date="2026-10-01", grade_type="points")]
    client._last_notes = []
    state = client.export_state()
    other = LibrusClient("7654321u", "x")
    other.import_state(state)
    assert other._last_api_grades == []
    same = LibrusClient("1234567u", "x")
    same.import_state(state)
    assert same._last_api_grades[0]["kind"] == "points"


def test_api_down_keeps_saved_point_grades(monkeypatch):
    client = LibrusClient("1234567u", "x")
    client.import_state({
        "login": "1234567u",
        "api_grades": [build_grade("4", subject="WF", date="2026-10-01", grade_type="points", source="api")],
        "notes": [{"id": "1", "date": "2026-09-29", "text": "Brawo"}],
    })

    async def api_down(*args):
        raise ConnectionError("gateway down")

    async def page(force=False):
        return [build_grade("5", subject="matematyka", date="2026-09-30")]

    monkeypatch.setattr(client, "_gateway", api_down)
    monkeypatch.setattr(client, "_page_grades", page)
    grades, notes = asyncio.run(client.fetch_api_parts())
    assert {g["kind"] for g in grades} == {"points", "grade"}
    assert notes[0]["text"] == "Brawo"
    assert client.grades_api_info["status"].startswith("błąd API")
