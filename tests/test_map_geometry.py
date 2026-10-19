import json

import numpy as np
import shapely

from berlin_temp.reference.lor import PlrGeometries
from berlin_temp.reference.map_geometry import plr_geometry_json


def _plr():
    # A square with a 1 m wiggle (removed by a 20 m tolerance) and a two-part area.
    wiggly = shapely.Polygon([(0, 0), (500, 1), (1000, 0), (1000, 1000), (0, 1000)])
    multi = shapely.MultiPolygon(
        [shapely.box(2000, 0, 2500, 500), shapely.box(3000.04, 0, 3500, 500.06)]
    )
    return PlrGeometries(plr_id=["02", "01"], geometry=np.array([wiggly, multi]), source_epsg=25833)


def test_sorted_simplified_rounded():
    payload = json.loads(plr_geometry_json(_plr()))
    assert payload["coordinate_system"] == "EPSG:25833"
    assert payload["simplification_m"] == 20.0
    assert [area["id"] for area in payload["areas"]] == ["01", "02"]
    multi, square = (area["geometry"] for area in payload["areas"])
    assert multi["type"] == "MultiPolygon" and len(multi["coordinates"]) == 2
    assert [3000.0, 0.0] in multi["coordinates"][1][0]  # rounded to 0.1 m
    assert square["type"] == "Polygon"
    assert len(square["coordinates"][0]) == 5  # the 1 m wiggle point is gone


def test_compact_json():
    assert b" " not in plr_geometry_json(_plr())
