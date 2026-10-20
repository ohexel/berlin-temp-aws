"""Lambda entry point for finalize (SQS trigger, batch size 1)."""

import json
import logging
import os

import boto3
from botocore.config import Config

from berlin_temp.pipeline.finalize import PUT_WORKERS, run_finalize
from berlin_temp.pipeline.forecast import parse_run_label

LOGGER = logging.getLogger(__name__)
logging.getLogger().setLevel(logging.INFO)


def handler(event, context):
    records = event["Records"]
    if len(records) != 1:
        raise ValueError(f"expected exactly 1 SQS record (batch size 1), got {len(records)}")
    run_time = parse_run_label(json.loads(records[0]["body"])["run_time"])
    summary = run_finalize(
        boto3.client("s3", config=Config(max_pool_connections=PUT_WORKERS)),
        boto3.client("dynamodb"),
        os.environ["DATA_BUCKET"],
        os.environ["DASHBOARD_BUCKET"],
        os.environ["STATE_TABLE"],
        run_time,
    )
    LOGGER.info(json.dumps(summary, sort_keys=True))
    return summary
