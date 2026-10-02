"""New-item events and grade statistics."""
from __future__ import annotations

import asyncio

from conftest import CoreState, MemoryStore

from z2z_librus.api import build_grade
from z2z_librus.notifier import KEY_SCHEMA, NewItemTracker, storage_key
from z2z_librus.sensor import _all_subjects, _grade_stats


class Bus:
    def __init__(self):
        self.fired = []

    def async_fire(self, event_type, data):
        self.fired.append((event_type, data))

    def async_listen_once(self, *args):
        raise AssertionError("HA is running - events must fire at once")


class Hass:
    def __init__(self):
        self.state = CoreState.running
        self.bus = Bus()


def data(grades, notes=()):
    return {"me": {"Me": {"Name": "Gabriela"}}, "grades": list(grades), "notes": list(notes)}


def run(tracker, payload):
    asyncio.run(tracker.async_process(payload))


def setup_function():
    MemoryStore.data.clear()


def test_first_run_is_silent_then_new_grade_fires():
    hass = Hass()
    tracker = NewItemTracker(hass, "e1")
    old = build_grade("5", subject="matematyka", date="2026-09-30")
    run(tracker, data([old]))
    assert hass.bus.fired == []
    new = build_grade("np", subject="język polski", date="2026-10-02")
    run(tracker, data([old, new]))
    assert [(t, d["display_value"]) for t, d in hass.bus.fired] == [("z2z_librus_new_grade", "np")]


def test_same_grade_twice_on_one_day_fires_twice():
    hass = Hass()
    tracker = NewItemTracker(hass, "e2")
    run(tracker, data([]))
    plus = build_grade("+", subject="hiszpański", date="2026-10-02")
    run(tracker, data([plus]))
    run(tracker, data([plus, dict(plus)]))
    assert len(hass.bus.fired) == 2


def test_restart_does_not_repeat():
    hass = Hass()
    g = build_grade("5", subject="matematyka", date="2026-09-30")
    run(NewItemTracker(hass, "e3"), data([]))
    run(NewItemTracker(hass, "e3"), data([g]))
    run(NewItemTracker(hass, "e3"), data([g]))  # "after restart"
    assert len(hass.bus.fired) == 1


def test_burst_is_not_announced():
    hass = Hass()
    tracker = NewItemTracker(hass, "e4")
    run(tracker, data([]))
    many = [build_grade("5", subject=f"p{i}", date="2026-10-02") for i in range(40)]
    run(tracker, data(many))
    assert hass.bus.fired == []


def test_old_schema_rebaselines_grades_silently():
    hass = Hass()
    MemoryStore.data[storage_key("e5")] = {"seen": {"grades": ["x"], "notes": []}, "schema": KEY_SCHEMA - 1}
    tracker = NewItemTracker(hass, "e5")
    run(tracker, data([build_grade("4", subject="WF", date="2026-10-01", grade_type="points")]))
    assert hass.bus.fired == []
    assert MemoryStore.data[storage_key("e5")]["schema"] == KEY_SCHEMA


def test_grade_stats():
    grades = [
        build_grade("5", subject="a", date="d"),
        build_grade("np", subject="a", date="d"),
        build_grade("NP", subject="a", date="d"),
        build_grade("+", subject="a", date="d"),
        build_grade("4.00", subject="WF", date="d", grade_type="points"),
        build_grade("xyz", subject="a", date="d"),
    ]
    stats = _grade_stats(grades)
    assert stats["np_count"] == 2
    assert stats["plus_count"] == 1
    assert stats["numeric_count"] == 1
    assert stats["points_count"] == 1
    assert stats["symbol_counts"]["xyz"] == 1
    assert stats["symbol_labels"]["xyz"] is None


def test_subjects_case_insensitive():
    payload = {
        "grades": [build_grade("5", subject="Wychowanie fizyczne", date="d")],
        "timetable": [{"subject": "wychowanie fizyczne"}, {"subject": "religia / etyka"}],
    }
    assert _all_subjects(payload) == ["etyka", "religia", "Wychowanie fizyczne"]
