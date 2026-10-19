from datetime import UTC, datetime

import boto3
import pytest
from botocore.stub import ANY, Stubber

from berlin_temp.pipeline import state
from berlin_temp.pipeline.forecast import ForecastKey

KEY = ForecastKey(datetime(2026, 10, 13, 12, 0, tzinfo=UTC), 5)
ITEM_KEY = {"PK": {"S": "run#2026-10-13T12:00Z"}, "SK": {"S": "lead#PT005H00M"}}


@pytest.fixture
def dynamodb():
    client = boto3.client(
        "dynamodb", region_name="eu-central-1", aws_access_key_id="x", aws_secret_access_key="x"
    )
    with Stubber(client) as stubber:
        client.stubber = stubber
        yield client
        stubber.assert_no_pending_responses()


def _condition_failed(client, item=None):
    fields = {"Item": item} if item else None
    client.stubber.add_client_error(
        "update_item",
        service_error_code="ConditionalCheckFailedException",
        modeled_fields=fields,
    )


def test_mark_done(dynamodb):
    dynamodb.stubber.add_response("update_item", {})
    assert state.mark_done(dynamodb, "t", KEY) == "done"


def test_mark_done_twice_is_harmless(dynamodb):
    _condition_failed(dynamodb, {**ITEM_KEY, "status": {"S": "DONE"}})
    assert state.mark_done(dynamodb, "t", KEY) == "already_done"


def test_mark_done_without_item_raises(dynamodb):
    _condition_failed(dynamodb)
    with pytest.raises(dynamodb.exceptions.ConditionalCheckFailedException):
        state.mark_done(dynamodb, "t", KEY)


def test_mark_done_sends_expected_request(dynamodb):
    dynamodb.stubber.add_response(
        "update_item",
        {},
        expected_params={
            "TableName": "t",
            "Key": ITEM_KEY,
            "UpdateExpression": "SET #status = :done, done_at = :now",
            "ConditionExpression": "attribute_exists(PK) AND #status <> :done",
            "ExpressionAttributeNames": {"#status": "status"},
            "ExpressionAttributeValues": {":done": {"S": "DONE"}, ":now": ANY},
            "ReturnValuesOnConditionCheckFailure": "ALL_OLD",
        },
    )
    state.mark_done(dynamodb, "t", KEY)


def test_record_failure_before_last_attempt_keeps_status(dynamodb):
    dynamodb.stubber.add_response(
        "update_item",
        {},
        expected_params={
            "TableName": "t",
            "Key": ITEM_KEY,
            "UpdateExpression": "SET last_error = :error, receive_count = :count",
            "ConditionExpression": "attribute_exists(PK) AND #status <> :done",
            "ExpressionAttributeNames": {"#status": "status"},
            "ExpressionAttributeValues": {
                ":error": {"S": "ValueError: boom"},
                ":count": {"N": "4"},
                ":done": {"S": "DONE"},
            },
        },
    )
    state.record_failure(dynamodb, "t", KEY, 4, ValueError("boom"))


def test_record_failure_on_last_attempt_dead_letters(dynamodb):
    dynamodb.stubber.add_response(
        "update_item",
        {},
        expected_params={
            "TableName": "t",
            "Key": ITEM_KEY,
            "UpdateExpression": (
                "SET last_error = :error, receive_count = :count, #status = :dead"
            ),
            "ConditionExpression": "attribute_exists(PK) AND #status <> :done",
            "ExpressionAttributeNames": {"#status": "status"},
            "ExpressionAttributeValues": {
                ":error": {"S": "ValueError: boom"},
                ":count": {"N": "5"},
                ":done": {"S": "DONE"},
                ":dead": {"S": "DEAD_LETTERED"},
            },
        },
    )
    state.record_failure(dynamodb, "t", KEY, 5, ValueError("boom"))


def test_record_failure_ignores_done_or_missing_item(dynamodb):
    _condition_failed(dynamodb)
    state.record_failure(dynamodb, "t", KEY, 1, ValueError("boom"))  # must not raise


def test_long_errors_are_truncated(dynamodb):
    dynamodb.stubber.add_response("update_item", {})
    state.record_failure(dynamodb, "t", KEY, 1, ValueError("x" * 5000))
