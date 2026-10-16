"""Acceptance checks for the reference build.

The default numbers come from the old pipeline and are the acceptance criteria for the real data.
"""

from dataclasses import dataclass

import numpy as np
import shapely

from berlin_temp.reference.geometry import Bridge
from berlin_temp.reference.icon_grid import IconGrid
from berlin_temp.reference.lor import PlrGeometries
from berlin_temp.reference.population import PopulationRow


@dataclass(frozen=True)
class Expectations:
    plr_count: int = 542
    population_accepted: int = 540
    population_rejected: int = 2
    grid_vertices: int = 272_089
    grid_cells: int = 542_040
    mask_cells: int = 465
    bridge_rows: int = 1_678
    mask_buffer_m: float = 5000.0
    fraction_tolerance: float = 1e-6


class ReferenceCheckError(Exception):
    """One or more acceptance checks failed; the message lists all of them."""


def check_reference(
    expect: Expectations,
    *,
    plr: PlrGeometries,
    names: dict[str, str],
    population: list[PopulationRow],
    grid: IconGrid,
    mask: np.ndarray,
    bridge: Bridge,
) -> dict[str, int | float]:
    """Return summary numbers, or raise ReferenceCheckError listing every failed check."""
    problems: list[str] = []

    def expect_equal(label: str, observed: int, expected: int) -> None:
        if observed != expected:
            problems.append(f"{label}: observed {observed:,}, expected {expected:,}")

    plr_ids = set(plr.plr_id)
    population_ids = [row.plr_id for row in population]
    expect_equal("PLR count", len(plr.plr_id), expect.plr_count)
    expect_equal("PLR distinct ids", len(plr_ids), len(plr.plr_id))
    expect_equal("population rows", len(population), expect.plr_count)
    expect_equal("population distinct ids", len(set(population_ids)), len(population))
    expect_equal("PLR names", len(names), expect.plr_count)
    if set(population_ids) != plr_ids:
        problems.append("population ids differ from LOR ids")
    if set(names) != plr_ids:
        problems.append("PLR name ids differ from LOR ids")
    if len({row.reference_date for row in population}) != 1:
        problems.append("population rows have more than one reference date")

    rejected = sum(row.rejection_reason is not None for row in population)
    expect_equal("population rejected", rejected, expect.population_rejected)
    expect_equal("population accepted", len(population) - rejected, expect.population_accepted)

    invalid_plr = int((~shapely.is_valid(plr.geometry)).sum())
    expect_equal("invalid PLR geometries", invalid_plr, 0)
    expect_equal("grid vertices", len(grid.lon_deg), expect.grid_vertices)
    expect_equal("grid cells", len(grid.vertex_of_cell), expect.grid_cells)
    expect_equal("mask cells", len(mask), expect.mask_cells)
    expect_equal("bridge rows", len(bridge), expect.bridge_rows)

    represented = set(bridge.plr_id.tolist())
    if represented != plr_ids:
        problems.append(f"{len(plr_ids - represented)} PLRs have no bridge rows")
    outside_mask = set(bridge.cell_index.tolist()) - set(mask.tolist())
    if outside_mask:
        problems.append(f"{len(outside_mask)} bridge cells are not in the mask")

    totals: dict[str, float] = {}
    for plr_id, fraction in zip(bridge.plr_id, bridge.fraction_of_plr, strict=True):
        totals[plr_id] = totals.get(plr_id, 0.0) + fraction
    worst = max((abs(total - 1.0) for total in totals.values()), default=float("inf"))
    if worst > expect.fraction_tolerance:
        problems.append(f"PLR weights do not sum to 1: worst error {worst:.3g}")

    if problems:
        raise ReferenceCheckError("; ".join(problems))
    return {
        "plr_count": len(plr.plr_id),
        "population_accepted": len(population) - rejected,
        "population_rejected": rejected,
        "mask_cells": len(mask),
        "bridge_rows": len(bridge),
        "max_plr_weight_error": worst,
    }
