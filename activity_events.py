from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Iterator

# Annotation only, and annotations are strings here - see scanner.py. Imported
# for real it pulls auth -> azure.identity -> cryptography into a module that
# builds a date window and pages a JSON feed.
if TYPE_CHECKING:
    from powerbi_client import PowerBIAdminClient


def _day_window(day: date) -> tuple[str, str]:
    """Build start/end for a single UTC day.

    The API requires startDateTime and endDateTime to fall on the SAME UTC day,
    wrapped in single quotes.
    """
    iso = day.isoformat()
    return f"'{iso}T00:00:00.000Z'", f"'{iso}T23:59:59.999Z'"


def fetch_activity_events(client: PowerBIAdminClient, day: date) -> Iterator[dict]:
    """Yield every audit activity event for one UTC day.

    Activity events only retain ~28 days of history, so this must run daily and
    accumulate into permanent storage — that snapshot IS the usage dataset.
    """
    start, end = _day_window(day)
    payload = client.get(
        "activityevents", params={"startDateTime": start, "endDateTime": end}
    )

    while True:
        for event in payload.get("activityEventEntities", []):
            yield event

        # The API pages via a continuation URI; it is null on the last page.
        continuation = payload.get("continuationUri")
        if not continuation:
            break
        payload = client.get(continuation)
