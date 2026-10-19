import json

import pytest

from berlin_temp.pipeline import finalize_handler


def test_handler(monkeypatch):
    seen = {}

    def fake_run(s3, dynamodb, data_bucket, dashboard_bucket, table, run_time):
        seen.update(buckets=(data_bucket, dashboard_bucket), table=table, run=run_time.isoformat())
        return {"outcome": "published"}

    for name, value in (("DATA_BUCKET", "d"), ("DASHBOARD_BUCKET", "w"), ("STATE_TABLE", "t")):
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(finalize_handler.boto3, "client", lambda name: name)
    monkeypatch.setattr(finalize_handler, "run_finalize", fake_run)

    event = {"Records": [{"body": json.dumps({"run_time": "2026-10-19T14:00Z"})}]}
    assert finalize_handler.handler(event, None) == {"outcome": "published"}
    assert seen == {"buckets": ("d", "w"), "table": "t", "run": "2026-10-19T14:00:00+00:00"}


def test_exactly_one_record():
    with pytest.raises(ValueError, match="exactly 1"):
        finalize_handler.handler({"Records": []}, None)
