"""Download the four static sources from their public URLs."""

import hashlib
import json
import logging
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from random import uniform
from time import sleep

LOGGER = logging.getLogger(__name__)

TIMEOUT_SECONDS = 120
USER_AGENT = "berlin-temp-aws/0.1"

# Gentle retry for connection-level failures: two retries (three attempts), each after a pause
# of 2 s and 5 s, randomised by +/-50 % so parallel workers do not retry in lockstep.
RETRY_PAUSES_SECONDS = (2.0, 5.0)
RETRY_JITTER = 0.5

# The AfS "published resource" URL (statistik-berlin-brandenburg.de/opendata/...) now answers with
# an HTML page; the download host below serves the CSV itself.
AFS_PUBLISHED_RESOURCE_URL = (
    "https://www.statistik-berlin-brandenburg.de/opendata/EWR_L21_202512E_Matrix.csv"
)
AFS_POPULATION_URL = (
    "https://download.statistik-berlin-brandenburg.de/"
    "c9771e49e9b212b3/f6a9c1df6163/EWR_L21_202512E_Matrix.csv"
)
PLR_NAMES_FILENAME = "lor_2021-01-01_k3_uebersicht_id_namen.xlsx"
PLR_NAMES_URL = (
    "https://www.berlin.de/sen/stadt/_assets/stadtdaten/stadtwissen/"
    f"lebensweltlich-orientierte-raeume/{PLR_NAMES_FILENAME}?ts=1780323170"
)
LOR_WFS_URL = "https://gdi.berlin.de/services/wfs/lor_2021"
ICON_GRID_URL = "https://opendata.dwd.de/weather/lib/cdo/icon_grid_0047_R19B07_L.nc.bz2"

PLR_LAYER_ALIASES = ("planungsraum", "planungsraeume", "plr")


@dataclass(frozen=True)
class FetchInfo:
    """Provenance of one downloaded file; stored next to it in bronze."""

    source_url: str
    resolved_url: str
    http_status: int
    content_type: str | None
    sha256: str
    size_bytes: int
    retrieved_at_utc: str

    def to_json(self, **extra: object) -> bytes:
        return json.dumps({**asdict(self), **extra}, indent=2, sort_keys=True).encode()


def is_connection_error(error: BaseException) -> bool:
    """Connection reset, timeout or TLS handshake failure (not HTTP answers or bad certificates)."""
    if isinstance(error, urllib.error.HTTPError):
        return False
    if isinstance(error, urllib.error.URLError):
        error = error.reason  # may be an exception or a plain string
    if isinstance(error, ssl.SSLCertVerificationError):
        return False
    return isinstance(error, ConnectionError | TimeoutError | ssl.SSLError)


def with_retries[T](call: Callable[[], T], what: str) -> T:
    """Call once, then retry after each pause in RETRY_PAUSES_SECONDS if it fails to connect.

    Every retry is logged. After the last attempt, or for any other error, the error is raised.
    """
    for attempt, pause in enumerate((*RETRY_PAUSES_SECONDS, None), start=1):
        try:
            return call()
        except Exception as error:
            if pause is None or not is_connection_error(error):
                raise
            delay = pause * uniform(1 - RETRY_JITTER, 1 + RETRY_JITTER)
            LOGGER.warning(
                "%s failed (%s); retry %d of %d in %.1f s",
                what,
                error,
                attempt,
                len(RETRY_PAUSES_SECONDS),
                delay,
            )
            sleep(delay)
    raise AssertionError("unreachable")  # the last pause is None, so the loop always returns/raises


def _request(url: str) -> urllib.request.Request:
    return urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})


def fetch(url: str, params: dict[str, str] | None = None) -> tuple[bytes, FetchInfo]:
    """Download a small file into memory."""
    full_url = url if not params else f"{url}?{urllib.parse.urlencode(params)}"

    def download() -> tuple[bytes, FetchInfo]:
        with urllib.request.urlopen(_request(full_url), timeout=TIMEOUT_SECONDS) as response:
            content = response.read()
            info = FetchInfo(
                source_url=full_url,
                resolved_url=response.geturl(),
                http_status=response.status,
                content_type=response.headers.get("Content-Type"),
                sha256=hashlib.sha256(content).hexdigest(),
                size_bytes=len(content),
                retrieved_at_utc=datetime.now(UTC).isoformat(timespec="seconds"),
            )
        return content, info

    return with_retries(download, f"GET {full_url}")


