import json
import ssl
import urllib.error

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


def test_require_not_html_checks_content_prefix():
    with pytest.raises(ValueError, match="HTML page"):
        sources.require_not_html("text/csv", "u", content=b"  <!DOCTYPE html><html>")
    sources.require_not_html("text/csv", "u", content=b"a;b\n1;2\n")


# --- gentle retry for connection errors ---


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (ConnectionResetError(104, "reset"), True),
        (TimeoutError(), True),
        (urllib.error.URLError(ConnectionResetError(104, "reset")), True),
        (urllib.error.URLError(TimeoutError("_ssl.c:1064: handshake timed out")), True),
        (urllib.error.URLError(ssl.SSLError("handshake failure")), True),
        (urllib.error.URLError(ssl.SSLCertVerificationError("bad certificate")), False),
        (urllib.error.URLError("dns"), False),
        (urllib.error.HTTPError("u", 404, "not found", None, None), False),
        (urllib.error.HTTPError("u", 503, "busy", None, None), False),
        (ValueError("not a GRIB"), False),
    ],
)
def test_is_connection_error(error, expected):
    assert sources.is_connection_error(error) is expected


def _flaky(failures, error=None):
    error = error or ConnectionResetError(104, "reset")
    calls = []

    def call():
        calls.append(1)
        if len(calls) <= failures:
            raise error
        return "ok"

    return call, calls


def test_two_retries_then_success_with_jittered_pauses(monkeypatch, caplog):
    pauses = []
    monkeypatch.setattr(sources, "sleep", pauses.append)
    call, calls = _flaky(2)
    assert sources.with_retries(call, "GET x") == "ok"
    assert len(calls) == 3
    assert 1.0 <= pauses[0] <= 3.0 and 2.5 <= pauses[1] <= 7.5
    assert caplog.text.count("retry") == 2 and "GET x failed" in caplog.text


def test_pauses_are_randomised(monkeypatch):
    seen = []
    monkeypatch.setattr(sources, "sleep", seen.append)
    for _ in range(20):
        sources.with_retries(_flaky(1)[0], "GET x")
    assert len(set(seen)) > 1


def test_gives_up_after_three_attempts_and_raises_the_last_error():
    call, calls = _flaky(10)
    with pytest.raises(ConnectionResetError):
        sources.with_retries(call, "GET x")
    assert len(calls) == 3


@pytest.mark.parametrize(
    "error",
    [
        urllib.error.HTTPError("u", 404, "not found", None, None),
        urllib.error.URLError(ssl.SSLCertVerificationError("bad certificate")),
        ValueError("not a GRIB"),
    ],
)
def test_other_errors_are_not_retried(error):
    call, calls = _flaky(10, error)
    with pytest.raises(type(error)):
        sources.with_retries(call, "GET x")
    assert len(calls) == 1


class _Download:
    status = 200
    headers = {"Content-Type": "application/octet-stream"}

    def __init__(self, chunks, fail_after_first=False):
        self.chunks = list(chunks)
        self.fail_after_first = fail_after_first

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def geturl(self):
        return "https://example.org/x"

    def read(self, size=-1):
        if self.fail_after_first and not self.chunks:
            raise ConnectionResetError(104, "reset")
        return self.chunks.pop(0) if self.chunks else b""


def test_fetch_retries_a_reset(monkeypatch):
    attempts = []

    def fake_urlopen(request, timeout):
        attempts.append(1)
        if len(attempts) == 1:
            raise urllib.error.URLError(ConnectionResetError(104, "reset"))
        return _Download([b"abc", b""])

    monkeypatch.setattr(sources.urllib.request, "urlopen", fake_urlopen)
    content, info = sources.fetch("https://example.org/x")
    assert content == b"abc" and info.size_bytes == 3 and len(attempts) == 2


def test_fetch_to_file_restarts_after_a_reset_mid_download(monkeypatch, tmp_path):
    attempts = []

    def fake_urlopen(request, timeout):
        attempts.append(1)
        if len(attempts) == 1:
            return _Download([b"partial"], fail_after_first=True)
        return _Download([b"complete", b""])

    monkeypatch.setattr(sources.urllib.request, "urlopen", fake_urlopen)
    target = tmp_path / "file.grib2"
    info = sources.fetch_to_file("https://example.org/x", target)
    assert target.read_bytes() == b"complete" and info.size_bytes == 8 and len(attempts) == 2
