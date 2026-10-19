"""Lambda entry point for ingest_lead (SQS trigger, batch size 1)."""

import json
import logging
import os
import time

import boto3

from berlin_temp.pipeline import state
from berlin_temp.pipeline.forecast import ForecastKey
from berlin_temp.pipeline.ingest import load_mask, run_ingest_lead

LOGGER = logging.getLogger(__name__)
logging.getLogger().setLevel(logging.INFO)

_mask_cache: dict[str, object] = {}  # loaded once, reused while the Lambda stays warm
_state = {"cold_start": True}  # True only for the first invocation of an execution environment


def _log(**fields) -> None:
    LOGGER.info(json.dumps(fields, sort_keys=True))


def handler(event, context):
    records = event["Records"]
    if len(records) != 1:
        raise ValueError(f"expected exactly 1 SQS record (batch size 1), got {len(records)}")
    record = records[0]
    key = ForecastKey.from_message(json.loads(record["body"]))
    receive_count = int(record.get("attributes", {}).get("ApproximateReceiveCount", 1))

    bucket, table = os.environ["DATA_BUCKET"], os.environ["STATE_TABLE"]
    s3, dynamodb = boto3.client("s3"), boto3.client("dynamodb")
    started = time.monotonic()
    log = {
        "run_time": key.run_label,
        "lead": key.lead_label,
        "receive_count": receive_count,
        "cold_start": _state["cold_start"],
    }
    _state["cold_start"] = False
    phases: dict[str, float] = {}
    try:
        if "mask" not in _mask_cache:
            lap = time.monotonic()
            _mask_cache["mask"] = load_mask(s3, bucket)
            phases["mask_load"] = round(time.monotonic() - lap, 1)
        summary = run_ingest_lead(s3, bucket, key, _mask_cache["mask"])
        phases.update(summary["seconds"])
        lap = time.monotonic()
        outcome = state.mark_done(dynamodb, table, key)
        phases["state_update"] = round(time.monotonic() - lap, 1)
    except Exception as error:
        _log(
            **log,
            outcome="error",
            error=str(error),
            phases=phases,
            seconds=round(time.monotonic() - started, 1),
        )
        try:
            state.record_failure(dynamodb, table, key, receive_count, error)
        except Exception:
            LOGGER.exception("could not record the failure in DynamoDB")
        raise
    _log(**log, outcome=outcome, phases=phases, seconds=round(time.monotonic() - started, 1))
    return {**summary, "state": outcome}
