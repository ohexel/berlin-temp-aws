"""discover: find (run, lead) pairs that DWD has published and queue them for ingest."""

import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

from berlin_temp.pipeline import state
from berlin_temp.pipeline.forecast import (
    BASE_URL,
    INDICATORS,
    LEAD_COUNT,
    ForecastKey,
    format_lead,
)
from berlin_temp.reference import sources

LOGGER = logging.getLogger(__name__)

MAX_RUNS = 3
WINDOW_HOURS_BEFORE_CURRENT = 2  # current UTC hour and the two before it
MAX_NEW_PAIRS_PER_TICK = 5
HEAD_TIMEOUT_SECONDS = 10
# Directory listing: 3 attempts x 15 s + retry pauses (up to ~10.5 s) must fit in the 60 s Lambda.
LIST_TIMEOUT_SECONDS = 15
HEAD_WORKERS = 4  # fewer parallel connections to DWD (was 8)
_RUN_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")


class DiscoverError(RuntimeError):
    """A lead could not be queued; its item was removed so the next tick retries."""


def list_runs() -> list[datetime]:
    """Runs advertised in the T_2M directory listing, newest first (UTC)."""
    content, _ = sources.fetch(f"{BASE_URL}/T_2M/r/", timeout=LIST_TIMEOUT_SECONDS)
    text = urllib.parse.unquote(content.decode("utf-8", errors="replace"))
    labels = set(_RUN_PATTERN.findall(text))
    runs = (datetime.strptime(label, "%Y-%m-%dT%H:%M").replace(tzinfo=UTC) for label in labels)
    return sorted(runs, reverse=True)


def file_exists(url: str) -> bool:
    """HEAD request. 404 means "not published yet"; any other problem is logged and also false.

    Connection resets and timeouts are retried first (see sources.with_retries).
    """
    request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": sources.USER_AGENT})

    def head() -> bool:
        with urllib.request.urlopen(request, timeout=HEAD_TIMEOUT_SECONDS) as response:
            return response.status == 200

    try:
        return sources.with_retries(head, f"HEAD {url}")
    except urllib.error.HTTPError as error:
        if error.code != 404:
            LOGGER.warning("HEAD %s answered %s; treating as not ready", url, error.code)
        return False
    except (urllib.error.URLError, ConnectionError, TimeoutError) as error:
        LOGGER.warning("HEAD %s failed (%s); treating as not ready", url, error)
        return False


def lead_ready(key: ForecastKey) -> bool:
    """All four indicator files exist (stops at the first missing one)."""
    return all(file_exists(key.field_url(indicator)) for indicator in INDICATORS)


def _window(now: datetime) -> tuple[datetime, datetime]:
    current_hour = now.replace(minute=0, second=0, microsecond=0)
    return current_hour - timedelta(hours=WINDOW_HOURS_BEFORE_CURRENT), now


def run_discover(
    dynamodb,
    sqs,
    table: str,
    queue_url: str,
    *,
    now: datetime | None = None,
    runs_fn: Callable[[], list[datetime]] = list_runs,
    ready_fn: Callable[[ForecastKey], bool] = lead_ready,
) -> dict:
    now = now or datetime.now(UTC)
    earliest, latest = _window(now)
    runs = [run for run in runs_fn() if earliest <= run <= latest][:MAX_RUNS]

    queued: list[str] = []
    skipped = {"already_known": 0, "not_ready": 0, "lost_race": 0}
    for run in runs:
        if len(queued) >= MAX_NEW_PAIRS_PER_TICK:
            break
        run_label = ForecastKey(run, 0).run_label
        known = state.lead_labels_for_run(dynamodb, table, run_label)
        candidates = [
            ForecastKey(run, hours)
            for hours in range(LEAD_COUNT)
            if format_lead(hours) not in known
        ]
        skipped["already_known"] += LEAD_COUNT - len(candidates)
        with ThreadPoolExecutor(max_workers=HEAD_WORKERS) as pool:
            ready = list(pool.map(ready_fn, candidates))
        skipped["not_ready"] += ready.count(False)

        for key, is_ready in zip(candidates, ready, strict=True):
            if not is_ready:
                continue
            if len(queued) >= MAX_NEW_PAIRS_PER_TICK:
                break
            if not state.create_lead_item(dynamodb, table, key, now):
                skipped["lost_race"] += 1
                continue
            try:
                sqs.send_message(
                    QueueUrl=queue_url,
                    MessageBody=json.dumps({"run_time": key.run_label, "lead": key.lead_label}),
                )
            except Exception as error:
                state.delete_lead_item(dynamodb, table, key)
                raise DiscoverError(
                    f"could not queue {key.run_label} {key.lead_label}: {error}"
                ) from error
            queued.append(f"{key.run_label} {key.lead_label}")

    return {
        "runs_considered": [ForecastKey(run, 0).run_label for run in runs],
        "queued": queued,
        "skipped": skipped,
        "cap_reached": len(queued) >= MAX_NEW_PAIRS_PER_TICK,
    }
