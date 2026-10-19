import io
import json
from datetime import UTC, datetime, timedelta

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from berlin_temp.pipeline import finalize
from berlin_temp.pipeline.forecast import LEAD_COUNT, ForecastKey

RUN = datetime(2026, 10, 19, 14, 0, tzinfo=UTC)
NOW = datetime(2026, 10, 19, 14, 52, tzinfo=UTC)
MASK = [10, 11, 12, 13]
# PLR "A" = half cell 10, half cell 11; "B" = cell 12; "C" = quarter 12, three quarters 13.
BRIDGE = [("A", 10, 0.5), ("A", 11, 0.5), ("B", 12, 1.0), ("C", 12, 0.25), ("C", 13, 0.75)]


def _parquet(table: pa.Table) -> bytes:
    buffer = io.BytesIO()
    pq.write_table(table, buffer)
    return buffer.getvalue()


def temperature(cell: int, hours: int) -> float:
    return 10.0 + hours + cell * 0.1


def apparent(cell: int, hours: int) -> float:
    bump = 3.0 if (cell == 13 and hours in (5, 9)) else 0.0  # C's widest gap: tie at 5 and 9
    return temperature(cell, hours) - 2.0 + bump


class NoSuchKey(Exception):
    pass


class FakeS3:
    class exceptions:  # noqa: N801
        NoSuchKey = NoSuchKey

    def __init__(self):
        self.objects: dict[tuple[str, str], dict] = {}
        self.order: list[str] = []

    def get_object(self, *, Bucket, Key):  # noqa: N803
        if (Bucket, Key) not in self.objects:
            raise NoSuchKey(Key)
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)]["Body"])}

    def put_object(self, *, Bucket, Key, Body, **extra):  # noqa: N803
        self.objects[(Bucket, Key)] = {"Body": bytes(Body), **extra}
        self.order.append(Key)

    def json(self, bucket, key):
        return json.loads(self.objects[(bucket, key)]["Body"])


class FakeDynamo:
    def __init__(self, published=False):
        self.published = published
        self.updates = []

    def get_item(self, **kwargs):
        return {"Item": {"published_at": {"S": "x"}}} if self.published else {}

    def update_item(self, **kwargs):
        self.updates.append(kwargs)


def silver_s3(skip_lead=None, extra_cell=False) -> FakeS3:
    s3 = FakeS3()
    put = lambda key, table: s3.put_object(Bucket="data", Key=key, Body=_parquet(table))  # noqa: E731
    put("silver/mask/part.parquet", pa.table({"cell_index": pa.array(MASK, type=pa.int32())}))
    put(
        "silver/bridge/part.parquet",
        pa.table(
            {
                "plr_id": [b[0] for b in BRIDGE],
                "cell_index": pa.array([b[1] for b in BRIDGE], type=pa.int32()),
                "fraction_of_plr": [b[2] for b in BRIDGE],
            }
        ),
    )
    put(
        "silver/plr/part.parquet",
        pa.table({"plr_id": ["A", "B", "C"], "plr_name": ["Aa", "Bb", "Cc"]}),
    )
    put(
        "silver/population/part.parquet",
        pa.table(
            {
                "plr_id": ["A", "B"],
                "population_total": pa.array([1000, None], type=pa.int64()),
                "population_65plus": pa.array([250, 7], type=pa.int64()),
                "rejection_reason": [None, "invalid_population_total"],
            }
        ),
    )
    timestamp = pa.timestamp("s", tz="UTC")
    for hours in range(LEAD_COUNT):
        if hours == skip_lead:
            continue
        key = ForecastKey(RUN, hours)
        cells = MASK + ([99] if extra_cell else [])
        put(
            f"silver/forecast_cell/{key.partition}/part.parquet",
            pa.table(
                {
                    "run_time_utc": pa.array([RUN] * len(cells), type=timestamp),
                    "lead": [key.lead_label] * len(cells),
                    "valid_time_utc": pa.array([key.valid_time] * len(cells), type=timestamp),
                    "cell_index": pa.array(cells, type=pa.int32()),
                    "temperature_c": [temperature(c, hours) for c in cells],
                    "apparent_temperature_c": [apparent(c, hours) for c in cells],
                }
            ),
        )
    return s3


def run(s3, dynamo=None):
    return finalize.run_finalize(
        s3, dynamo or FakeDynamo(), "data", "dash", "table", RUN, now=NOW, expected_plr_count=3
    )


