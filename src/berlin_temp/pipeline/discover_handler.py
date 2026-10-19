"""Lambda entry point for discover (EventBridge Scheduler, every 5 minutes)."""

import json
import logging
import os
import time

import boto3

from berlin_temp.pipeline.discover import run_discover

LOGGER = logging.getLogger(__name__)
logging.getLogger().setLevel(logging.INFO)


def handler(event, context):
    started = time.monotonic()
    summary = run_discover(
        boto3.client("dynamodb"),
        boto3.client("sqs"),
        os.environ["STATE_TABLE"],
        os.environ["QUEUE_URL"],
    )
    summary["seconds"] = round(time.monotonic() - started, 1)
    LOGGER.info(json.dumps(summary, sort_keys=True))
    return summary
