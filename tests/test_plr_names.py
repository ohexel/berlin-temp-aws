import pytest
from synthetic import PLR_IDS, names_xlsx

from berlin_temp.reference.plr_names import parse_plr_names


def test_real_workbook(real_names):
    names = parse_plr_names(real_names)
    assert len(names) == 542
    assert names["01100101"] == "Stülerstraße"


def test_synthetic_workbook():
    assert list(parse_plr_names(names_xlsx())) == PLR_IDS


def test_missing_worksheet():
    with pytest.raises(ValueError, match="missing worksheet"):
        parse_plr_names(names_xlsx(sheet="other"))


@pytest.mark.parametrize(
    ("ids", "message"),
    [(["1234"], "eight digits"), (["01100101", "01100101"], "duplicate")],
)
def test_bad_identifiers(ids, message):
    with pytest.raises(ValueError, match=message):
        parse_plr_names(names_xlsx(ids))
