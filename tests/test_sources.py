import json

import pytest

from berlin_temp.reference import sources

CAPABILITIES = b"""<?xml version="1.0"?>
<wfs:WFS_Capabilities version="2.0.0" xmlns:wfs="http://www.opengis.net/wfs/2.0"
    xmlns:ows="http://www.opengis.net/ows/1.1">
  <ows:OperationsMetadata>
    <ows:Operation name="GetFeature">
      <ows:Parameter name="outputFormat">
        <ows:AllowedValues>
          <ows:Value>application/gml+xml; version=3.2</ows:Value>
          <ows:Value>application/geo+json</ows:Value>
        </ows:AllowedValues>
      </ows:Parameter>
    </ows:Operation>
  </ows:OperationsMetadata>
  <wfs:FeatureTypeList>
    <wfs:FeatureType><wfs:Name>lor:prognoseraeume</wfs:Name>
      <wfs:Title>Prognoser\xc3\xa4ume</wfs:Title></wfs:FeatureType>
    <wfs:FeatureType><wfs:Name>lor:bezirksregionen</wfs:Name>
      <wfs:Title>Bezirksregionen</wfs:Title></wfs:FeatureType>
    <wfs:FeatureType><wfs:Name>lor:planungsraeume_2021</wfs:Name>
      <wfs:Title>Planungsr\xc3\xa4ume</wfs:Title></wfs:FeatureType>
  </wfs:FeatureTypeList>
</wfs:WFS_Capabilities>"""

COLLECTION = json.dumps({"type": "FeatureCollection", "features": []}).encode()


def test_parse_capabilities():
    parsed = sources.parse_wfs_capabilities(CAPABILITIES)
    assert parsed["version"] == "2.0.0"
    assert "application/geo+json" in parsed["formats"]
    assert [layer["name"] for layer in parsed["layers"]][-1] == "lor:planungsraeume_2021"


def test_discovers_planning_area_layer():
    layers = sources.parse_wfs_capabilities(CAPABILITIES)["layers"]
    assert sources.discover_plr_layer(layers) == "lor:planungsraeume_2021"


def test_no_matching_layer_is_an_error():
    with pytest.raises(RuntimeError, match="No WFS layer"):
        sources.discover_plr_layer([{"name": "lor:bezirksregionen", "title": "Bezirksregionen"}])


def test_tie_is_an_error():
    layers = [
        {"name": "a:planungsraum", "title": ""},
        {"name": "b:planungsraum", "title": ""},
    ]
    with pytest.raises(RuntimeError, match="Ambiguous"):
        sources.discover_plr_layer(layers)


def _info(url: str) -> sources.FetchInfo:
    return sources.FetchInfo(url, url, 200, "application/json", "0" * 64, 1, "2026-01-01T00:00:00")


def test_geojson_download_falls_back_to_next_output_format(monkeypatch):
    requested = []

    def fake_fetch(url, params=None):
        if params["request"] == "GetCapabilities":
            return CAPABILITIES, _info(url)
        requested.append(params["outputFormat"])
        if params["outputFormat"] == "application/geo+json":
            raise OSError("boom")
        return COLLECTION, _info(url)

    monkeypatch.setattr(sources, "fetch", fake_fetch)
    content, _, details = sources.fetch_plr_geojson()
    assert content == COLLECTION
    assert requested == ["application/geo+json", "application/json"]
    assert details == {
        "layer": "lor:planungsraeume_2021",
        "output_format": "application/json",
        "wfs_version": "2.0.0",
    }


def test_layer_override_skips_discovery(monkeypatch):
    seen = {}

    def fake_fetch(url, params=None):
        if params["request"] == "GetCapabilities":
            return CAPABILITIES, _info(url)
        seen.update(params)
        return COLLECTION, _info(url)

    monkeypatch.setattr(sources, "fetch", fake_fetch)
    sources.fetch_plr_geojson("my:layer")
    assert seen["typeNames"] == "my:layer"


def test_html_response_is_rejected():
    with pytest.raises(ValueError, match="HTML"):
        sources.require_not_html("text/html; charset=utf-8", "https://example.org/x")
    sources.require_not_html("text/csv", "https://example.org/x")
