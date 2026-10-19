from datetime import UTC, datetime

import pytest

from berlin_temp.pipeline.forecast import ForecastKey, format_lead, parse_lead

RUN = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)


def test_lead_labels_are_dwd_native():
    assert format_lead(5) == "PT005H00M"
    assert parse_lead("PT024H00M") == 24


@pytest.mark.parametrize("label", ["PT005H", "PT5H00M", "PT005H30M", "PT025H00M", "pt005h00m", ""])
def test_bad_lead_labels(label):
    with pytest.raises(ValueError):
        parse_lead(label)


def test_key_from_message():
    key = ForecastKey.from_message({"run_time": "2026-10-13T12:00Z", "lead": "PT005H00M"})
    assert key == ForecastKey(RUN, 5)
    assert key.run_label == "2026-10-13T12:00Z"
    assert key.valid_time == datetime(2026, 10, 13, 17, 0, tzinfo=UTC)
    assert key.partition == "run=20261013T1200Z/lead=PT005H00M"


def test_field_url_matches_dwd_layout():
    assert ForecastKey(RUN, 5).field_url("T_2M") == (
        "https://opendata.dwd.de/weather/nwp/v1/m/icon-d2-ruc/p/T_2M/r/2026-10-13T12:00/s/PT005H00M.grib2"
    )
    with pytest.raises(ValueError):
        ForecastKey(RUN, 5).field_url("TD_2M")


def test_naive_run_time_rejected():
    with pytest.raises(ValueError, match="UTC"):
        ForecastKey(datetime(2026, 10, 13, 12, 0), 5)


def test_message_without_z_rejected():
    with pytest.raises(ValueError):
        ForecastKey.from_message({"run_time": "2026-10-13T12:00", "lead": "PT005H00M"})
