"""Berlin mask and the PLR-to-cell area bridge."""

from dataclasses import dataclass

import numpy as np
import shapely
from shapely import STRtree

from berlin_temp.reference.icon_grid import IconCells

# PostGIS default (the old pipeline's ST_Buffer): 8 segments per quarter circle.
BUFFER_QUARTER_SEGMENTS = 8


@dataclass(frozen=True)
class Bridge:
    plr_id: np.ndarray
    cell_index: np.ndarray
    intersection_area_m2: np.ndarray
    plr_area_m2: np.ndarray
    icon_cell_area_m2: np.ndarray
    fraction_of_plr: np.ndarray
    fraction_of_icon_cell: np.ndarray

    def __len__(self) -> int:
        return len(self.plr_id)


def build_mask(plr_geometries: np.ndarray, cells: IconCells, buffer_m: float) -> np.ndarray:
    """Cell indices of all cells that touch Berlin's outline widened by `buffer_m`."""
    berlin = shapely.union_all(plr_geometries).buffer(buffer_m, quad_segs=BUFFER_QUARTER_SEGMENTS)
    hits = STRtree(cells.polygons).query(berlin, predicate="intersects")
    return np.sort(cells.cell_index[hits])


def build_bridge(plr_ids: list[str], plr_geometries: np.ndarray, cells: IconCells) -> Bridge:
    """One row per PLR/cell pair with positive overlap area."""
    tree = STRtree(cells.polygons)
    plr_area = shapely.area(plr_geometries)
    columns: dict[str, list[np.ndarray]] = {
        name: [] for name in ("plr", "cell", "inter", "plr_area", "cell_area", "f_plr", "f_cell")
    }
    for position, geometry in enumerate(plr_geometries):
        candidates = tree.query(geometry, predicate="intersects")
        overlap = shapely.area(shapely.intersection(geometry, cells.polygons[candidates]))
        positive = overlap > 0
        candidates, overlap = candidates[positive], overlap[positive]
        cell_area = cells.area_m2[candidates]
        count = len(candidates)
        columns["plr"].append(np.full(count, plr_ids[position], dtype=object))
        columns["cell"].append(cells.cell_index[candidates])
        columns["inter"].append(overlap)
        columns["plr_area"].append(np.full(count, plr_area[position]))
        columns["cell_area"].append(cell_area)
        columns["f_plr"].append(overlap / plr_area[position])
        columns["f_cell"].append(overlap / cell_area)
    joined = {name: np.concatenate(parts) for name, parts in columns.items()}
    return Bridge(
        plr_id=joined["plr"],
        cell_index=joined["cell"],
        intersection_area_m2=joined["inter"],
        plr_area_m2=joined["plr_area"],
        icon_cell_area_m2=joined["cell_area"],
        fraction_of_plr=joined["f_plr"],
        fraction_of_icon_cell=joined["f_cell"],
    )
