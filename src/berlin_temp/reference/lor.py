"""Parse the LOR planning-area GeoJSON (WFS output) into EPSG:25833 geometries."""

import json
import re
from dataclasses import dataclass

import numpy as np
import shapely
from pyproj import Transformer
from shapely.geometry import shape

PROJECTED_EPSG = 25833
PLR_ID_CANDIDATES = ("plr_id", "PLR_ID", "RAUMID", "raumid", "PLR", "plr")


@dataclass(frozen=True)
class PlrGeometries:
    plr_id: list[str]
    geometry: np.ndarray
    source_epsg: int


def _source_epsg(collection: dict) -> int:
    """EPSG code of the coordinates; GeoJSON without a crs member means WGS 84 (4326)."""
    name = (collection.get("crs") or {}).get("properties", {}).get("name")
    if name is None:
        return 4326
    match = re.search(r"EPSG:+(\d+)", name)
    if match is None:
        raise ValueError(f"Cannot read an EPSG code from GeoJSON crs name {name!r}")
    return int(match.group(1))


def parse_plr_geojson(content: bytes) -> PlrGeometries:
    collection = json.loads(content)
    if collection.get("type") != "FeatureCollection" or "features" not in collection:
        raise ValueError("LOR response is not a GeoJSON FeatureCollection")
    features = collection["features"]
    if not features:
        raise ValueError("LOR GeoJSON contains no features")

    properties = features[0].get("properties") or {}
    id_key = next((key for key in PLR_ID_CANDIDATES if key in properties), None)
    if id_key is None:
        raise ValueError(f"Could not identify the PLR id property; available: {sorted(properties)}")

    epsg = _source_epsg(collection)
    ids: list[str] = []
    geometries = []
    for feature in features:
        raw_id = (feature.get("properties") or {}).get(id_key)
        if raw_id is None or not str(raw_id).strip():
            raise ValueError("LOR feature without a PLR id")
        if feature.get("geometry") is None:
            raise ValueError(f"LOR feature {raw_id} has no geometry")
        ids.append(str(raw_id).strip())
        geometries.append(shape(feature["geometry"]))

    array = np.array(geometries, dtype=object)
    if epsg != PROJECTED_EPSG:
        transformer = Transformer.from_crs(epsg, PROJECTED_EPSG, always_xy=True)
        array = shapely.transform(
            array, lambda coords: np.column_stack(transformer.transform(coords[:, 0], coords[:, 1]))
        )
    return PlrGeometries(plr_id=ids, geometry=array, source_epsg=epsg)
