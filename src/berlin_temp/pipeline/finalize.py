"""finalize: silver forecast cells -> gold PLR tables -> dashboard publication."""

import io
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from berlin_temp.pipeline import state
from berlin_temp.pipeline.forecast import LEAD_COUNT, ForecastKey, run_key_label, run_label
from berlin_temp.pipeline.ingest import SILVER_PREFIX, load_mask

LOGGER = logging.getLogger(__name__)

EXPECTED_PLR_COUNT = 542
BERLIN = ZoneInfo("Europe/Berlin")
GOLD_FORECAST_PREFIX = "gold/forecast_plr"
GOLD_SUMMARY_PREFIX = "gold/forecast_plr_summary"
POINTER_KEY = "data/current.json"
RUNS_PREFIX = "data/runs"
FORMAT_VERSION = 3  # dashboard data format; version 2 was the old export with history
CACHE_IMMUTABLE = "public, max-age=31536000, immutable"  # run folders never change
CACHE_POINTER = "public, max-age=60"
PUT_WORKERS = 16
VALUES = ["temperature_c", "apparent_temperature_c"]


class FinalizeError(RuntimeError):
    """The run's data is incomplete or inconsistent; nothing was published."""


def _read_parquet(s3, bucket: str, key: str, columns: list[str] | None = None) -> pd.DataFrame:
    body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    return pq.read_table(io.BytesIO(body), columns=columns).to_pandas()


def read_forecast_cells(s3, bucket: str, run_time: datetime, mask: np.ndarray) -> pd.DataFrame:
    """All 25 silver lead files of one run; every check failure is reported together."""
    mask_cells = set(mask.tolist())
    frames, problems = [], []
    for hours in range(LEAD_COUNT):
        key = ForecastKey(run_time, hours)
        object_key = f"{SILVER_PREFIX}/{key.partition}/part.parquet"
        try:
            frame = _read_parquet(s3, bucket, object_key)
        except s3.exceptions.NoSuchKey:
            problems.append(f"{key.lead_label}: missing")
            continue
        if len(frame) != len(mask_cells) or set(frame["cell_index"]) != mask_cells:
            problems.append(
                f"{key.lead_label}: cells do not match the {len(mask_cells)} mask cells"
            )
        if (frame["run_time_utc"] != pd.Timestamp(run_time)).any() or (
            frame["lead"] != key.lead_label
        ).any():
            problems.append(f"{key.lead_label}: rows from another run or lead")
        if not np.isfinite(frame[VALUES].to_numpy()).all():
            problems.append(f"{key.lead_label}: values not finite")
        frames.append(frame)
    if problems:
        raise FinalizeError(f"run {run_label(run_time)} incomplete: " + "; ".join(problems))
    return pd.concat(frames, ignore_index=True)


def aggregate_to_plr(
    cells: pd.DataFrame, bridge: pd.DataFrame, expected_plr_count: int = EXPECTED_PLR_COUNT
) -> pd.DataFrame:
    """Area-weighted PLR values per lead (cell values times fraction_of_plr, summed)."""
    weights = bridge[["plr_id", "cell_index", "fraction_of_plr"]]
    merged = weights.merge(
        cells[["lead", "valid_time_utc", "cell_index", *VALUES]], on="cell_index", how="inner"
    )
    if len(merged) != len(weights) * LEAD_COUNT:
        raise FinalizeError("bridge cells missing from the forecast cells")
    for column in VALUES:
        merged[column] = merged[column] * merged["fraction_of_plr"]
    forecast = (
        merged.groupby(["plr_id", "lead", "valid_time_utc"], as_index=False)[VALUES]
        .sum()
        .sort_values(["plr_id", "lead"], ignore_index=True)
    )
    if (
        forecast["plr_id"].nunique() != expected_plr_count
        or len(forecast) != expected_plr_count * LEAD_COUNT
        or not np.isfinite(forecast[VALUES].to_numpy()).all()
    ):
        raise FinalizeError(
            f"expected {expected_plr_count} PLRs x {LEAD_COUNT} leads with finite values, "
            f"got {forecast['plr_id'].nunique()} PLRs and {len(forecast)} rows"
        )
    return forecast


def _first_by(frame: pd.DataFrame, column: str) -> pd.DataFrame:
    """Per PLR, the row with the highest value; ties go to the earliest hour."""
    ordered = frame.sort_values(["plr_id", column, "valid_time_utc"], ascending=[True, False, True])
    return ordered.groupby("plr_id", as_index=False).head(1).set_index("plr_id")


