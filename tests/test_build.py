import io
import json

import pyarrow.parquet as pq
from synthetic import grid_netcdf_bz2, names_xlsx, plr_geojson, population_csv

from berlin_temp.reference import sources
from berlin_temp.reference.build import Downloads, run_reference_build

NAMES_XLSX = names_xlsx()  # generated once: the workbook embeds a creation time


class FakeS3:
    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def put_object(self, *, Bucket, Key, Body, **_):  # noqa: N803 (boto3 signature)
        assert Bucket == "bucket"
        self.objects[Key] = bytes(Body)

    def upload_file(self, filename, bucket, key):
        assert bucket == "bucket"
        with open(filename, "rb") as handle:
            self.objects[key] = handle.read()


def _info(name: str, content_type: str = "application/octet-stream") -> sources.FetchInfo:
    url = f"https://example.org/{name}"
    return sources.FetchInfo(url, url, 200, content_type, "ab" * 32, 1, "2026-01-01T00:00:00")


def _downloads(tmp_path) -> Downloads:
    def grid_file(target):
        target.write_bytes(grid_netcdf_bz2(tmp_path))
        return _info("grid.nc.bz2")

    return Downloads(
        population=lambda: (population_csv(), _info("pop.csv", "text/csv")),
        plr_names=lambda: (NAMES_XLSX, _info("names.xlsx")),
        plr_geojson=lambda layer: (
            plr_geojson(),
            _info("lor", "application/json"),
            {"layer": layer or "discovered", "output_format": "application/json"},
        ),
        grid_file=grid_file,
    )


def _silver(s3: FakeS3) -> dict[str, bytes]:
    return {key: body for key, body in s3.objects.items() if key.startswith("silver/")}


def test_end_to_end_with_fake_s3(world, tmp_path):
    s3 = FakeS3()
    summary = run_reference_build(
        s3, "bucket", expect=world["expect"], downloads=_downloads(tmp_path), workdir=tmp_path
    )

    # Bronze: bytes exactly as received, plus provenance.
    assert s3.objects["bronze/static/population/EWR_L21_202512E_Matrix.csv"] == population_csv()
    assert s3.objects["bronze/static/lor/lor_planungsraum.geojson"] == plr_geojson()
    assert s3.objects[f"bronze/static/plr_names/{sources.PLR_NAMES_FILENAME}"] == NAMES_XLSX
    grid_key = "bronze/static/icon_grid/icon_grid_0047_R19B07_L.nc.bz2"
    assert grid_key in s3.objects
    meta = json.loads(s3.objects["bronze/static/lor/lor_planungsraum.geojson.meta.json"])
    assert meta["sha256"] == "ab" * 32
    assert meta["layer"] == "discovered"

    # Silver: five tables with the expected row counts.
    def rows(name):
        return pq.read_table(io.BytesIO(s3.objects[f"silver/{name}/part.parquet"]))

    assert rows("plr").num_rows == 4
    assert rows("plr").column("plr_name").to_pylist()[0] == "Name 01100101"
    assert rows("population").num_rows == 4
    assert rows("population").column("rejection_reason").to_pylist().count(None) == 3
    assert rows("icon_cell").num_rows == 1152
    assert rows("mask").num_rows == world["expect"].mask_cells
    assert rows("bridge").num_rows == world["expect"].bridge_rows
    assert summary["silver"]["silver/bridge/part.parquet"]["rows"] == world["expect"].bridge_rows
    geometry = json.loads(s3.objects["gold/static/plr_geometry.json"])
    assert [area["id"] for area in geometry["areas"]] == rows("plr").column("plr_id").to_pylist()
    assert geometry["coordinate_system"] == "EPSG:25833"
    assert set(summary["seconds"]) == {
        "download_to_bronze",
        "parse",
        "cells",
        "mask_and_bridge",
        "silver",
    }


def test_rerun_overwrites_with_identical_silver_content(world, tmp_path):
    s3 = FakeS3()
    kwargs = {"expect": world["expect"], "downloads": _downloads(tmp_path), "workdir": tmp_path}
    run_reference_build(s3, "bucket", **kwargs)
    first = _silver(s3)
    run_reference_build(s3, "bucket", **kwargs)
    assert _silver(s3) == first


def test_failed_check_writes_bronze_but_no_silver(world, tmp_path):
    from dataclasses import replace

    import pytest

    from berlin_temp.reference.checks import ReferenceCheckError

    s3 = FakeS3()
    wrong = replace(world["expect"], mask_cells=1)
    with pytest.raises(ReferenceCheckError, match="mask cells"):
        run_reference_build(
            s3, "bucket", expect=wrong, downloads=_downloads(tmp_path), workdir=tmp_path
        )
    assert _silver(s3) == {}
    assert any(key.startswith("bronze/static/") for key in s3.objects)
    assert "gold/static/plr_geometry.json" not in s3.objects
