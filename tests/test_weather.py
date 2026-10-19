import numpy as np
import pytest

from berlin_temp.pipeline.weather import apparent_temperature_c, wind_speed


def test_apparent_temperature_matches_old_repo_fixture():
    # Fixture from the old repo's SQL test: 30 C, 70 % humidity, wind components 3 and 4 m/s.
    wind = wind_speed(np.array([3.0]), np.array([4.0]))
    assert wind[0] == 5.0
    result = apparent_temperature_c(np.array([30.0]), np.array([70.0]), wind)
    assert result[0] == pytest.approx(32.26833429149539, abs=1e-9)


def test_apparent_temperature_is_per_cell():
    result = apparent_temperature_c(np.array([0.0, 30.0]), np.array([50.0, 70.0]), np.zeros(2))
    assert result.shape == (2,)
    assert result[0] < result[1]
