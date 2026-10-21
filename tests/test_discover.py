import json
import urllib.error
from datetime import UTC, datetime

import pytest

from berlin_temp.pipeline import discover
from berlin_temp.pipeline.forecast import ForecastKey, format_lead

NOW = datetime(2026, 10, 19, 14, 37, tzinfo=UTC)


def at(hour: int, day: int = 19) -> datetime:
    return datetime(2026, 10, day, hour, 0, tzinfo=UTC)


class ConditionFailed(Exception):
    response: dict = {}


class FakeDynamo:
    """In-memory stand-in for the three DynamoDB calls discover uses."""

    class exceptions:  # noqa: N801 (mirrors the boto3 client attribute)
        ConditionalCheckFailedException = ConditionFailed

    def __init__(self, existing=()):
        self.items: dict[tuple[str, str], dict] = {}
        for run_label, lead in existing:
            self.items[(f"run#{run_label}", f"lead#{lead}")] = {"status": {"S": "DONE"}}
        self.queries: list[str] = []

    def query(self, **kwargs):
        pk = kwargs["ExpressionAttributeValues"][":pk"]["S"]
        self.queries.append(pk)
        found = [{"SK": {"S": sk}} for (item_pk, sk) in self.items if item_pk == pk]
        return {"Items": found}

    def put_item(self, *, TableName, Item, ConditionExpression):  # noqa: N803
        assert ConditionExpression == "attribute_not_exists(PK)"
        key = (Item["PK"]["S"], Item["SK"]["S"])
        if key in self.items:
            raise ConditionFailed
        self.items[key] = Item

    def delete_item(self, *, TableName, Key):  # noqa: N803
        del self.items[(Key["PK"]["S"], Key["SK"]["S"])]


class FakeSqs:
    def __init__(self, fail_on=None):
        self.messages: list[dict] = []
        self.fail_on = fail_on

    def send_message(self, *, QueueUrl, MessageBody):  # noqa: N803
        body = json.loads(MessageBody)
        if body == self.fail_on:
            raise OSError("queue unavailable")
        self.messages.append(body)


def run(dynamo, sqs, runs, ready=lambda key: True, now=NOW):
    return discover.run_discover(
        dynamo, sqs, "table", "url", now=now, runs_fn=lambda: runs, ready_fn=ready
    )


def test_first_tick_queues_the_first_five_leads_of_the_newest_run():
    dynamo, sqs = FakeDynamo(), FakeSqs()
    summary = run(dynamo, sqs, [at(14), at(13), at(12)])
    assert [m["lead"] for m in sqs.messages] == [format_lead(h) for h in range(5)]
    assert {m["run_time"] for m in sqs.messages} == {"2026-10-19T14:00Z"}
    assert summary["cap_reached"] is True
    assert len(dynamo.items) == 5
    item = dynamo.items[("run#2026-10-19T14:00Z", "lead#PT000H00M")]
    assert item["status"] == {"S": "QUEUED"}
    assert int(item["ttl"]["N"]) == int(NOW.timestamp()) + 7 * 24 * 3600


def test_cap_stops_before_older_runs_are_even_queried():
    dynamo = FakeDynamo()
    run(dynamo, FakeSqs(), [at(14), at(13)])
    assert dynamo.queries == ["run#2026-10-19T14:00Z"]


def test_second_tick_continues_where_the_first_stopped():
    dynamo, sqs = FakeDynamo(), FakeSqs()
    run(dynamo, sqs, [at(14)])
    sqs.messages.clear()
    run(dynamo, sqs, [at(14)])
    assert [m["lead"] for m in sqs.messages] == [format_lead(h) for h in range(5, 10)]


def test_known_leads_cause_no_requests_to_dwd():
    known = [("2026-10-19T14:00Z", format_lead(h)) for h in range(25)]
    checked = []
    dynamo, sqs = FakeDynamo(known), FakeSqs()
    summary = run(dynamo, sqs, [at(14)], ready=lambda key: checked.append(key) or True)
    assert checked == [] and sqs.messages == []
    assert summary["skipped"]["already_known"] == 25


def test_leads_not_ready_are_skipped_and_older_runs_fill_the_cap():
    def ready(key):
        return key.run_time == at(14) and key.lead_hours < 3 or key.run_time == at(13)

    sqs = FakeSqs()
    summary = run(FakeDynamo(), sqs, [at(14), at(13)], ready=ready)
    assert [(m["run_time"], m["lead"]) for m in sqs.messages] == [
        ("2026-10-19T14:00Z", "PT000H00M"),
        ("2026-10-19T14:00Z", "PT001H00M"),
        ("2026-10-19T14:00Z", "PT002H00M"),
        ("2026-10-19T13:00Z", "PT000H00M"),
        ("2026-10-19T13:00Z", "PT001H00M"),
    ]
    assert summary["skipped"]["not_ready"] == 22


def test_nothing_ready_queues_nothing():
    dynamo, sqs = FakeDynamo(), FakeSqs()
    summary = run(dynamo, sqs, [at(14)], ready=lambda key: False)
    assert sqs.messages == [] and dynamo.items == {} and summary["cap_reached"] is False


def test_only_runs_in_the_window_are_considered_at_most_three():
    runs = [at(15), at(14), at(13), at(12), at(11), at(10, day=18)]
    summary = run(FakeDynamo(), FakeSqs(), runs, ready=lambda key: False)
    assert summary["runs_considered"] == [
        "2026-10-19T14:00Z",
        "2026-10-19T13:00Z",
        "2026-10-19T12:00Z",
    ]


