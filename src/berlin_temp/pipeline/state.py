"""Lead status in DynamoDB: QUEUED -> DONE, or DEAD_LETTERED after the last failed attempt."""

import logging
from datetime import UTC, datetime

from berlin_temp.pipeline.forecast import ForecastKey

LOGGER = logging.getLogger(__name__)

MAX_RECEIVE_COUNT = 5  # must equal maxReceiveCount of ingest-queue
_ERROR_LIMIT = 1000


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def lead_item_key(key: ForecastKey) -> dict:
    return {"PK": {"S": f"run#{key.run_label}"}, "SK": {"S": f"lead#{key.lead_label}"}}


def mark_done(dynamodb, table: str, key: ForecastKey) -> str:
    """Set DONE unless already DONE. Returns "done" or "already_done"; a missing item raises."""
    try:
        dynamodb.update_item(
            TableName=table,
            Key=lead_item_key(key),
            UpdateExpression="SET #status = :done, done_at = :now",
            ConditionExpression="attribute_exists(PK) AND #status <> :done",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={":done": {"S": "DONE"}, ":now": {"S": _now()}},
            ReturnValuesOnConditionCheckFailure="ALL_OLD",
        )
        return "done"
    except dynamodb.exceptions.ConditionalCheckFailedException as error:
        item = error.response.get("Item")
        if item is not None and item["status"]["S"] == "DONE":
            return "already_done"
        raise


def record_failure(
    dynamodb, table: str, key: ForecastKey, receive_count: int, error: Exception
) -> None:
    """Store last_error and receive_count; set DEAD_LETTERED on the last allowed attempt.

    A lead that is already DONE (or has no item) is left unchanged.
    """
    values = {
        ":error": {"S": f"{type(error).__name__}: {error}"[:_ERROR_LIMIT]},
        ":count": {"N": str(receive_count)},
        ":done": {"S": "DONE"},
    }
    expression = "SET last_error = :error, receive_count = :count"
    if receive_count >= MAX_RECEIVE_COUNT:
        expression += ", #status = :dead"
        values[":dead"] = {"S": "DEAD_LETTERED"}
    try:
        dynamodb.update_item(
            TableName=table,
            Key=lead_item_key(key),
            UpdateExpression=expression,
            ConditionExpression="attribute_exists(PK) AND #status <> :done",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues=values,
        )
    except dynamodb.exceptions.ConditionalCheckFailedException:
        LOGGER.warning("failure not recorded: lead item missing or already DONE")


LEAD_TTL_SECONDS = 7 * 24 * 3600


def lead_labels_for_run(dynamodb, table: str, run_label: str) -> set[str]:
    """Leads of a run that already have an item (any status)."""
    labels: set[str] = set()
    request = {
        "TableName": table,
        "KeyConditionExpression": "PK = :pk",
        "ExpressionAttributeValues": {":pk": {"S": f"run#{run_label}"}},
        "ProjectionExpression": "SK",
        "ConsistentRead": True,
    }
    while True:
        response = dynamodb.query(**request)
        for item in response["Items"]:
            sort_key = item["SK"]["S"]
            if sort_key.startswith("lead#"):
                labels.add(sort_key.removeprefix("lead#"))
        if "LastEvaluatedKey" not in response:
            return labels
        request["ExclusiveStartKey"] = response["LastEvaluatedKey"]


def create_lead_item(dynamodb, table: str, key: ForecastKey, now: datetime) -> bool:
    """Create the QUEUED item if absent. False means it already exists."""
    ttl = int(now.timestamp()) + LEAD_TTL_SECONDS
    try:
        dynamodb.put_item(
            TableName=table,
            Item={
                **lead_item_key(key),
                "status": {"S": "QUEUED"},
                "queued_at": {"S": now.isoformat(timespec="seconds")},
                "ttl": {"N": str(ttl)},
            },
            ConditionExpression="attribute_not_exists(PK)",
        )
        return True
    except dynamodb.exceptions.ConditionalCheckFailedException:
        return False


def delete_lead_item(dynamodb, table: str, key: ForecastKey) -> None:
    dynamodb.delete_item(TableName=table, Key=lead_item_key(key))
