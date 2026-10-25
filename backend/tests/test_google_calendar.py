from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.google_calendar import CalendarUnavailable, GoogleCalendar, resolve_host_calendar_id


@pytest.mark.parametrize(
    ("status_code", "body", "message"),
    [
        (404, {}, "Calendar 'host@example.com' was not found by Google.*scheduler@example.iam.gserviceaccount.com"),
        (403, {}, "Access to calendar 'host@example.com' was denied.*Make changes to events"),
        (
            403,
            {"error": {"errors": [{"reason": "forbiddenForServiceAccounts"}]}},
            "domain-wide delegation",
        ),
        (500, {}, "HTTP 500 for 'host@example.com'"),
    ],
)
def test_calendar_http_errors_provide_configuration_guidance(status_code, body, message):
    calendar = object.__new__(GoogleCalendar)
    calendar.host_calendar_id = "host@example.com"
    calendar.service_account_email = "scheduler@example.iam.gserviceaccount.com"
    calendar.session = SimpleNamespace(
        request=lambda *_args, **_kwargs: SimpleNamespace(
            status_code=status_code,
            content=b"{}",
            json=lambda: body,
        )
    )

    with pytest.raises(CalendarUnavailable, match=message):
        calendar._request("GET", "https://calendar.example/events")


def test_check_calendar_reports_problem_for_unshared_calendar():
    calendar = object.__new__(GoogleCalendar)
    calendar.host_calendar_id = "host@example.com"
    calendar.service_account_email = "scheduler@example.iam.gserviceaccount.com"
    calendar.session = SimpleNamespace(
        request=lambda *_args, **_kwargs: SimpleNamespace(status_code=404, content=b"{}", json=lambda: {})
    )

    ok, problem = calendar.check_calendar("interviewer@example.com")

    assert ok is False
    assert "interviewer@example.com" in problem


def test_primary_host_calendar_uses_service_account_email(tmp_path: Path):
    credentials = tmp_path / "service-account.json"
    credentials.write_text('{"client_email": "scheduler@example.iam.gserviceaccount.com"}', encoding="utf-8")
    assert resolve_host_calendar_id(credentials, "primary") == "scheduler@example.iam.gserviceaccount.com"
    assert resolve_host_calendar_id(credentials, "shared-calendar@example.com") == "shared-calendar@example.com"
