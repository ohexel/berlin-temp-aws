"""ingest_lead: four DWD GRIB files -> bronze, then silver forecast_cell for the Berlin mask."""

import io
import logging
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from berlin_temp.pipeline import weather
from berlin_temp.pipeline.forecast import INDICATORS, ForecastKey
from berlin_temp.pipeline.grib import ICON_D2_POINT_COUNT, read_field
from berlin_temp.reference import sources

LOGGER = logging.getLogger(__name__)

BRONZE_PREFIX = "bronze/forecast"
SILVER_PREFIX = "silver/forecast_cell"
MASK_KEY = "silver/mask/part.parquet"
_GRIB_MAGIC = b"GRIB"


def download_grib(url: str, target: Path) -> sources.FetchInfo:
    """Stream one GRIB file to disk; reject empty files and HTML error pages."""
    info = sources.fetch_to_file(url, target)
    with target.open("rb") as handle:
        magic = handle.read(len(_GRIB_MAGIC))
    if info.size_bytes == 0 or magic != _GRIB_MAGIC:
        raise ValueError(f"{url} did not return a GRIB file ({info.size_bytes} bytes)")
    return info


@dataclass(frozen=True)
class Downloads:
    """Injection point for tests."""

    grib: Callable[[str, Path], sources.FetchInfo] = download_grib


def load_mask(s3, bucket: str) -> np.ndarray:
    body = s3.get_object(Bucket=bucket, Key=MASK_KEY)["Body"].read()
    cells = pq.read_table(io.BytesIO(body), columns=["cell_index"]).column("cell_index")
    return cells.to_numpy().astype("int64")


def _forecast_table(key: ForecastKey, mask: np.ndarray, fields: dict[str, np.ndarray]) -> pa.Table:
    count = len(mask)
    timestamp = pa.timestamp("s", tz="UTC")
    return pa.table(
        {
            "run_time_utc": pa.array([key.run_time] * count, type=timestamp),
            "lead": pa.array([key.lead_label] * count, type=pa.string()),
            "valid_time_utc": pa.array([key.valid_time] * count, type=timestamp),
            "cell_index": pa.array(mask, type=pa.int32()),
            "temperature_c": pa.array(fields["temperature_c"], type=pa.float64()),
            "relative_humidity_percent": pa.array(
                fields["relative_humidity_percent"], type=pa.float64()
            ),
            "wind_speed_10m_ms": pa.array(fields["wind_speed_10m_ms"], type=pa.float64()),
            "apparent_temperature_c": pa.array(fields["apparent_temperature_c"], type=pa.float64()),
        }
    )


def _require_finite(name: str, values: np.ndarray) -> None:
    bad = int((~np.isfinite(values)).sum())
    if bad:
        raise ValueError(f"{name}: {bad} of {len(values)} mask cells are missing or not finite")


def run_ingest_lead(
    s3,
    bucket: str,
    key: ForecastKey,
    mask: np.ndarray,
    *,
    downloads: Downloads = Downloads(),  # noqa: B008 (frozen dataclass)
    workdir: Path | None = None,
    expected_points: int = ICON_D2_POINT_COUNT,
) -> dict:
    if len(mask) == 0 or mask.min() < 0 or mask.max() >= expected_points:
        raise ValueError(f"mask is empty or outside the grid of {expected_points} cells")

    started = time.monotonic()
    raw: dict[str, np.ndarray] = {}
    sha256: dict[str, str] = {}
    with tempfile.TemporaryDirectory(dir=workdir) as directory:
        for indicator in INDICATORS:
            target = Path(directory) / f"{indicator}.grib2"
            info = downloads.grib(key.field_url(indicator), target)
            bronze_key = f"{BRONZE_PREFIX}/{key.partition}/{indicator}.grib2"
            s3.upload_file(str(target), bucket, bronze_key)
            s3.put_object(
                Bucket=bucket,
                Key=bronze_key + ".meta.json",
                Body=info.to_json(indicator=indicator),
                ContentType="application/json",
            )
            sha256[indicator] = info.sha256
            raw[indicator] = read_field(target, indicator, key, expected_points)[mask]
    downloaded = time.monotonic()

    for indicator, values in raw.items():
        _require_finite(indicator, values)

    temperature = raw["T_2M"] - weather.KELVIN_OFFSET
    humidity = raw["RELHUM_2M"]
    wind = weather.wind_speed(raw["U_10M"], raw["V_10M"])
    apparent = weather.apparent_temperature_c(temperature, humidity, wind)
    fields = {
        "temperature_c": temperature,
        "relative_humidity_percent": humidity,
        "wind_speed_10m_ms": wind,
        "apparent_temperature_c": apparent,
    }
    for name, values in fields.items():
        _require_finite(name, values)

    buffer = io.BytesIO()
    pq.write_table(_forecast_table(key, mask, fields), buffer, compression="zstd")
    silver_key = f"{SILVER_PREFIX}/{key.partition}/part.parquet"
    s3.put_object(Bucket=bucket, Key=silver_key, Body=buffer.getvalue())

    return {
        "run_time": key.run_label,
        "lead": key.lead_label,
        "cells": len(mask),
        "silver_key": silver_key,
        "seconds": {
            "download_and_decode": round(downloaded - started, 1),
            "compute_and_write": round(time.monotonic() - downloaded, 1),
        },
        "sources_sha256": sha256,
    }
