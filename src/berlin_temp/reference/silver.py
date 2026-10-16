"""Silver tables (Parquet). Geometries are stored as WKB in EPSG:25833, with a crs column."""

import io

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import shapely

from berlin_temp.reference.geometry import Bridge
from berlin_temp.reference.icon_grid import PROJECTED_EPSG, IconCells
from berlin_temp.reference.lor import PlrGeometries
from berlin_temp.reference.population import PopulationRow

CRS = f"EPSG:{PROJECTED_EPSG}"


def _wkb(geometries: np.ndarray) -> pa.Array:
    return pa.array(shapely.to_wkb(geometries).tolist(), type=pa.binary())


def plr_table(plr: PlrGeometries, names: dict[str, str]) -> pa.Table:
    return pa.table(
        {
            "plr_id": pa.array(plr.plr_id, type=pa.string()),
            "plr_name": pa.array([names[plr_id] for plr_id in plr.plr_id], type=pa.string()),
            "area_m2": pa.array(shapely.area(plr.geometry), type=pa.float64()),
            "crs": pa.array([CRS] * len(plr.plr_id), type=pa.string()),
            "geometry_wkb": _wkb(plr.geometry),
        }
    )


def population_table(rows: list[PopulationRow]) -> pa.Table:
    return pa.table(
        {
            "plr_id": pa.array([r.plr_id for r in rows], type=pa.string()),
            "population_total": pa.array([r.population_total for r in rows], type=pa.int64()),
            "population_65_79": pa.array([r.population_65_79 for r in rows], type=pa.int64()),
            "population_80plus": pa.array([r.population_80plus for r in rows], type=pa.int64()),
            "population_65plus": pa.array([r.population_65plus for r in rows], type=pa.int64()),
            "share_65plus": pa.array([r.share_65plus for r in rows], type=pa.float64()),
            "rejection_reason": pa.array([r.rejection_reason for r in rows], type=pa.string()),
            "reference_date": pa.array([r.reference_date for r in rows], type=pa.date32()),
        }
    )


def icon_cell_table(cells: IconCells) -> pa.Table:
    return pa.table(
        {
            "cell_index": pa.array(cells.cell_index, type=pa.int32()),
            "area_m2": pa.array(cells.area_m2, type=pa.float64()),
            "crs": pa.array([CRS] * len(cells.cell_index), type=pa.string()),
            "geometry_wkb": _wkb(cells.polygons),
        }
    )


def mask_table(mask: np.ndarray, buffer_m: float) -> pa.Table:
    return pa.table(
        {
            "cell_index": pa.array(mask, type=pa.int32()),
            "buffer_m": pa.array([buffer_m] * len(mask), type=pa.float64()),
        }
    )


def bridge_table(bridge: Bridge) -> pa.Table:
    return pa.table(
        {
            "plr_id": pa.array(bridge.plr_id.tolist(), type=pa.string()),
            "cell_index": pa.array(bridge.cell_index, type=pa.int32()),
            "intersection_area_m2": pa.array(bridge.intersection_area_m2, type=pa.float64()),
            "plr_area_m2": pa.array(bridge.plr_area_m2, type=pa.float64()),
            "icon_cell_area_m2": pa.array(bridge.icon_cell_area_m2, type=pa.float64()),
            "fraction_of_plr": pa.array(bridge.fraction_of_plr, type=pa.float64()),
            "fraction_of_icon_cell": pa.array(bridge.fraction_of_icon_cell, type=pa.float64()),
        }
    )


def to_parquet_bytes(table: pa.Table) -> bytes:
    buffer = io.BytesIO()
    pq.write_table(table, buffer, compression="zstd")
    return buffer.getvalue()
