"""Google Calendar availability and event operations for interview scheduling."""

import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from app.core.config import settings

CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar"
CALENDAR_API = "https://www.googleapis.com/calendar/v3/calendars"


class CalendarNotConfigured(RuntimeError):
    pass


class CalendarUnavailable(RuntimeError):
    pass


def resolve_host_calendar_id(credentials_path: Path, configured_id: str) -> str:
    configured = (configured_id or "").strip()
    service_account_email = ""
    try:
        payload = json.loads(Path(credentials_path).read_text(encoding="utf-8"))
        service_account_email = str(payload.get("client_email") or "").strip()
    except (OSError, ValueError, json.JSONDecodeError):
        service_account_email = ""
    if not configured or configured.casefold() == "primary":
        if service_account_email:
            return service_account_email
        raise CalendarNotConfigured(
            "Google Calendar is not configured. Set GOOGLE_CALENDAR_HOST_ID to an existing calendar ID shared with the service account. "
            "Service accounts do not have a 'primary' calendar alias."
        )
    return configured


def service_account_email(credentials_path: Path | None) -> str:
    try:
        payload = json.loads(Path(credentials_path).read_text(encoding="utf-8"))
        return str(payload.get("client_email") or "").strip()
    except (OSError, TypeError, ValueError):
        return ""


def _google_error_reason(response) -> str:
    try:
        errors = response.json().get("error", {}).get("errors") or []
        return str(errors[0].get("reason") or "") if errors else ""
    except (ValueError, AttributeError, IndexError):
        return ""


