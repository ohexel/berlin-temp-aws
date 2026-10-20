import json
from datetime import UTC, datetime

from berlin_temp.pipeline import checker
from berlin_temp.pipeline.forecast import LEAD_COUNT, format_lead

NOW = datetime(2026, 10, 19, 14, 37, tzinfo=UTC)
RUN_14, RUN_13, RUN_12 = "2026-10-19T14:00Z", "2026-10-19T13:00Z", "2026-10-19T12:00Z"


class FakeDynamo:
    """In-memory stand-in for the DynamoDB calls the checker uses."""

    def __init__(self, done=None, meta=None):
        self.done = done or {}  # run label -> number of DONE leads
        self.meta = {label: dict(item) for label, item in (meta or {}).items()}

    def get_item(self, *, TableName, Key, ProjectionExpression, ConsistentRead):  # noqa: N803
        assert Key["SK"] == {"S": "meta"} and ConsistentRead is True
        label = Key["PK"]["S"].removeprefix("run#")
        return {"Item": self.meta[label]} if label in self.meta else {}

    def query(self, **kwargs):
        assert kwargs["ConsistentRead"] is True and kwargs["Select"] == "COUNT"
        label = kwargs["ExpressionAttributeValues"][":pk"]["S"].removeprefix("run#")
        return {"Count": self.done.get(label, 0)}

    def update_item(self, *, TableName, Key, UpdateExpression, **kwargs):  # noqa: N803
        label = Key["PK"]["S"].removeprefix("run#")
        item = self.meta.setdefault(label, {})
        item.setdefault("finalize_requested_at", kwargs["ExpressionAttributeValues"][":now"])


class FakeSqs:
    def __init__(self, fail=False):
        self.messages: list[dict] = []
        self.fail = fail

    def send_message(self, *, QueueUrl, MessageBody):  # noqa: N803
        if self.fail:
            raise OSError("queue unavailable")
        self.messages.append(json.loads(MessageBody))


def tick(dynamo, sqs, now=NOW):
    return checker.run_checker(dynamo, sqs, "table", "url", now=now)


def test_window_is_current_hour_and_two_before_newest_first():
    assert [run.hour for run in checker.window_runs(NOW)] == [14, 13, 12]


def test_window_crosses_midnight():
    runs = checker.window_runs(datetime(2026, 10, 20, 0, 5, tzinfo=UTC))
    assert [(run.day, run.hour) for run in runs] == [(20, 0), (19, 23), (19, 22)]


def test_complete_run_requests_finalize_once_then_never_again():
    dynamo, sqs = FakeDynamo(done={RUN_14: LEAD_COUNT}), FakeSqs()
    first = tick(dynamo, sqs)
    assert sqs.messages == [{"run_time": RUN_14}]
    assert first["finalize_requested"] == [RUN_14]
    assert first["skipped"]["incomplete"] == 2
    assert dynamo.meta[RUN_14]["finalize_requested_at"] == {"S": "2026-10-19T14:37:00+00:00"}

    second = tick(dynamo, sqs)
    assert len(sqs.messages) == 1
    assert second["finalize_requested"] == [] and second["skipped"]["already_requested"] == 1


def test_incomplete_run_sends_nothing():
    dynamo, sqs = FakeDynamo(done={RUN_14: LEAD_COUNT - 1}), FakeSqs()
    summary = tick(dynamo, sqs)
    assert sqs.messages == [] and RUN_14 not in dynamo.meta
    assert summary["skipped"]["incomplete"] == 3


def test_published_run_is_skipped_without_counting_leads():
    dynamo = FakeDynamo(done={RUN_14: LEAD_COUNT}, meta={RUN_14: {"published_at": {"S": "x"}}})
    sqs = FakeSqs()
    summary = tick(dynamo, sqs)
    assert sqs.messages == [] and summary["skipped"]["published"] == 1


def test_several_complete_runs_each_get_one_message_newest_first():
    dynamo = FakeDynamo(done={RUN_14: LEAD_COUNT, RUN_12: LEAD_COUNT})
    sqs = FakeSqs()
    tick(dynamo, sqs)
    assert [m["run_time"] for m in sqs.messages] == [RUN_14, RUN_12]


def test_failed_send_records_nothing_and_next_tick_retries(caplog):
    dynamo = FakeDynamo(done={RUN_14: LEAD_COUNT})
    summary = tick(dynamo, FakeSqs(fail=True))
    assert summary["finalize_requested"] == [] and summary["skipped"]["send_failed"] == 1
    assert RUN_14 not in dynamo.meta
    assert "queue unavailable" in caplog.text

    sqs = FakeSqs()
    tick(dynamo, sqs)
    assert sqs.messages == [{"run_time": RUN_14}]


def test_lead_count_matches_the_forecast_horizon():
    assert LEAD_COUNT == 25 and format_lead(24) == "PT024H00M"
