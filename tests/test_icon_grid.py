import numpy as np
import pytest
from synthetic import NX, NY, grid_arrays, grid_netcdf_bz2

from berlin_temp.reference.icon_grid import (
    IconGrid,
    build_cells,
    decompress_bz2,
    read_icon_grid,
)


def test_read_converts_radians_and_connectivity(tmp_path):
    archive, netcdf = tmp_path / "grid.nc.bz2", tmp_path / "grid.nc"
    archive.write_bytes(grid_netcdf_bz2(tmp_path))
    decompress_bz2(archive, netcdf)
    grid = read_icon_grid(netcdf)
    assert grid.vertex_of_cell.shape == (2 * NX * NY, 3)
    assert grid.vertex_of_cell.min() == 0  # 1-based input became 0-based
    assert grid.lon_deg.min() == pytest.approx(13.30)
    assert grid.lat_deg.max() == pytest.approx(52.45 + 0.01 * NY)


def test_build_cells_keeps_all_valid_triangles(world):
    cells = world["cells"]
    assert len(cells.cell_index) == 2 * NX * NY
    assert cells.rejected == {}
    assert (cells.area_m2 > 0).all()
    assert cells.cell_index.tolist() == list(range(2 * NX * NY))


def test_degenerate_cell_is_rejected_and_index_is_kept():
    arrays = grid_arrays()
    connectivity = arrays.vertex_of_cell_1based.T - 1
    connectivity[5] = [connectivity[5][0]] * 3  # three identical vertices
    grid = IconGrid(np.rad2deg(arrays.vlon_rad), np.rad2deg(arrays.vlat_rad), connectivity)
    cells = build_cells(grid)
    assert sum(cells.rejected.values()) == 1
    assert 5 not in cells.cell_index.tolist()
    assert len(cells.cell_index) == 2 * NX * NY - 1


def test_connectivity_index_base_is_checked(tmp_path):
    arrays = grid_arrays()
    bad = type(arrays)(arrays.vlon_rad, arrays.vlat_rad, arrays.vertex_of_cell_1based + 1)
    netcdf = tmp_path / "grid.nc"
    archive = tmp_path / "grid.nc.bz2"
    archive.write_bytes(grid_netcdf_bz2(tmp_path, bad))
    decompress_bz2(archive, netcdf)
    with pytest.raises(ValueError, match="0-based or 1-based"):
        read_icon_grid(netcdf)
