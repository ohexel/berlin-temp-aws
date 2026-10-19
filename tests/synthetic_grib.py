"""Synthetic GRIB2 files for tests (small regular lat/lon grid, ICON-D2-RUC style metadata)."""

from pathlib import Path

import numpy as np

# indicator -> (discipline, parameterCategory, parameterNumber, surface value in m)
_PARAMETERS = {
    "T_2M": (0, 0, 0, 2),
    "RELHUM_2M": (0, 1, 1, 2),
    "U_10M": (0, 2, 2, 10),
    "V_10M": (0, 2, 3, 10),
}
NI, NJ = 10, 6  # 60 grid points


def eccodes_available() -> bool:
    try:
        import eccodes  # noqa: F401
    except ImportError, RuntimeError:  # RuntimeError: ecCodes C library not found
        return False
    return True


def write_grib(
    path: Path,
    indicator: str,
    run_time,
    lead_hours: int,
    values: np.ndarray,
    *,
    missing_at: tuple[int, ...] = (),
    parameter_number: int | None = None,
    ni: int = NI,
) -> Path:
    import eccodes as codes

    discipline, category, number, surface = _PARAMETERS[indicator]
    handle = codes.codes_grib_new_from_samples("regular_ll_sfc_grib2")
    try:
        settings = {
            "discipline": discipline,
            "parameterCategory": category,
            "parameterNumber": number if parameter_number is None else parameter_number,
            "typeOfFirstFixedSurface": 103,
            "scaleFactorOfFirstFixedSurface": 0,
            "scaledValueOfFirstFixedSurface": surface,
            "dataDate": int(run_time.strftime("%Y%m%d")),
            "dataTime": int(run_time.strftime("%H%M")),
            "stepUnits": 1,
            "forecastTime": lead_hours,
            "Ni": ni,
            "Nj": NJ,
        }
        for name, value in settings.items():
            codes.codes_set(handle, name, value)
        if missing_at:
            codes.codes_set(handle, "bitmapPresent", 1)
        marker = codes.codes_get(handle, "missingValue", float)
        data = np.asarray(values, dtype=float).copy()
        data[list(missing_at)] = marker
        codes.codes_set_values(handle, data)
        path.write_bytes(codes.codes_get_message(handle))
    finally:
        codes.codes_release(handle)
    return path
