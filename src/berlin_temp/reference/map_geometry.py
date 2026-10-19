"""Simplified planning-area boundaries for the dashboard map (written once, by reference_build)."""

import json

import shapely

from berlin_temp.reference.icon_grid import PROJECTED_EPSG
from berlin_temp.reference.lor import PlrGeometries

SIMPLIFY_METERS = 20.0  # same tolerance as the old dashboard export
COORDINATE_DECIMALS = 1


def _round(coordinates):
    if isinstance(coordinates[0], int | float):
        return [round(value, COORDINATE_DECIMALS) for value in coordinates]
    return [_round(child) for child in coordinates]


def plr_geometry_json(plr: PlrGeometries, tolerance_m: float = SIMPLIFY_METERS) -> bytes:
    """GeoJSON-like geometries in EPSG:25833, simplified per area with topology preserved."""
    simplified = shapely.simplify(plr.geometry, tolerance_m, preserve_topology=True)
    areas = []
    for plr_id, geometry in sorted(zip(plr.plr_id, simplified, strict=True)):
        if geometry.is_empty or geometry.geom_type not in ("Polygon", "MultiPolygon"):
            raise ValueError(
                f"PLR {plr_id}: simplified geometry is {geometry.geom_type}, empty or not"
            )
        mapping = shapely.geometry.mapping(geometry)
        areas.append(
            {
                "id": plr_id,
                "geometry": {
                    "type": mapping["type"],
                    "coordinates": _round(mapping["coordinates"]),
                },
            }
        )
    payload = {
        "format_version": 1,
        "coordinate_system": f"EPSG:{PROJECTED_EPSG}",
        "simplification_m": tolerance_m,
        "areas": areas,
    }
    return json.dumps(payload, separators=(",", ":")).encode()