def summarize(forecast: pd.DataFrame, plr: pd.DataFrame, population: pd.DataFrame) -> pd.DataFrame:
    frame = forecast.assign(
        apparent_minus_air_c=forecast["apparent_temperature_c"] - forecast["temperature_c"]
    )
    hottest = _first_by(frame, "temperature_c")
    widest = _first_by(frame, "apparent_minus_air_c")
    summary = pd.DataFrame(
        {
            "max_temperature_c": hottest["temperature_c"],
            "max_temperature_at_utc": hottest["valid_time_utc"],
            "max_apparent_minus_air_c": widest["apparent_minus_air_c"],
            "max_apparent_minus_air_at_utc": widest["valid_time_utc"],
        }
    ).reset_index()
    summary = summary.merge(plr[["plr_id", "plr_name"]], on="plr_id", how="left")
    if summary["plr_name"].isna().any():
        raise FinalizeError("PLR names missing for some areas")
    people = population[["plr_id", "population_total", "population_65plus", "rejection_reason"]]
    summary = summary.merge(people, on="plr_id", how="left", indicator=True)
    accepted = (summary["_merge"] == "both") & summary["rejection_reason"].isna()
    summary["population_status"] = np.where(
        summary["_merge"] != "both",
        "missing_source_record",
        np.where(accepted, "available", "rejected_source_record"),
    )
    for column in ("population_total", "population_65plus"):
        summary[column] = summary[column].where(accepted)  # never report a rejected count
    return summary.drop(columns=["_merge", "rejection_reason"]).sort_values(
        "plr_id", ignore_index=True
    )


def _optional_int(value) -> int | None:
    return None if pd.isna(value) else int(value)


def forecast_table(forecast: pd.DataFrame, run_time: datetime) -> pa.Table:
    timestamp = pa.timestamp("s", tz="UTC")
    return pa.table(
        {
            "plr_id": pa.array(forecast["plr_id"], type=pa.string()),
            "run_time_utc": pa.array([run_time] * len(forecast), type=timestamp),
            "lead": pa.array(forecast["lead"], type=pa.string()),
            "valid_time_utc": pa.array(forecast["valid_time_utc"], type=timestamp),
            "temperature_c": pa.array(forecast["temperature_c"], type=pa.float64()),
            "apparent_temperature_c": pa.array(
                forecast["apparent_temperature_c"], type=pa.float64()
            ),
        }
    )


def summary_table(summary: pd.DataFrame, run_time: datetime) -> pa.Table:
    timestamp = pa.timestamp("s", tz="UTC")
    return pa.table(
        {
            "plr_id": pa.array(summary["plr_id"], type=pa.string()),
            "plr_name": pa.array(summary["plr_name"], type=pa.string()),
            "run_time_utc": pa.array([run_time] * len(summary), type=timestamp),
            "max_temperature_c": pa.array(summary["max_temperature_c"], type=pa.float64()),
            "max_temperature_at_utc": pa.array(summary["max_temperature_at_utc"], type=timestamp),
            "max_apparent_minus_air_c": pa.array(
                summary["max_apparent_minus_air_c"], type=pa.float64()
            ),
            "max_apparent_minus_air_at_utc": pa.array(
                summary["max_apparent_minus_air_at_utc"], type=timestamp
            ),
            "population_total": pa.array(
                [_optional_int(v) for v in summary["population_total"]], type=pa.int64()
            ),
            "population_65plus": pa.array(
                [_optional_int(v) for v in summary["population_65plus"]], type=pa.int64()
            ),
            "population_status": pa.array(summary["population_status"], type=pa.string()),
        }
    )


def _berlin(value) -> str:
    return pd.Timestamp(value).tz_convert(BERLIN).strftime("%Y-%m-%dT%H:%M")


def dashboard_payloads(
    summary: pd.DataFrame, forecast: pd.DataFrame, run_time: datetime, now: datetime
) -> tuple[dict, dict[str, dict]]:
    """summary.json (all areas) and one detail document per PLR."""
    run_time_berlin = _berlin(run_time)
    areas = [
        {
            "id": row.plr_id,
            "name": row.plr_name,
            "maximum_temperature_c": round(float(row.max_temperature_c), 2),
            "maximum_temperature_at_berlin": _berlin(row.max_temperature_at_utc),
            "maximum_apparent_temperature_difference_c": round(
                float(row.max_apparent_minus_air_c), 2
            ),
            "maximum_apparent_temperature_difference_at_berlin": _berlin(
                row.max_apparent_minus_air_at_utc
            ),
            "population_total": _optional_int(row.population_total),
            "population_65plus": _optional_int(row.population_65plus),
            "population_status": row.population_status,
        }
        for row in summary.itertuples(index=False)
    ]
    summary_payload = {
        "format_version": FORMAT_VERSION,
        "run": run_key_label(run_time),
        "run_time_utc": run_label(run_time),
        "run_time_berlin": run_time_berlin,
        "generated_at_utc": now.isoformat(timespec="seconds"),
        "lead_hour_count": LEAD_COUNT,
        "areas": areas,
    }
    names = dict(zip(summary["plr_id"], summary["plr_name"], strict=True))
    details = {}
    for plr_id, rows in forecast.groupby("plr_id", sort=True):
        rows = rows.sort_values("lead")
        details[plr_id] = {
            "plr_id": plr_id,
            "plr_name": names[plr_id],
            "run_time_berlin": run_time_berlin,
            "valid_times_berlin": [_berlin(value) for value in rows["valid_time_utc"]],
            "forecast_temperatures_c": [round(float(v), 2) for v in rows["temperature_c"]],
            "forecast_apparent_temperatures_c": [
                round(float(v), 2) for v in rows["apparent_temperature_c"]
            ],
        }
    return summary_payload, details


