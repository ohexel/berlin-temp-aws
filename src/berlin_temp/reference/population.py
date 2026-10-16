"""Parse and classify the AfS population CSV (one row per planning area)."""

import csv
import io
import re
from dataclasses import dataclass
from datetime import date, timedelta

REQUIRED_COLUMNS = ("RAUMID", "E_E", "E_E65U80", "E_E80U110", "ZEIT")
_INTEGER = re.compile(r"^-?[0-9]+$")


@dataclass(frozen=True)
class PopulationRow:
    plr_id: str | None
    population_total: int | None
    population_65_79: int | None
    population_80plus: int | None
    population_65plus: int | None
    share_65plus: float | None
    rejection_reason: str | None
    reference_date: date


def _text_or_none(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _integer(text: str | None) -> tuple[int | None, bool]:
    """Return (value, invalid). Blank text is missing, not invalid."""
    if text is None:
        return None, False
    if _INTEGER.match(text):
        return int(text), False
    return None, True


def reference_date(code: str) -> date:
    """Last day of the month named by a code such as '202512'."""
    year, month = int(code[:4]), int(code[4:6])
    first_of_next_month = date(year + month // 12, month % 12 + 1, 1)
    return first_of_next_month - timedelta(days=1)


def classify(
    total: int | None,
    total_invalid: bool,
    p65_79: int | None,
    p65_79_invalid: bool,
    p80plus: int | None,
    p80plus_invalid: bool,
) -> str | None:
    """Return the rejection reason, or None if the row is accepted.

    The order of the checks is the order of precedence.
    """
    p65plus = None if p65_79 is None or p80plus is None else p65_79 + p80plus

    if total is None and not total_invalid:
        return "missing_population_total"
    if total_invalid:
        return "invalid_population_total"
    if (p65_79 is None and not p65_79_invalid) or (p80plus is None and not p80plus_invalid):
        return "missing_population_65plus_component"
    if p65_79_invalid or p80plus_invalid:
        return "invalid_population_65plus_component"
    assert total is not None and p65_79 is not None and p80plus is not None
    assert p65plus is not None
    if total < 0:
        return "negative_population_total"
    if total == 0:
        return "zero_population_total"
    if min(p65_79, p80plus, p65plus) < 0 or max(p65_79, p80plus, p65plus) > total:
        return "invalid_population_65plus"
    return None


def parse_population(content: bytes) -> list[PopulationRow]:
    reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig"), newline=""), delimiter=";")
    missing = [column for column in REQUIRED_COLUMNS if column not in (reader.fieldnames or [])]
    if missing:
        raise ValueError("AfS population CSV is missing required columns: " + ", ".join(missing))

    rows = []
    for source in reader:
        values = {column: _text_or_none(source.get(column)) for column in REQUIRED_COLUMNS}
        total, total_invalid = _integer(values["E_E"])
        p65_79, p65_79_invalid = _integer(values["E_E65U80"])
        p80plus, p80plus_invalid = _integer(values["E_E80U110"])
        reason = classify(total, total_invalid, p65_79, p65_79_invalid, p80plus, p80plus_invalid)
        p65plus = None if p65_79 is None or p80plus is None else p65_79 + p80plus
        share = p65plus / total if reason is None and p65plus is not None and total else None
        zeit = values["ZEIT"]
        if zeit is None:
            raise ValueError(f"AfS population row {values['RAUMID']!r} has no ZEIT value")
        rows.append(
            PopulationRow(
                plr_id=values["RAUMID"],
                population_total=total,
                population_65_79=p65_79,
                population_80plus=p80plus,
                population_65plus=p65plus,
                share_65plus=share,
                rejection_reason=reason,
                reference_date=reference_date(zeit),
            )
        )
    return rows
