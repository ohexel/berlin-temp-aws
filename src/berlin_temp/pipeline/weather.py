"""Per-cell weather arithmetic. Apparent temperature is computed per cell, before any weighting."""

import numpy as np

KELVIN_OFFSET = 273.15


def wind_speed(u: np.ndarray, v: np.ndarray) -> np.ndarray:
    return np.sqrt(u**2 + v**2)


def apparent_temperature_c(
    temperature_c: np.ndarray, relative_humidity_percent: np.ndarray, wind_speed_ms: np.ndarray
) -> np.ndarray:
    """Apparent temperature in the shade (Steadman-type, no radiation term).

    Same formula as the old repo's SQL function calculate_apparent_temperature_shade_c.
    """
    vapour_pressure = (
        (relative_humidity_percent / 100.0)
        * 6.105
        * np.exp(17.27 * temperature_c / (237.7 + temperature_c))
    )
    return temperature_c + 0.33 * vapour_pressure - 0.70 * wind_speed_ms - 4.00
