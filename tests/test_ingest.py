import io
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from synthetic_grib import NI, NJ, eccodes_available, write_grib

from berlin_temp.pipeline import ingest, weather
from berlin_temp.pipeline.forecast import INDICATORS, ForecastKey
from berlin_temp.reference import sources

pytestmark = pytest.mark.skipif(not eccodes_available(), reason="ecCodes C library not installed")

RUN = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
KEY = ForecastKey(RUN, 5)
POINTS = NI * NJ
MASK = np.array([0, 7, 59])
FIELDS = {  # full-grid test values per indicator
    "T_2M": 280.0 + np.arange(POINTS) * 0.1,
    "RELHUM_2M": 40.0 + np.arange(POINTS) * 0.5,
    "U_10M": 1.0 + np.arange(POINTS) * 0.05,
    "V_10M": -2.0 + np.arange(POINTS) * 0.02,
}


class FakeS3:
    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def put_object(self, *, Bucket, Key, Body, **_):  # noqa: N803 (boto3 signature)
        assert Bucket == "bucket"
        self.objects[Key] = bytes(Body)

    def upload_file(self, filename, bucket, key):
        assert bucket == "bucket"
        self.objects[key] = Path(filename).read_bytes()


def _downloads(missing_at=None, calls=None) -> ingest.Downloads:
    def grib(url: str, target: Path) -> sources.FetchInfo:
        indicator = next(name for name in INDICATORS if f"/{name}/" in url)
        if calls is not None:
            calls.append(url)
        gaps = (missing_at or {}).get(indicator, ())
        write_grib(target, indicator, RUN, 5, FIELDS[indicator], missing_at=gaps)
        return sources.FetchInfo(
            url, url, 200, "application/octet-stream", "ab" * 32, target.stat().st_size, "t"
        )

    return ingest.Downloads(grib=grib)


def _run(s3, **kwargs):
    return ingest.run_ingest_lead(
        s3,
        "bucket",
        KEY,
        MASK,
        downloads=kwargs.pop("downloads", _downloads()),
        expected_points=POINTS,
        **kwargs,
    )


def test_end_to_end(tmp_path):
    s3, calls = FakeS3(), []
    summary = _run(s3, downloads=_downloads(calls=calls), workdir=tmp_path)

    assert calls == [KEY.field_url(name) for name in INDICATORS]
    for name in INDICATORS:
        key = f"bronze/forecast/run=20261013T1200Z/lead=PT005H00M/{name}.grib2"
        assert s3.objects[key].startswith(b"GRIB")
        assert key + ".meta.json" in s3.objects

    silver_key = "silver/forecast_cell/run=20261013T1200Z/lead=PT005H00M/part.parquet"
    assert summary["silver_key"] == silver_key
    table = pq.read_table(io.BytesIO(s3.objects[silver_key]))
    assert table.column_names == [
        "run_time_utc", "lead", "valid_time_utc", "cell_index", "temperature_c",
        "relative_humidity_percent", "wind_speed_10m_ms", "apparent_temperature_c",
    ]  # fmt: skip
    assert table.num_rows == 3
    assert table.column("cell_index").to_pylist() == [0, 7, 59]
    assert set(table.column("lead").to_pylist()) == {"PT005H00M"}
    assert table.column("run_time_utc").to_pylist()[0] == RUN
    assert table.column("valid_time_utc").to_pylist()[0] == KEY.valid_time

    # Values are decoded from GRIB (packing precision ~1e-2) and computed per cell.
    temperature = FIELDS["T_2M"][MASK] - 273.15
    wind = weather.wind_speed(FIELDS["U_10M"][MASK], FIELDS["V_10M"][MASK])
    apparent = weather.apparent_temperature_c(temperature, FIELDS["RELHUM_2M"][MASK], wind)
    np.testing.assert_allclose(table.column("temperature_c").to_numpy(), temperature, atol=0.05)
    np.testing.assert_allclose(table.column("wind_speed_10m_ms").to_numpy(), wind, atol=0.05)
    np.testing.assert_allclose(
        table.column("apparent_temperature_c").to_numpy(), apparent, atol=0.2
    )
    assert table.schema.field("cell_index").type == pa.int32()


def test_rerun_writes_the_same_keys():
    s3 = FakeS3()
    _run(s3)
    first = set(s3.objects)
    _run(s3)
    assert set(s3.objects) == first


def test_missing_value_inside_mask_fails_before_silver():
    s3 = FakeS3()
    with pytest.raises(ValueError, match="T_2M: 1 of 3 mask cells"):
        _run(s3, downloads=_downloads(missing_at={"T_2M": (7,)}))
    assert not any(key.startswith("silver/") for key in s3.objects)
    assert any(key.startswith("bronze/forecast/") for key in s3.objects)


def test_missing_value_outside_mask_is_fine():
    s3 = FakeS3()
    _run(s3, downloads=_downloads(missing_at={"T_2M": (1, 2)}))
    assert any(key.startswith("silver/") for key in s3.objects)


def test_mask_outside_grid_is_rejected():
    with pytest.raises(ValueError, match="mask"):
        ingest.run_ingest_lead(
            FakeS3(), "bucket", KEY, np.array([0, POINTS]),
            downloads=_downloads(), expected_points=POINTS,
        )  # fmt: skip
    with pytest.raises(ValueError, match="mask"):
        ingest.run_ingest_lead(
            FakeS3(), "bucket", KEY, np.array([], dtype=int),
            downloads=_downloads(), expected_points=POINTS,
        )  # fmt: skip


def test_download_grib_rejects_html(tmp_path, monkeypatch):
    def fake_fetch(url, target):
        target.write_bytes(b"<html>error</html>")
        return sources.FetchInfo(url, url, 200, "text/html", "ab" * 32, 18, "t")

    monkeypatch.setattr(sources, "fetch_to_file", fake_fetch)
    with pytest.raises(ValueError, match="did not return a GRIB file"):
        ingest.download_grib("https://example.org/x.grib2", tmp_path / "x.grib2")


def test_load_mask():
    buffer = io.BytesIO()
    pq.write_table(pa.table({"cell_index": pa.array([5, 9], type=pa.int32())}), buffer)

    class S3:
        def get_object(self, *, Bucket, Key):  # noqa: N803
            assert (Bucket, Key) == ("bucket", "silver/mask/part.parquet")
            return {"Body": io.BytesIO(buffer.getvalue())}

    assert ingest.load_mask(S3(), "bucket").tolist() == [5, 9]