def read_pointer(s3, bucket: str) -> dict | None:
    try:
        return json.loads(s3.get_object(Bucket=bucket, Key=POINTER_KEY)["Body"].read())
    except s3.exceptions.NoSuchKey:
        return None


def _put_json(s3, bucket: str, key: str, payload: dict, cache_control: str) -> None:
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(payload, separators=(",", ":")).encode(),
        ContentType="application/json",
        CacheControl=cache_control,
    )


def publish(s3, bucket: str, run_time: datetime, summary_payload, details, now) -> str:
    """Write the run folder completely, then switch the pointer (last write)."""
    prefix = f"{RUNS_PREFIX}/{run_key_label(run_time)}"
    with ThreadPoolExecutor(max_workers=PUT_WORKERS) as pool:
        futures = [
            pool.submit(
                _put_json, s3, bucket, f"{prefix}/areas/{plr_id}.json", body, CACHE_IMMUTABLE
            )
            for plr_id, body in details.items()
        ]
        for future in futures:
            future.result()  # re-raises the first failed upload
    _put_json(s3, bucket, f"{prefix}/summary.json", summary_payload, CACHE_IMMUTABLE)
    pointer = {
        "format_version": FORMAT_VERSION,
        "run": run_key_label(run_time),
        "run_time_utc": run_label(run_time),
        "published_at_utc": now.isoformat(timespec="seconds"),
    }
    _put_json(s3, bucket, POINTER_KEY, pointer, CACHE_POINTER)
    return prefix


def run_finalize(
    s3,
    dynamodb,
    data_bucket: str,
    dashboard_bucket: str,
    table: str,
    run_time: datetime,
    *,
    now: datetime | None = None,
    expected_plr_count: int = EXPECTED_PLR_COUNT,
) -> dict:
    started = time.monotonic()
    now = now or datetime.now(UTC)
    label, key_label = run_label(run_time), run_key_label(run_time)

    if state.is_published(dynamodb, table, label):
        return {"run_time": label, "outcome": "duplicate"}
    pointer = read_pointer(s3, dashboard_bucket)
    if pointer is not None and pointer["run"] > key_label:
        return {"run_time": label, "outcome": "stale", "published_run": pointer["run"]}

    cells = read_forecast_cells(s3, data_bucket, run_time, load_mask(s3, data_bucket))
    bridge = _read_parquet(s3, data_bucket, "silver/bridge/part.parquet")
    plr = _read_parquet(s3, data_bucket, "silver/plr/part.parquet", ["plr_id", "plr_name"])
    population = _read_parquet(s3, data_bucket, "silver/population/part.parquet")
    forecast = aggregate_to_plr(cells, bridge, expected_plr_count)
    summary = summarize(forecast, plr, population)
    built = time.monotonic()

    for prefix, arrow_table in (
        (GOLD_FORECAST_PREFIX, forecast_table(forecast, run_time)),
        (GOLD_SUMMARY_PREFIX, summary_table(summary, run_time)),
    ):
        buffer = io.BytesIO()
        pq.write_table(arrow_table, buffer, compression="zstd")
        s3.put_object(
            Bucket=data_bucket, Key=f"{prefix}/run={key_label}/part.parquet", Body=buffer.getvalue()
        )

    summary_payload, details = dashboard_payloads(summary, forecast, run_time, now)
    run_prefix = publish(s3, dashboard_bucket, run_time, summary_payload, details, now)
    state.mark_published(dynamodb, table, label, now)

    return {
        "run_time": label,
        "outcome": "published",
        "plr_rows": len(forecast),
        "areas": len(summary),
        "dashboard_prefix": run_prefix,
        "seconds": {
            "read_and_build": round(built - started, 1),
            "write_and_publish": round(time.monotonic() - built, 1),
        },
    }