class GoogleCalendar:
    def __init__(self) -> None:
        path = settings.google_calendar_service_account_file
        if not path or not Path(path).is_file():
            raise CalendarNotConfigured(
                "Google Calendar is not configured. Set GOOGLE_CALENDAR_SERVICE_ACCOUNT_FILE to a service-account JSON file."
            )
        self.service_account_email = service_account_email(Path(path))
        self.host_calendar_id = resolve_host_calendar_id(Path(path), settings.google_calendar_host_id)
        try:
            from google.auth.transport.requests import AuthorizedSession
            from google.oauth2 import service_account

            credentials = service_account.Credentials.from_service_account_file(
                str(path),
                scopes=[CALENDAR_SCOPE],
            )
            self.session = AuthorizedSession(credentials)
        except (OSError, ValueError) as exc:
            raise CalendarNotConfigured("Google Calendar service-account credentials could not be loaded.") from exc

    @staticmethod
    def _calendar_url(calendar_id: str) -> str:
        return f"{CALENDAR_API}/{quote(calendar_id, safe='')}/events"

    def _request(self, method: str, url: str, calendar_id: str | None = None, **kwargs) -> dict:
        target = calendar_id or self.host_calendar_id
        sa_email = self.service_account_email or "the service account"
        try:
            response = self.session.request(method, url, timeout=20, **kwargs)
            if response.status_code >= 400:
                reason = _google_error_reason(response)
                if response.status_code == 404:
                    raise CalendarUnavailable(
                        f"Calendar '{target}' was not found by Google. Check the calendar ID, then share that calendar with "
                        f"{sa_email} using the permission 'Make changes to events'. Service accounts cannot use 'primary'."
                    )
                if reason == "forbiddenForServiceAccounts":
                    raise CalendarUnavailable(
                        "Google does not let a service account invite attendees unless domain-wide delegation is set up "
                        "(Google Workspace only). Use a Workspace account with delegation, or add attendees manually."
                    )
                if response.status_code == 403:
                    raise CalendarUnavailable(
                        f"Access to calendar '{target}' was denied. Share it with {sa_email} using 'Make changes to events', "
                        "and confirm the Google Calendar API is enabled for the service account's project."
                    )
                if response.status_code == 401:
                    raise CalendarUnavailable(
                        "Google rejected the service-account credentials. Check GOOGLE_CALENDAR_SERVICE_ACCOUNT_FILE."
                    )
                raise CalendarUnavailable(
                    f"Google Calendar returned HTTP {response.status_code} for '{target}'; verify calendar permissions and that the Google Calendar API is enabled."
                )
            if response.status_code == 204 or not response.content:
                return {}
            return response.json()
        except CalendarUnavailable:
            raise
        except Exception as exc:
            raise CalendarUnavailable("Google Calendar could not be reached. Check calendar permissions and network access.") from exc

    def list_events(self, calendar_id: str, start: datetime, end: datetime) -> list[dict]:
        events: list[dict] = []
        page_token = None
        while True:
            params = {
                "timeMin": start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                "timeMax": end.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                "singleEvents": "true",
                "orderBy": "startTime",
                "maxResults": 2500,
            }
            if page_token:
                params["pageToken"] = page_token
            result = self._request("GET", self._calendar_url(calendar_id), calendar_id=calendar_id, params=params)
            events.extend(result.get("items", []))
            page_token = result.get("nextPageToken")
            if not page_token:
                return events

    def check_calendar(self, calendar_id: str) -> tuple[bool, str | None]:
        """Return whether the service account can read this calendar, with a human-readable problem."""
        try:
            self._request(
                "GET",
                self._calendar_url(calendar_id),
                calendar_id=calendar_id,
                params={"maxResults": 1},
            )
            return True, None
        except CalendarUnavailable as exc:
            return False, str(exc)

    def is_available(
        self,
        calendar_ids: list[str],
        start: datetime,
        end: datetime,
        *,
        ignore_event_id: str | None = None,
    ) -> tuple[bool, str | None]:
        for calendar_id in dict.fromkeys(calendar_ids):
            for event in self.list_events(calendar_id, start, end):
                if ignore_event_id and event.get("id") == ignore_event_id:
                    continue
                if event.get("status") == "cancelled":
                    continue
                event_start = event.get("start", {}).get("dateTime") or event.get("start", {}).get("date")
                event_end = event.get("end", {}).get("dateTime") or event.get("end", {}).get("date")
                if not event_start or not event_end:
                    continue
                busy_start = datetime.fromisoformat(event_start.replace("Z", "+00:00"))
                busy_end = datetime.fromisoformat(event_end.replace("Z", "+00:00"))
                if busy_start.tzinfo is None:
                    busy_start = busy_start.replace(tzinfo=timezone.utc)
                if busy_end.tzinfo is None:
                    busy_end = busy_end.replace(tzinfo=timezone.utc)
                if start < busy_end and end > busy_start:
                    return False, calendar_id
        return True, None

    def create_event(
        self,
        *,
        event_id: str,
        summary: str,
        description: str,
        start: datetime,
        end: datetime,
        timezone_name: str,
        location: str | None,
        attendees: list[str],
    ) -> dict:
        resource = {
            "id": event_id,
            "summary": summary,
            "description": description,
            "start": {"dateTime": start.isoformat(), "timeZone": timezone_name},
            "end": {"dateTime": end.isoformat(), "timeZone": timezone_name},
            "attendees": [{"email": attendee} for attendee in dict.fromkeys(attendees)],
            "guestsCanModify": False,
            "guestsCanInviteOthers": False,
        }
        if location:
            resource["location"] = location
        return self._request(
            "POST",
            self._calendar_url(self.host_calendar_id),
            params={"sendUpdates": "all"},
            json=resource,
        )

    def update_event(
        self,
        event_id: str,
        *,
        summary: str,
        description: str,
        start: datetime,
        end: datetime,
        timezone_name: str,
        location: str | None,
        attendees: list[str],
    ) -> dict:
        resource = {
            "summary": summary,
            "description": description,
            "start": {"dateTime": start.isoformat(), "timeZone": timezone_name},
            "end": {"dateTime": end.isoformat(), "timeZone": timezone_name},
            "attendees": [{"email": attendee} for attendee in dict.fromkeys(attendees)],
        }
        if location:
            resource["location"] = location
        return self._request(
            "PATCH",
            f"{self._calendar_url(self.host_calendar_id)}/{quote(event_id, safe='')}",
            params={"sendUpdates": "all"},
            json=resource,
        )

    def delete_event(self, event_id: str) -> None:
        self._request(
            "DELETE",
            f"{self._calendar_url(self.host_calendar_id)}/{quote(event_id, safe='')}",
            params={"sendUpdates": "all"},
        )
