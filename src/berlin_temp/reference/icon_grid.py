"""Read the ICON grid file and build the triangular cell polygons (EPSG:25833)."""

import bz2
import shutil
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import shapely
from netCDF4 import Dataset
from pyproj import Transformer

PROJECTED_EPSG = 25833
REQUIRED_VARIABLES = ("vlon", "vlat", "vertex_of_cell")


@dataclass(frozen=True)
class IconGrid:
    """Vertex coordinates in degrees and 0-based vertex indices per triangle."""

    lon_deg: np.ndarray
    lat_deg: np.ndarray
    vertex_of_cell: np.ndarray  # shape (cell_count, 3)


@dataclass(frozen=True)
class IconCells:
    """Valid cell polygons with their original (global) cell index."""

    cell_index: np.ndarray
    polygons: np.ndarray
    area_m2: np.ndarray
    rejected: dict[str, int]  # reason -> number of rejected cells


def decompress_bz2(source: Path, target: Path) -> None:
    with bz2.open(source, "rb") as reader, target.open("wb") as writer:
        shutil.copyfileobj(reader, writer)


def _to_degrees(values: np.ndarray, units: str | None) -> np.ndarray:
    result = np.asarray(values, dtype="float64").reshape(-1)
    if "radian" in (units or "").lower():
        return np.rad2deg(result)
    # Files without unit attributes: values that fit within +/- 2*pi are radians.
    if result.size > 0 and np.nanmax(np.abs(result)) <= 2 * np.pi + 0.1:
        return np.rad2deg(result)
    return result


def read_icon_grid(netcdf_path: Path) -> IconGrid:
    with Dataset(netcdf_path, "r") as dataset:
        missing = set(REQUIRED_VARIABLES) - set(dataset.variables)
        if missing:
            raise ValueError(
                "ICON grid is missing required variables: " + ", ".join(sorted(missing))
            )
        lon_var, lat_var = dataset.variables["vlon"], dataset.variables["vlat"]
        lon = _to_degrees(lon_var[:], getattr(lon_var, "units", None))
        lat = _to_degrees(lat_var[:], getattr(lat_var, "units", None))
        connectivity = np.asarray(dataset.variables["vertex_of_cell"][:])

    if connectivity.shape[0] == 3:
        connectivity = connectivity.T
    if connectivity.ndim != 2 or connectivity.shape[1] != 3:
        raise ValueError(f"Unexpected ICON vertex_of_cell shape: {connectivity.shape}")
    connectivity = connectivity.astype("int64")

    minimum = int(connectivity.min())
    if minimum == 1:
        connectivity -= 1
    elif minimum != 0:
        raise ValueError(
            "ICON vertex connectivity must use 0-based or 1-based indexes; "
            f"minimum observed index is {minimum}"
        )
    if int(connectivity.max()) >= len(lon):
        raise ValueError("ICON vertex connectivity references a vertex outside the vertex array")
    if len(lon) != len(lat):
        raise ValueError("ICON longitude/latitude vertex counts differ")
    return IconGrid(lon_deg=lon, lat_deg=lat, vertex_of_cell=connectivity)


def build_cells(grid: IconGrid) -> IconCells:
    """Build one triangle per cell in EPSG:25833 and drop invalid ones.

    Vertices are projected first, so triangle edges are straight in the projected plane.
    """
    transformer = Transformer.from_crs(4326, PROJECTED_EPSG, always_xy=True)
    x, y = transformer.transform(grid.lon_deg, grid.lat_deg)
    corners = np.stack([x[grid.vertex_of_cell], y[grid.vertex_of_cell]], axis=-1)  # (n, 3, 2)
    rings = np.concatenate([corners, corners[:, :1, :]], axis=1)  # close the ring
    polygons = shapely.polygons(rings)
    area = shapely.area(polygons)

    reason = np.full(len(polygons), "", dtype=object)
    reason[area <= 0] = "non_positive_area"
    reason[~shapely.is_valid(polygons)] = "invalid_geometry"
    reason[shapely.is_empty(polygons)] = "empty_geometry"
    keep = reason == ""

    rejected: dict[str, int] = {}
    for value in reason[~keep]:
        rejected[value] = rejected.get(value, 0) + 1
    return IconCells(
        cell_index=np.flatnonzero(keep).astype("int32"),
        polygons=polygons[keep],
        area_m2=area[keep],
        rejected=rejected,
    )
