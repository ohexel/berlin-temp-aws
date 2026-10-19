"""Decode one ICON-D2-RUC GRIB2 field and check that it is the field we asked for."""

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from berlin_temp.pipeline.forecast import ForecastKey

ICON_D2_POINT_COUNT = 542_040
_MS_UNITS = frozenset({"m/s", "ms-1", "ms**-1", "ms^-1"})


@dataclass(frozen=True)
class Indicator:
    discipline: int
    category: int
    number: int
    surface_type: int
    surface_value: int
    units: frozenset[str]


INDICATOR_CONTRACTS = {
    "T_2M": Indicator(0, 0, 0, 103, 2, frozenset({"k", "kelvin"})),
    "RELHUM_2M": Indicator(0, 1, 1, 103, 2, frozenset({"%", "percent"})),
    "U_10M": Indicator(0, 2, 2, 103, 10, _MS_UNITS),
    "V_10M": Indicator(0, 2, 3, 103, 10, _MS_UNITS),
}


class GribError(ValueError):
    """The file is not the expected field."""


def _normalize_unit(unit: object) -> str:
    return str(unit).strip().lower().replace(" ", "")


def _time(handle, codes, date_key: str, time_key: str):
    date, hhmm = codes.codes_get(handle, date_key, int), codes.codes_get(handle, time_key, int)
    return datetime.strptime(f"{date:08d}{hhmm:04d}", "%Y%m%d%H%M").replace(tzinfo=UTC)


def read_field(
    path: Path,
    indicator: str,
    key: ForecastKey,
    expected_points: int = ICON_D2_POINT_COUNT,
) -> np.ndarray:
    """Return the field as float64 (NaN where the GRIB bitmap says missing). Raises GribError."""
    import eccodes as codes  # imported here: needs the ecCodes C library

    contract = INDICATOR_CONTRACTS[indicator]
    with path.open("rb") as handle_file:
        handle = codes.codes_grib_new_from_file(handle_file)
        if handle is None:
            raise GribError(f"{indicator}: no GRIB message in {path.name}")
        try:
            get = lambda name, kind=int: codes.codes_get(handle, name, kind)  # noqa: E731
            problems = []
            if get("edition") != 2:
                problems.append(f"edition {get('edition')}, expected 2")
            for name, expected in (
                ("discipline", contract.discipline),
                ("parameterCategory", contract.category),
                ("parameterNumber", contract.number),
                ("typeOfFirstFixedSurface", contract.surface_type),
                ("scaledValueOfFirstFixedSurface", contract.surface_value),
            ):
                if get(name) != expected:
                    problems.append(f"{name} {get(name)}, expected {expected}")
            unit = _normalize_unit(get("units", str))
            if unit not in contract.units:
                problems.append(f"units {unit!r}, expected one of {sorted(contract.units)}")
            if get("numberOfPoints") != expected_points:
                problems.append(f"{get('numberOfPoints')} points, expected {expected_points}")
            run_time = _time(handle, codes, "dataDate", "dataTime")
            valid_time = _time(handle, codes, "validityDate", "validityTime")
            if run_time != key.run_time:
                problems.append(f"run time {run_time:%Y-%m-%dT%H:%MZ}, expected {key.run_label}")
            if valid_time != key.valid_time:
                problems.append(
                    f"valid time {valid_time:%Y-%m-%dT%H:%MZ}, "
                    f"expected {key.valid_time:%Y-%m-%dT%H:%MZ}"
                )
            if problems:
                raise GribError(
                    f"{indicator} {key.run_label} {key.lead_label}: " + "; ".join(problems)
                )

            values = np.asarray(codes.codes_get_values(handle), dtype="float64").copy()
            missing = (
                get("numberOfMissing") if codes.codes_is_defined(handle, "numberOfMissing") else 0
            )
            if missing:
                bitmap = (
                    get("bitmapPresent") if codes.codes_is_defined(handle, "bitmapPresent") else 0
                )
                marker = get("missingValue", float)
                is_missing = values == marker
                if bitmap != 1 or int(is_missing.sum()) != missing:
                    raise GribError(
                        f"{indicator}: inconsistent missing values (numberOfMissing={missing}, "
                        f"bitmap={bitmap}, markers={int(is_missing.sum())})"
                    )
                values[is_missing] = np.nan
            return values
        finally:
            codes.codes_release(handle)
