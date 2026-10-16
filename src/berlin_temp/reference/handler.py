"""Lambda entry point for reference_build (invoked manually)."""

import logging
import os

import boto3

from berlin_temp.reference.build import run_reference_build

logging.getLogger().setLevel(logging.INFO)


def handler(event, context):
    """event (optional): {"plr_layer": "<WFS layer name>"} overrides layer discovery."""
    bucket = os.environ["DATA_BUCKET"]
    plr_layer = (event or {}).get("plr_layer")
    return run_reference_build(boto3.client("s3"), bucket, plr_layer=plr_layer)
