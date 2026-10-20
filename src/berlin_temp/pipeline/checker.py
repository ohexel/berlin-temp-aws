"""checker: request finalize once for every recent run whose 25 leads are all DONE."""

import json
import logging
from datetime import UTC, datetime, timedelta

from berlin_temp.pipeline import state
from berlin_temp.pipeline.discover import WINDOW_HOURS_BEFORE_CURRENT
from berlin_temp.pipeline.forecast import LEAD_COUNT, ForecastKey

LOGGER = logging.getLogger(__name__)


def window_runs(now: datetime) -> list[datetime]:
    """Run times of the current UTC hour and the two before it, newest first.

    Same window as discover, but derived from the clock instead of the DWD listing.
    """
    current_hour = now.replace(minute=0, second=0, microsecond=0)
    return [current_hour - timedelta(hours=back) for back in range(WINDOW_HOURS_BEFORE_CURRENT + 1)]


def run_checker(dynamodb, sqs, table: str, queue_url: str, *, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    runs = [ForecastKey(run, 0).run_label for run in window_runs(now)]

    requested: list[str] = []
    skipped = {"published": 0, "already_requested": 0, "incomplete": 0, "send_failed": 0}
    for run_label in runs:
        published, already_requested = state.run_flags(dynamodb, table, run_label)
        if published:
            skipped["published"] += 1
            continue
        if already_requested:
            skipped["already_requested"] += 1
            continue
        if state.done_lead_count(dynamodb, table, run_label) < LEAD_COUNT:
            skipped["incomplete"] += 1
            continue
        try:
            sqs.send_message(QueueUrl=queue_url, MessageBody=json.dumps({"run_time": run_label}))
        except Exception as error:
            LOGGER.warning(
                "could not queue finalize for %s: %s; next tick retries", run_label, error
            )
            skipped["send_failed"] += 1
            continue
        state.mark_finalize_requested(dynamodb, table, run_label, now)
        requested.append(run_label)

    return {"runs_considered": runs, "finalize_requested": requested, "skipped": skipped}
