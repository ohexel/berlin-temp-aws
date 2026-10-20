from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def real_population() -> bytes:
    return (FIXTURES / "EWR_L21_202512E_Matrix.csv").read_bytes()


@pytest.fixture
def real_names() -> bytes:
    return (FIXTURES / "lor_2021-01-01_k3_uebersicht_id_namen.xlsx").read_bytes()


@pytest.fixture(scope="session")
def world():
    """Synthetic grid, planning areas, population, names, mask and bridge.

    Expected counts come from a naive, independent computation (no spatial index).
    """
    import numpy as np
    import shapely
    from synthetic import grid_arrays, names_xlsx, plr_geojson, population_csv

    from berlin_temp.reference.checks import Expectations
    from berlin_temp.reference.geometry import build_bridge, build_mask
    from berlin_temp.reference.icon_grid import IconGrid, build_cells
    from berlin_temp.reference.lor import parse_plr_geojson
    from berlin_temp.reference.plr_names import parse_plr_names
    from berlin_temp.reference.population import parse_population

    arrays = grid_arrays()
    grid = IconGrid(
        lon_deg=np.rad2deg(arrays.vlon_rad),
        lat_deg=np.rad2deg(arrays.vlat_rad),
        vertex_of_cell=arrays.vertex_of_cell_1based.T - 1,
    )
    cells = build_cells(grid)
    plr = parse_plr_geojson(plr_geojson())
    buffer_m = 1000.0
    mask = build_mask(plr.geometry, cells, buffer_m)
    bridge = build_bridge(plr.plr_id, plr.geometry, cells)

    berlin = shapely.union_all(plr.geometry).buffer(buffer_m, quad_segs=8)
    naive_mask = [i for i, polygon in enumerate(cells.polygons) if polygon.intersects(berlin)]
    naive_pairs = sum(
        shapely.intersection(geometry, polygon).area > 0
        for geometry in plr.geometry
        for polygon in cells.polygons
    )
    expect = Expectations(
        plr_count=4,
        population_accepted=3,
        population_rejected=1,
        grid_vertices=len(grid.lon_deg),
        grid_cells=len(grid.vertex_of_cell),
        mask_cells=len(naive_mask),
        bridge_rows=int(naive_pairs),
        mask_buffer_m=buffer_m,
    )
    return {
        "grid": grid,
        "cells": cells,
        "plr": plr,
        "names": parse_plr_names(names_xlsx()),
        "population": parse_population(population_csv()),
        "mask": mask,
        "bridge": bridge,
        "expect": expect,
        "naive_mask": naive_mask,
    }


@pytest.fixture(autouse=True)
def no_retry_pauses(monkeypatch):
    """Retries must not slow the suite down; tests that check the pauses patch this again."""
    from berlin_temp.reference import sources

    monkeypatch.setattr(sources, "sleep", lambda seconds: None)
