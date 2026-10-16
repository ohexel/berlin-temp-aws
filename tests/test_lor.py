import json

import pytest
from synthetic import PLR_IDS, plr_geojson

from berlin_temp.reference.lor import parse_plr_geojson


def test_default_crs_is_wgs84_and_is_projected():
    plr = parse_plr_geojson(plr_geojson())
    assert plr.source_epsg == 4326
    assert plr.plr_id == PLR_IDS
    minx, miny, maxx, maxy = plr.geometry[0].bounds
    assert 3.5e5 < minx < maxx < 4.5e5
    assert 5.7e6 < miny < maxy < 5.9e6


def test_data_already_in_target_crs_is_not_transformed():
    plr = parse_plr_geojson(plr_geojson("urn:ogc:def:crs:EPSG::25833"))
    assert plr.source_epsg == 25833
    assert plr.geometry[0].bounds[0] == pytest.approx(13.34)  # unchanged


def test_unreadable_crs_name_is_an_error():
    with pytest.raises(ValueError, match="EPSG code"):
        parse_plr_geojson(plr_geojson("something"))


def test_not_a_feature_collection():
    with pytest.raises(ValueError, match="FeatureCollection"):
        parse_plr_geojson(b'{"type": "Feature"}')


def test_feature_without_id():
    collection = json.loads(plr_geojson())
    collection["features"][1]["properties"]["plr_id"] = " "
    with pytest.raises(ValueError, match="without a PLR id"):
        parse_plr_geojson(json.dumps(collection).encode())
