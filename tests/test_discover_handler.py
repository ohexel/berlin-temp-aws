import json

from berlin_temp.pipeline import discover_handler


def test_handler_passes_environment_and_returns_summary(monkeypatch, caplog):
    seen = {}

    def fake_run(dynamodb, sqs, table, queue_url):
        seen.update(clients=(dynamodb, sqs), table=table, queue_url=queue_url)
        return {"queued": ["x"]}

    monkeypatch.setenv("STATE_TABLE", "table")
    monkeypatch.setenv("QUEUE_URL", "https://sqs/queue")
    monkeypatch.setattr(discover_handler.boto3, "client", lambda name: name)
    monkeypatch.setattr(discover_handler, "run_discover", fake_run)
    caplog.set_level("INFO", logger=discover_handler.LOGGER.name)

    result = discover_handler.handler({}, None)

    assert seen == {
        "clients": ("dynamodb", "sqs"),
        "table": "table",
        "queue_url": "https://sqs/queue",
    }
    assert result["queued"] == ["x"] and "seconds" in result
    assert json.loads(caplog.records[-1].getMessage())["queued"] == ["x"]
