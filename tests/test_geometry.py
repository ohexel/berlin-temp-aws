import numpy as np
import pytest
import shapely

from berlin_temp.reference.geometry import build_bridge, build_mask


def test_mask_matches_naive_computation(world):
    assert world["mask"].tolist() == world["naive_mask"]


def test_mask_without_buffer_is_subset_of_buffered_mask(world):
    tight = build_mask(world["plr"].geometry, world["cells"], 0.0)
    assert set(tight.tolist()) < set(world["mask"].tolist())


def test_bridge_row_count_matches_naive_pair_count(world):
    assert len(world["bridge"]) == world["expect"].bridge_rows


def test_bridge_weights_sum_to_one_per_plr(world):
    bridge = world["bridge"]
    for plr_id in world["plr"].plr_id:
        selected = bridge.plr_id == plr_id
        assert bridge.fraction_of_plr[selected].sum() == pytest.approx(1.0, abs=1e-9)
        assert bridge.intersection_area_m2[selected].sum() == pytest.approx(
            bridge.plr_area_m2[selected][0]
        )


def test_bridge_fractions_are_valid(world):
    bridge = world["bridge"]
    assert (bridge.fraction_of_plr > 0).all()
    assert (bridge.fraction_of_icon_cell <= 1 + 1e-9).all()
    assert set(bridge.cell_index.tolist()) <= set(world["mask"].tolist())


def test_bridge_areas_match_geometry(world):
    bridge, cells = world["bridge"], world["cells"]
    position = {int(index): row for row, index in enumerate(cells.cell_index)}
    plr = dict(zip(world["plr"].plr_id, world["plr"].geometry, strict=True))
    for plr_id, cell_index, area in zip(
        bridge.plr_id[:20], bridge.cell_index[:20], bridge.intersection_area_m2[:20], strict=True
    ):
        expected = shapely.intersection(plr[plr_id], cells.polygons[position[int(cell_index)]]).area
        assert area == pytest.approx(expected)


def test_empty_candidate_set_gives_no_rows(world):
    far_away = np.array([shapely.box(0, 0, 10, 10)], dtype=object)
    bridge = build_bridge(["99999999"], far_away, world["cells"])
    assert len(bridge) == 0
