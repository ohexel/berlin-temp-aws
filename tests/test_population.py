from collections import Counter
from datetime import date

import pytest
from synthetic import population_csv

from berlin_temp.reference.population import classify, parse_population, reference_date


def test_real_file_matches_old_pipeline(real_population):
    rows = parse_population(real_population)
    reasons = Counter(row.rejection_reason for row in rows)
    assert len(rows) == 542
    assert reasons == {None: 540, "invalid_population_total": 2}
    assert {row.reference_date for row in rows} == {date(2025, 12, 31)}


def test_accepted_row_has_derived_values():
    rows = parse_population(population_csv())
    first = rows[0]
    assert first.population_65plus == 200
    assert first.share_65plus == pytest.approx(0.2)
    assert first.rejection_reason is None


def test_rejected_row_has_no_share():
    rejected = parse_population(population_csv())[1]
    assert rejected.rejection_reason == "invalid_population_total"
    assert rejected.share_65plus is None


@pytest.mark.parametrize(
    ("args", "reason"),
    [
        ((None, False, 1, False, 1, False), "missing_population_total"),
        ((None, True, 1, False, 1, False), "invalid_population_total"),
        ((10, False, None, False, 1, False), "missing_population_65plus_component"),
        ((10, False, None, True, 1, False), "invalid_population_65plus_component"),
        ((-5, False, 1, False, 1, False), "negative_population_total"),
        ((0, False, 0, False, 0, False), "zero_population_total"),
        ((10, False, 8, False, 8, False), "invalid_population_65plus"),
        ((10, False, -1, False, 1, False), "invalid_population_65plus"),
        ((10, False, 3, False, 4, False), None),
    ],
)
def test_classification_order(args, reason):
    assert classify(*args) == reason


def test_reference_date_handles_year_end_and_leap_year():
    assert reference_date("202512") == date(2025, 12, 31)
    assert reference_date("202402") == date(2024, 2, 29)


def test_missing_column_is_an_error():
    with pytest.raises(ValueError, match="missing required columns"):
        parse_population(b'"ZEIT";"RAUMID"\n"202512";"1"\n')
