import json

from berlin_temp.pipeline import checker_handler


def test_handler_passes_environment_and_returns_summary(monkeypatch, caplog):
    seen = {}

    def fake_run(dynamodb, sqs, table, queue_url):
        seen.update(clients=(dynamodb, sqs), table=table, queue_url=queue_url)
        return {"finalize_requested": ["x"]}

    monkeypatch.setenv("STATE_TABLE", "table")
    monkeypatch.setenv("FINALIZE_QUEUE_URL", "https://sqs/finalize")
    monkeypatch.setattr(checker_handler.boto3, "client", lambda name: name)
    monkeypatch.setattr(checker_handler, "run_checker", fake_run)
    caplog.set_level("INFO", logger=checker_handler.LOGGER.name)

    result = checker_handler.handler({}, None)

    assert seen == {
        "clients": ("dynamodb", "sqs"),
        "table": "table",
        "queue_url": "https://sqs/finalize",
    }
    assert result["finalize_requested"] == ["x"] and "seconds" in result
    assert json.loads(caplog.records[-1].getMessage())["finalize_requested"] == ["x"]
