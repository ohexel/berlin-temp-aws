"""Lambda entry point for the checker (EventBridge Scheduler, every 5 minutes)."""

import json
import logging
import os
import time

import boto3

from berlin_temp.pipeline.checker import run_checker

LOGGER = logging.getLogger(__name__)
logging.getLogger().setLevel(logging.INFO)


def handler(event, context):
    started = time.monotonic()
    summary = run_checker(
        boto3.client("dynamodb"),
        boto3.client("sqs"),
        os.environ["STATE_TABLE"],
        os.environ["FINALIZE_QUEUE_URL"],
    )
    summary["seconds"] = round(time.monotonic() - started, 1)
    LOGGER.info(json.dumps(summary, sort_keys=True))
    return summary
