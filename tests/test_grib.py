from datetime import UTC, datetime

import numpy as np
import pytest
from synthetic_grib import NI, NJ, eccodes_available, write_grib

from berlin_temp.pipeline.forecast import ForecastKey
from berlin_temp.pipeline.grib import GribError, read_field

pytestmark = pytest.mark.skipif(not eccodes_available(), reason="ecCodes C library not installed")

RUN = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
KEY = ForecastKey(RUN, 5)
POINTS = NI * NJ
VALUES = np.arange(POINTS, dtype=float) + 280.0


@pytest.mark.parametrize("indicator", ["T_2M", "RELHUM_2M", "U_10M", "V_10M"])
def test_reads_each_indicator(tmp_path, indicator):
    path = write_grib(tmp_path / "f.grib2", indicator, RUN, 5, VALUES)
    values = read_field(path, indicator, KEY, POINTS)
    assert values.shape == (POINTS,)
    np.testing.assert_allclose(values, VALUES, atol=1e-2)  # GRIB packing precision


def test_wrong_parameter_is_rejected(tmp_path):
    path = write_grib(tmp_path / "f.grib2", "T_2M", RUN, 5, VALUES)
    with pytest.raises(GribError, match="parameterCategory"):
        read_field(path, "RELHUM_2M", KEY, POINTS)


def test_wrong_parameter_number_is_rejected(tmp_path):
    path = write_grib(tmp_path / "f.grib2", "U_10M", RUN, 5, VALUES, parameter_number=3)
    with pytest.raises(GribError, match="parameterNumber"):
        read_field(path, "U_10M", KEY, POINTS)


def test_wrong_run_or_lead_is_rejected(tmp_path):
    path = write_grib(tmp_path / "f.grib2", "T_2M", RUN, 5, VALUES)
    with pytest.raises(GribError, match="run time"):
        read_field(path, "T_2M", ForecastKey(datetime(2026, 10, 13, 13, 0, tzinfo=UTC), 5), POINTS)
    with pytest.raises(GribError, match="valid time"):
        read_field(path, "T_2M", ForecastKey(RUN, 6), POINTS)


def test_wrong_point_count_is_rejected(tmp_path):
    path = write_grib(tmp_path / "f.grib2", "T_2M", RUN, 5, VALUES)
    with pytest.raises(GribError, match="points"):
        read_field(path, "T_2M", KEY, POINTS + 1)


def test_bitmap_missing_values_become_nan(tmp_path):
    path = write_grib(tmp_path / "f.grib2", "T_2M", RUN, 5, VALUES, missing_at=(3, 7))
    values = read_field(path, "T_2M", KEY, POINTS)
    assert np.isnan(values[[3, 7]]).all()
    assert np.isfinite(np.delete(values, [3, 7])).all()


def test_not_a_grib_file(tmp_path):
    path = tmp_path / "f.grib2"
    path.write_bytes(b"<html>nope</html>")
    with pytest.raises(GribError, match="no GRIB message"):
        read_field(path, "T_2M", KEY, POINTS)