def fetch_to_file(url: str, target: Path) -> FetchInfo:
    """Stream a large file to disk. A retry starts the download again and overwrites the file."""

    def download() -> FetchInfo:
        digest = hashlib.sha256()
        size = 0
        with urllib.request.urlopen(_request(url), timeout=TIMEOUT_SECONDS) as response:
            with target.open("wb") as writer:
                while chunk := response.read(1024 * 1024):
                    writer.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
            return FetchInfo(
                source_url=url,
                resolved_url=response.geturl(),
                http_status=response.status,
                content_type=response.headers.get("Content-Type"),
                sha256=digest.hexdigest(),
                size_bytes=size,
                retrieved_at_utc=datetime.now(UTC).isoformat(timespec="seconds"),
            )

    return with_retries(download, f"GET {url}")


def require_not_html(
    content_type: str | None,
    url: str,
    resolved_url: str | None = None,
    content: bytes | None = None,
) -> None:
    """Fail if a download is an HTML page (by Content-Type or by its first bytes)."""
    html_type = bool(content_type) and "html" in content_type.lower()
    head = (content or b"")[:500].lstrip().lower()
    if html_type or head.startswith((b"<html", b"<!doctype html")):
        raise ValueError(
            f"{url} (resolved to {resolved_url or url}) returned an HTML page "
            "instead of the expected file"
        )


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child_text(element: ET.Element, wanted: str) -> str | None:
    for child in element:
        if _local_name(child.tag) == wanted and child.text:
            return child.text.strip()
    return None


def parse_wfs_capabilities(xml_bytes: bytes) -> dict:
    """Return the WFS version, the advertised GetFeature output formats and the layers."""
    root = ET.fromstring(xml_bytes)
    formats: list[str] = []
    for element in root.iter():
        is_format_parameter = (
            _local_name(element.tag) == "Parameter"
            and element.attrib.get("name", "").lower() == "outputformat"
        )
        if is_format_parameter:
            for value in element.iter():
                text = (value.text or "").strip()
                if _local_name(value.tag) in {"Value", "DefaultValue"} and text:
                    if text not in formats:
                        formats.append(text)
    layers = [
        {"name": name, "title": _child_text(element, "Title") or ""}
        for element in root.iter()
        if _local_name(element.tag) == "FeatureType"
        if (name := _child_text(element, "Name"))
    ]
    return {"version": root.attrib.get("version", "2.0.0"), "formats": formats, "layers": layers}


def _normalise(text: str) -> str:
    table = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})
    return re.sub(r"\s+", " ", text.lower().translate(table)).strip()


def score_layer(layer: dict, aliases: tuple[str, ...] = PLR_LAYER_ALIASES) -> int:
    name, title = _normalise(layer["name"]), _normalise(layer["title"])
    combined = f"{name} {title}"
    score = 0
    for alias in aliases:
        if re.search(rf"(^|[^a-z0-9]){re.escape(alias)}([^a-z0-9]|$)", combined):
            score += 10
        elif alias in combined:
            score += 3
        if alias == name.split(":")[-1]:
            score += 10
    return score


def discover_plr_layer(layers: list[dict]) -> str:
    """Pick the planning-area layer by name; fail on no match or a tie."""
    scored = sorted(
        ((score_layer(layer), layer["name"]) for layer in layers if score_layer(layer) > 0),
        reverse=True,
    )
    if not scored:
        raise RuntimeError("No WFS layer looks like the planning areas (Planungsraum)")
    if len(scored) > 1 and scored[0][0] == scored[1][0]:
        tied = [name for score, name in scored if score == scored[0][0]]
        raise RuntimeError(f"Ambiguous WFS layers for planning areas: {tied}; pass plr_layer")
    return scored[0][1]


def fetch_plr_geojson(layer_override: str | None = None) -> tuple[bytes, FetchInfo, dict]:
    """Download the planning-area layer as GeoJSON; returns content, provenance, details."""
    capabilities, _ = fetch(LOR_WFS_URL, {"service": "WFS", "request": "GetCapabilities"})
    parsed = parse_wfs_capabilities(capabilities)
    layer = layer_override or discover_plr_layer(parsed["layers"])
    version = parsed["version"]
    type_parameter = "typeNames" if version.startswith("2") else "typeName"
    json_formats = [value for value in parsed["formats"] if "json" in value.lower()]
    candidates = list(
        dict.fromkeys(json_formats + ["application/json", "application/json; subtype=geojson"])
    )

    errors: list[str] = []
    for output_format in candidates:
        params = {
            "service": "WFS",
            "version": version,
            "request": "GetFeature",
            type_parameter: layer,
            "outputFormat": output_format,
        }
        try:
            content, info = fetch(LOR_WFS_URL, params)
            collection = json.loads(content)
            if collection.get("type") != "FeatureCollection" or "features" not in collection:
                raise ValueError("response is JSON but not a GeoJSON FeatureCollection")
        except (OSError, ValueError) as error:
            errors.append(f"{output_format}: {error}")
            continue
        details = {"layer": layer, "output_format": output_format, "wfs_version": version}
        return content, info, details
    raise RuntimeError(f"Could not download layer {layer!r} as GeoJSON: {' | '.join(errors)}")
