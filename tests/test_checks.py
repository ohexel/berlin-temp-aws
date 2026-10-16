from dataclasses import replace

import numpy as np
import pytest

from berlin_temp.reference.checks import ReferenceCheckError, check_reference


def _check(world, **overrides):
    arguments = {
        "plr": world["plr"],
        "names": world["names"],
        "population": world["population"],
        "grid": world["grid"],
        "mask": world["mask"],
        "bridge": world["bridge"],
        **overrides,
    }
    return check_reference(world["expect"], **arguments)


def test_consistent_world_passes(world):
    summary = _check(world)
    assert summary["population_rejected"] == 1
    assert summary["max_plr_weight_error"] < 1e-9


def test_all_problems_are_reported_together(world):
    expect = replace(world["expect"], mask_cells=1, bridge_rows=1)
    with pytest.raises(ReferenceCheckError) as error:
        check_reference(
            expect,
            plr=world["plr"],
            names=world["names"],
            population=world["population"],
            grid=world["grid"],
            mask=world["mask"],
            bridge=world["bridge"],
        )
    assert "mask cells" in str(error.value)
    assert "bridge rows" in str(error.value)


def test_name_mismatch_is_detected(world):
    names = dict(world["names"])
    names["99999999"] = names.pop("01100101")
    with pytest.raises(ReferenceCheckError, match="PLR name ids differ"):
        _check(world, names=names)


def test_weights_not_summing_to_one_are_detected(world):
    bridge = world["bridge"]
    broken = replace(bridge, fraction_of_plr=bridge.fraction_of_plr * 0.9)
    with pytest.raises(ReferenceCheckError, match="weights do not sum to 1"):
        _check(world, bridge=broken)


def test_bridge_cell_outside_mask_is_detected(world):
    mask = np.setdiff1d(world["mask"], world["bridge"].cell_index[:2])
    with pytest.raises(ReferenceCheckError, match="not in the mask"):
        _check(world, mask=mask)