def test_window_edges():
    summary = run(
        FakeDynamo(), FakeSqs(), [at(14), at(12), at(11)], ready=lambda k: False, now=at(14)
    )
    assert summary["runs_considered"] == ["2026-10-19T14:00Z", "2026-10-19T12:00Z"]


def test_no_runs_listed():
    summary = run(FakeDynamo(), FakeSqs(), [])
    assert summary["queued"] == [] and summary["runs_considered"] == []


def test_lost_race_is_skipped_without_a_message():
    class RacyDynamo(FakeDynamo):
        def query(self, **kwargs):  # the item appears after the query, before the put
            response = super().query(**kwargs)
            self.items[("run#2026-10-19T14:00Z", "lead#PT000H00M")] = {}
            return response

    sqs = FakeSqs()
    summary = run(RacyDynamo(), sqs, [at(14)])
    assert summary["skipped"]["lost_race"] == 1
    assert [m["lead"] for m in sqs.messages] == [format_lead(h) for h in range(1, 6)]


def test_send_failure_removes_the_item_and_fails_the_tick():
    dynamo = FakeDynamo()
    sqs = FakeSqs(fail_on={"run_time": "2026-10-19T14:00Z", "lead": "PT001H00M"})
    with pytest.raises(discover.DiscoverError, match="PT001H00M"):
        run(dynamo, sqs, [at(14)])
    assert ("run#2026-10-19T14:00Z", "lead#PT001H00M") not in dynamo.items
    assert ("run#2026-10-19T14:00Z", "lead#PT000H00M") in dynamo.items  # earlier success stays


def test_list_runs_parses_encoded_and_plain_labels(monkeypatch):
    html = (
        b'<a href="2026-10-19T13%3A00/">x</a> <a href="2026-10-19T14:00/">y</a>'
        b'<a href="2026-10-19T13:00/">dup</a> <a href="../">up</a>'
    )
    monkeypatch.setattr(discover.sources, "fetch", lambda url, timeout: (html, None))
    assert discover.list_runs() == [at(14), at(13)]


def test_list_runs_uses_the_short_timeout(monkeypatch):
    seen = []
    monkeypatch.setattr(
        discover.sources, "fetch", lambda url, timeout: seen.append(timeout) or (b"", None)
    )
    discover.list_runs()
    assert seen == [discover.LIST_TIMEOUT_SECONDS] == [15]


class _Response:
    def __init__(self, status):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        (_Response(200), True),
        (urllib.error.HTTPError("u", 404, "nf", None, None), False),
        (urllib.error.HTTPError("u", 503, "busy", None, None), False),
        (urllib.error.URLError("dns"), False),
        (TimeoutError(), False),
    ],
)
def test_file_exists(monkeypatch, outcome, expected):
    def fake_urlopen(request, timeout):
        assert request.get_method() == "HEAD"
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(discover.urllib.request, "urlopen", fake_urlopen)
    assert discover.file_exists("https://example.org/x") is expected


def test_file_exists_retries_a_reset_and_then_succeeds(monkeypatch):
    attempts = []

    def fake_urlopen(request, timeout):
        attempts.append(1)
        if len(attempts) < 3:
            raise urllib.error.URLError(ConnectionResetError(104, "reset"))
        return _Response(200)

    monkeypatch.setattr(discover.urllib.request, "urlopen", fake_urlopen)
    assert discover.file_exists("https://example.org/x") is True
    assert len(attempts) == 3


def test_file_exists_after_three_resets_is_not_ready_and_logged(monkeypatch, caplog):
    attempts = []

    def fake_urlopen(request, timeout):
        attempts.append(1)
        raise ConnectionResetError(104, "reset")

    monkeypatch.setattr(discover.urllib.request, "urlopen", fake_urlopen)
    assert discover.file_exists("https://example.org/x") is False
    assert len(attempts) == 3
    assert "treating as not ready" in caplog.text


def test_file_exists_does_not_retry_404(monkeypatch):
    attempts = []

    def fake_urlopen(request, timeout):
        attempts.append(1)
        raise urllib.error.HTTPError("u", 404, "nf", None, None)

    monkeypatch.setattr(discover.urllib.request, "urlopen", fake_urlopen)
    assert discover.file_exists("https://example.org/x") is False
    assert len(attempts) == 1


def test_head_requests_use_four_parallel_workers():
    assert discover.HEAD_WORKERS == 4


def test_unexpected_http_errors_are_logged(monkeypatch, caplog):
    def fake_urlopen(request, timeout):
        raise urllib.error.HTTPError("u", 503, "busy", None, None)

    monkeypatch.setattr(discover.urllib.request, "urlopen", fake_urlopen)
    discover.file_exists("https://example.org/x")
    assert "503" in caplog.text


def test_lead_ready_needs_all_four_files(monkeypatch):
    present = {"T_2M", "RELHUM_2M", "U_10M"}
    monkeypatch.setattr(discover, "file_exists", lambda url: any(f"/{n}/" in url for n in present))
    assert discover.lead_ready(ForecastKey(at(14), 0)) is False
    present.add("V_10M")
    assert discover.lead_ready(ForecastKey(at(14), 0)) is True
