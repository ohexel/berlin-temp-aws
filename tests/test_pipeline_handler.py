import json

import numpy as np
import pytest

from berlin_temp.pipeline import handler

MESSAGE = {"run_time": "2026-10-13T12:00Z", "lead": "PT005H00M"}


def _event(body=MESSAGE, count="1"):
    return {
        "Records": [{"body": json.dumps(body), "attributes": {"ApproximateReceiveCount": count}}]
    }


@pytest.fixture
def wired(monkeypatch):
    calls = []
    monkeypatch.setenv("DATA_BUCKET", "bucket")
    monkeypatch.setenv("STATE_TABLE", "table")
    monkeypatch.setattr(handler.boto3, "client", lambda name: name)
    monkeypatch.setattr(handler, "_mask_cache", {})
    monkeypatch.setattr(handler, "_state", {"cold_start": True})
    monkeypatch.setattr(
        handler, "load_mask", lambda s3, bucket: calls.append("mask") or np.array([1])
    )
    monkeypatch.setattr(
        handler,
        "run_ingest_lead",
        lambda *a, **k: (
            calls.append("ingest") or {"cells": 1, "seconds": {"download_and_decode": 0.5}}
        ),
    )
    monkeypatch.setattr(
        handler.state, "mark_done", lambda ddb, table, key: calls.append("done") or "done"
    )
    monkeypatch.setattr(
        handler.state,
        "record_failure",
        lambda ddb, table, key, count, error: calls.append(("failure", count, str(error))),
    )
    return calls


def test_success(wired):
    result = handler.handler(_event(), None)
    assert result == {"cells": 1, "seconds": {"download_and_decode": 0.5}, "state": "done"}
    assert wired == ["mask", "ingest", "done"]


def test_mask_is_loaded_once_per_warm_lambda(wired):
    handler.handler(_event(), None)
    handler.handler(_event(), None)
    assert wired.count("mask") == 1


def test_failure_is_recorded_with_receive_count_and_reraised(wired, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("dwd said no")

    monkeypatch.setattr(handler, "run_ingest_lead", boom)
    with pytest.raises(RuntimeError, match="dwd said no"):
        handler.handler(_event(count="3"), None)
    assert ("failure", 3, "dwd said no") in wired
    assert "done" not in wired


def test_state_write_failure_does_not_mask_the_original_error(wired, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("original")

    def broken_state(*args, **kwargs):
        raise OSError("dynamodb down")

    monkeypatch.setattr(handler, "run_ingest_lead", boom)
    monkeypatch.setattr(handler.state, "record_failure", broken_state)
    with pytest.raises(RuntimeError, match="original"):
        handler.handler(_event(), None)


def test_manual_event_without_attributes_counts_as_first_attempt(wired):
    event = {"Records": [{"body": json.dumps(MESSAGE)}]}
    assert handler.handler(event, None)["state"] == "done"


def test_exactly_one_record_required(wired):
    with pytest.raises(ValueError, match="exactly 1"):
        handler.handler({"Records": []}, None)


def test_bad_message_fails_without_touching_dynamodb(wired):
    with pytest.raises(ValueError):
        handler.handler(_event({"run_time": "2026-10-13T12:00Z", "lead": "PT005H"}), None)
    assert wired == []


def test_log_line_has_phase_timings_and_cold_start_flag(wired, caplog):
    caplog.set_level("INFO", logger=handler.LOGGER.name)
    handler.handler(_event(), None)
    handler.handler(_event(), None)
    lines = [json.loads(r.getMessage()) for r in caplog.records if r.getMessage().startswith("{")]
    first, second = lines
    assert (first["cold_start"], second["cold_start"]) == (True, False)
    assert set(first["phases"]) == {"mask_load", "download_and_decode", "state_update"}
    assert set(second["phases"]) == {"download_and_decode", "state_update"}  # mask is cached
    assert first["outcome"] == "done" and first["receive_count"] == 1
