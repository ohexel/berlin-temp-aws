"""Reference build: static sources -> bronze/static -> checks -> silver reference tables."""

import logging
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from berlin_temp.reference import silver, sources
from berlin_temp.reference.checks import Expectations, check_reference
from berlin_temp.reference.geometry import build_bridge, build_mask
from berlin_temp.reference.icon_grid import build_cells, decompress_bz2, read_icon_grid
from berlin_temp.reference.lor import parse_plr_geojson
from berlin_temp.reference.plr_names import parse_plr_names
from berlin_temp.reference.population import parse_population

LOGGER = logging.getLogger(__name__)

BRONZE_PREFIX = "bronze/static"
POPULATION_FILENAME = "EWR_L21_202512E_Matrix.csv"
LOR_FILENAME = "lor_planungsraum.geojson"
GRID_FILENAME = "icon_grid_0047_R19B07_L.nc.bz2"


@dataclass(frozen=True)
class Downloads:
    """How the sources are fetched; replaced by fakes in tests."""

    population: Callable[[], tuple[bytes, sources.FetchInfo]] = lambda: sources.fetch(
        sources.AFS_POPULATION_URL
    )
    plr_names: Callable[[], tuple[bytes, sources.FetchInfo]] = lambda: sources.fetch(
        sources.PLR_NAMES_URL
    )
    plr_geojson: Callable[[str | None], tuple[bytes, sources.FetchInfo, dict]] = (
        sources.fetch_plr_geojson
    )
    grid_file: Callable[[Path], sources.FetchInfo] = lambda target: sources.fetch_to_file(
        sources.ICON_GRID_URL, target
    )


@dataclass
class _Run:
    s3: object
    bucket: str
    seconds: dict[str, float] = field(default_factory=dict)
    _started: float = field(default_factory=time.monotonic)

    def lap(self, phase: str) -> None:
        now = time.monotonic()
        self.seconds[phase] = round(now - self._started, 1)
        self._started = now
        LOGGER.info("phase %s done in %.1f s", phase, self.seconds[phase])

    def put(self, key: str, body: bytes, content_type: str | None = None) -> None:
        extra = {"ContentType": content_type} if content_type else {}
        self.s3.put_object(Bucket=self.bucket, Key=key, Body=body, **extra)

    def bronze_key(self, directory: str, filename: str) -> str:
        return f"{BRONZE_PREFIX}/{directory}/{filename}"

    def put_provenance(self, key: str, info: sources.FetchInfo, **extra) -> None:
        self.put(key + ".meta.json", info.to_json(**extra), "application/json")


def run_reference_build(
    s3,
    bucket: str,
    *,
    plr_layer: str | None = None,
    expect: Expectations = Expectations(),  # noqa: B008 (frozen dataclass)
    downloads: Downloads = Downloads(),  # noqa: B008 (frozen dataclass)
    workdir: Path | None = None,
) -> dict:
    run = _Run(s3=s3, bucket=bucket)

    # 1. Bronze: the sources exactly as received, plus provenance.
    population_bytes, population_info = downloads.population()
    sources.require_not_html(
        population_info.content_type,
        population_info.source_url,
        population_info.resolved_url,
        population_bytes,
    )
    key = run.bronze_key("population", POPULATION_FILENAME)
    run.put(key, population_bytes)
    run.put_provenance(
        key, population_info, published_resource_url=sources.AFS_PUBLISHED_RESOURCE_URL
    )

    names_bytes, names_info = downloads.plr_names()
    sources.require_not_html(
        names_info.content_type, names_info.source_url, names_info.resolved_url, names_bytes
    )
    key = run.bronze_key("plr_names", sources.PLR_NAMES_FILENAME)
    run.put(key, names_bytes)
    run.put_provenance(key, names_info)

    lor_bytes, lor_info, lor_details = downloads.plr_geojson(plr_layer)
    key = run.bronze_key("lor", LOR_FILENAME)
    run.put(key, lor_bytes)
    run.put_provenance(key, lor_info, **lor_details)

    with tempfile.TemporaryDirectory(dir=workdir) as temporary:
        grid_archive = Path(temporary) / GRID_FILENAME
        grid_info = downloads.grid_file(grid_archive)
        grid_key = run.bronze_key("icon_grid", GRID_FILENAME)
        s3.upload_file(str(grid_archive), bucket, grid_key)
        run.put_provenance(grid_key, grid_info)
        run.lap("download_to_bronze")

        # 2. Parse and build.
        population = parse_population(population_bytes)
        names = parse_plr_names(names_bytes)
        plr = parse_plr_geojson(lor_bytes)
        netcdf = Path(temporary) / "icon_grid.nc"
        decompress_bz2(grid_archive, netcdf)
        grid = read_icon_grid(netcdf)
        netcdf.unlink()
        grid_archive.unlink()
        run.lap("parse")

        cells = build_cells(grid)
        run.lap("cells")
        mask = build_mask(plr.geometry, cells, expect.mask_buffer_m)
        bridge = build_bridge(plr.plr_id, plr.geometry, cells)
        run.lap("mask_and_bridge")

    # 3. Checks before anything is written to silver.
    summary = check_reference(
        expect, plr=plr, names=names, population=population, grid=grid, mask=mask, bridge=bridge
    )
    summary["icon_cells_rejected"] = cells.rejected
    summary["icon_cells"] = len(cells.cell_index)

    # 4. Silver: fixed keys, so a re-run overwrites with identical content.
    tables = {
        "plr": silver.plr_table(plr, names),
        "population": silver.population_table(population),
        "icon_cell": silver.icon_cell_table(cells),
        "mask": silver.mask_table(mask, expect.mask_buffer_m),
        "bridge": silver.bridge_table(bridge),
    }
    written = {}
    for name, table in tables.items():
        key = f"silver/{name}/part.parquet"
        body = silver.to_parquet_bytes(table)
        run.put(key, body)
        written[key] = {"rows": table.num_rows, "bytes": len(body)}
    run.lap("silver")

    summary["silver"] = written
    summary["seconds"] = run.seconds
    summary["sources_sha256"] = {
        "population": population_info.sha256,
        "plr_names": names_info.sha256,
        "lor": lor_info.sha256,
        "icon_grid": grid_info.sha256,
    }
    return summary
