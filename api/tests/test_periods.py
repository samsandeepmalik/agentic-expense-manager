from datetime import date

from app.services.periods import resolve_period


TODAY = date(2026, 6, 5)


def test_month():
    assert resolve_period("2026-06", TODAY) == ("2026-06-01", "2026-06-30")


def test_default_is_current_month():
    assert resolve_period(None, TODAY) == ("2026-06-01", "2026-06-30")


def test_last3():
    assert resolve_period("last3", TODAY) == ("2026-04-01", "2026-06-30")


def test_last6_across_year():
    assert resolve_period("last6", date(2026, 2, 10)) == ("2025-09-01", "2026-02-28")


def test_ytd():
    assert resolve_period("ytd", TODAY) == ("2026-01-01", "2026-06-30")


def test_custom():
    assert resolve_period("2026-01-15:2026-03-10", TODAY) == ("2026-01-15", "2026-03-10")