def test_publishes_gold_and_dashboard():
    s3, dynamo = silver_s3(), FakeDynamo()
    result = run(s3, dynamo)
    assert result["outcome"] == "published" and result["plr_rows"] == 75 and result["areas"] == 3

    gold = pq.read_table(
        io.BytesIO(
            s3.objects[("data", "gold/forecast_plr/run=20261019T1400Z/part.parquet")]["Body"]
        )
    ).to_pandas()
    a0 = gold[(gold.plr_id == "A") & (gold.lead == "PT000H00M")].iloc[0]
    assert a0.temperature_c == pytest.approx((temperature(10, 0) + temperature(11, 0)) / 2)
    c3 = gold[(gold.plr_id == "C") & (gold.lead == "PT003H00M")].iloc[0]
    assert c3.temperature_c == pytest.approx(0.25 * temperature(12, 3) + 0.75 * temperature(13, 3))

    summary = s3.json("dash", "data/runs/20261019T1400Z/summary.json")
    assert summary["format_version"] == 3
    assert summary["run_time_berlin"] == "2026-10-19T16:00"  # CEST, UTC+2
    areas = {area["id"]: area for area in summary["areas"]}
    assert areas["A"]["maximum_temperature_c"] == round(
        (temperature(10, 24) + temperature(11, 24)) / 2, 2
    )
    assert areas["A"]["maximum_temperature_at_berlin"] == "2026-10-20T16:00"
    # C: apparent - air = -2 + 0.75 * 3 = +0.25 at leads 5 and 9; the earliest wins.
    assert areas["C"]["maximum_apparent_temperature_difference_c"] == 0.25
    assert areas["C"]["maximum_apparent_temperature_difference_at_berlin"] == "2026-10-19T21:00"
    assert (areas["A"]["population_status"], areas["A"]["population_65plus"]) == ("available", 250)
    assert areas["B"]["population_status"] == "rejected_source_record"
    assert areas["B"]["population_65plus"] is None  # rejected counts are never shown
    assert areas["C"]["population_status"] == "missing_source_record"

    detail = s3.json("dash", "data/runs/20261019T1400Z/areas/B.json")
    assert detail["plr_name"] == "Bb"
    assert (
        len(detail["valid_times_berlin"]) == 25 == len(detail["forecast_apparent_temperatures_c"])
    )
    assert detail["forecast_temperatures_c"][0] == round(temperature(12, 0), 2)

    pointer = s3.json("dash", "data/current.json")
    assert pointer["run"] == "20261019T1400Z"
    assert s3.order[-1] == "data/current.json"  # the pointer is switched last
    assert s3.objects[("dash", "data/current.json")]["CacheControl"] == "public, max-age=60"
    assert (
        "immutable" in s3.objects[("dash", "data/runs/20261019T1400Z/summary.json")]["CacheControl"]
    )
    assert dynamo.updates[0]["Key"]["SK"] == {"S": "meta"}

    summary_gold = pq.read_table(
        io.BytesIO(
            s3.objects[("data", "gold/forecast_plr_summary/run=20261019T1400Z/part.parquet")][
                "Body"
            ]
        )
    )
    assert summary_gold.num_rows == 3
    assert summary_gold.schema.field("population_65plus").type == pa.int64()


def test_duplicate_message_stops_before_reading():
    s3 = silver_s3()
    before = len(s3.order)
    assert run(s3, FakeDynamo(published=True))["outcome"] == "duplicate"
    assert len(s3.order) == before


def test_older_run_never_replaces_a_newer_one():
    s3 = silver_s3()
    s3.put_object(
        Bucket="dash", Key="data/current.json", Body=json.dumps({"run": "20261019T1500Z"}).encode()
    )
    before = len(s3.order)
    result = run(s3)
    assert result == {
        "run_time": "2026-10-19T14:00Z",
        "outcome": "stale",
        "published_run": "20261019T1500Z",
    }
    assert len(s3.order) == before


def test_same_run_in_pointer_is_republished():
    s3 = silver_s3()
    s3.put_object(
        Bucket="dash", Key="data/current.json", Body=json.dumps({"run": "20261019T1400Z"}).encode()
    )
    assert run(s3)["outcome"] == "published"


def test_missing_lead_fails_without_publishing():
    s3 = silver_s3(skip_lead=7)
    with pytest.raises(finalize.FinalizeError, match="PT007H00M: missing"):
        run(s3)
    assert not any(bucket == "dash" for bucket, _ in s3.objects)
    assert not any(key.startswith("gold/") for _, key in s3.objects)


def test_cells_outside_the_mask_fail():
    with pytest.raises(finalize.FinalizeError, match="do not match"):
        run(silver_s3(extra_cell=True))


def test_wrong_plr_count_fails():
    s3 = silver_s3()
    with pytest.raises(finalize.FinalizeError, match="expected 4 PLRs"):
        finalize.run_finalize(
            s3, FakeDynamo(), "data", "dash", "t", RUN, now=NOW, expected_plr_count=4
        )


def test_berlin_time_across_the_autumn_clock_change():
    run_time = datetime(2026, 10, 25, 0, 0, tzinfo=UTC)  # clocks go back at 01:00 UTC
    stamps = [finalize._berlin(run_time + timedelta(hours=h)) for h in range(3)]
    assert stamps == ["2026-10-25T02:00", "2026-10-25T02:00", "2026-10-25T03:00"]
