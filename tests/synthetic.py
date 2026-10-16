"""A small synthetic 'world' for tests: grid, planning areas, population, names."""

import bz2
import io
import json
from dataclasses import dataclass

import numpy as np
from netCDF4 import Dataset
from openpyxl import Workbook

LON0, LAT0, STEP = 13.30, 52.45, 0.01
NX = NY = 24
PLR_IDS = ["01100101", "01100102", "01100103", "01100104"]
# Four rectangles tiling lon 13.34..13.46, lat 52.49..52.61 (exactly shared edges).
LON_EDGES = [13.34, 13.40, 13.46]
LAT_EDGES = [52.49, 52.55, 52.61]


@dataclass(frozen=True)
class GridArrays:
    vlon_rad: np.ndarray
    vlat_rad: np.ndarray
    vertex_of_cell_1based: np.ndarray  # shape (3, n_cells), like the real ICON file


def grid_arrays() -> GridArrays:
    lons = LON0 + STEP * np.arange(NX + 1)
    lats = LAT0 + STEP * np.arange(NY + 1)
    lon_grid, lat_grid = np.meshgrid(lons, lats)  # shape (NY+1, NX+1)
    index = np.arange((NX + 1) * (NY + 1)).reshape(NY + 1, NX + 1)
    triangles = []
    for j in range(NY):
        for i in range(NX):
            a, b, c, d = index[j, i], index[j, i + 1], index[j + 1, i], index[j + 1, i + 1]
            triangles += [(a, b, d), (a, d, c)]
    cells = np.array(triangles).T + 1
    return GridArrays(np.deg2rad(lon_grid.ravel()), np.deg2rad(lat_grid.ravel()), cells)


def grid_netcdf_bz2(tmp_path, arrays: GridArrays | None = None) -> bytes:
    arrays = arrays or grid_arrays()
    path = tmp_path / "grid.nc"
    with Dataset(path, "w") as dataset:
        dataset.createDimension("vertex", len(arrays.vlon_rad))
        dataset.createDimension("cell", arrays.vertex_of_cell_1based.shape[1])
        dataset.createDimension("nv", 3)
        for name, values in (("vlon", arrays.vlon_rad), ("vlat", arrays.vlat_rad)):
            variable = dataset.createVariable(name, "f8", ("vertex",))
            variable.units = "radian"
            variable[:] = values
        connectivity = dataset.createVariable("vertex_of_cell", "i4", ("nv", "cell"))
        connectivity[:] = arrays.vertex_of_cell_1based
    return bz2.compress(path.read_bytes())


def plr_geojson(crs_name: str | None = None) -> bytes:
    features = []
    for number, plr_id in enumerate(PLR_IDS):
        i, j = number % 2, number // 2
        x0, x1 = LON_EDGES[i], LON_EDGES[i + 1]
        y0, y1 = LAT_EDGES[j], LAT_EDGES[j + 1]
        ring = [[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]
        features.append(
            {
                "type": "Feature",
                "properties": {"plr_id": plr_id, "name": f"area {plr_id}"},
                "geometry": {"type": "Polygon", "coordinates": [ring]},
            }
        )
    collection: dict = {"type": "FeatureCollection", "features": features}
    if crs_name:
        collection["crs"] = {"type": "name", "properties": {"name": crs_name}}
    return json.dumps(collection).encode()


def population_csv(rows: list[tuple[str, str, str, str]] | None = None) -> bytes:
    """Rows are (RAUMID, E_E, E_E65U80, E_E80U110); the default has one invalid total."""
    rows = rows or [
        ("01100101", "1000", "150", "50"),
        ("01100102", "x", "10", "5"),
        ("01100103", "500", "60", "40"),
        ("01100104", "800", "100", "20"),
    ]
    lines = ['"ZEIT";"RAUMID";"E_E";"E_E65U80";"E_E80U110"']
    lines += [f'"202512";"{a}";{b};{c};{d}' for a, b, c, d in rows]
    return ("﻿" + "\n".join(lines) + "\n").encode("utf-8")


def names_xlsx(ids: list[str] | None = None, sheet: str = "LOR_2023_PLR") -> bytes:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = sheet
    worksheet.append(["PLR_ID", "PLR_Name"])
    for plr_id in ids or PLR_IDS:
        worksheet.append([plr_id, f"Name {plr_id}"])
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
