"""Identity of one forecast partition (run time + lead) and its DWD source files.

Labels are DWD-native everywhere: runs as ``2026-10-13T12:00`` in DWD URLs
(``...T12:00Z`` in messages), leads as ``PT005H00M``.
"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

BASE_URL = "https://opendata.dwd.de/weather/nwp/v1/m/icon-d2-ruc/p"
INDICATORS = ("T_2M", "RELHUM_2M", "U_10M", "V_10M")
LEAD_COUNT = 25  # PT000H00M .. PT024H00M

_DWD_RUN_FORMAT = "%Y-%m-%dT%H:%M"
_MESSAGE_RUN_FORMAT = "%Y-%m-%dT%H:%MZ"
_KEY_RUN_FORMAT = "%Y%m%dT%H%MZ"
_LEAD_PATTERN = re.compile(r"PT(\d{3})H00M")


def parse_run_label(label: str) -> datetime:
    """Message form, e.g. 2026-10-13T12:00Z."""
    return datetime.strptime(label, _MESSAGE_RUN_FORMAT).replace(tzinfo=UTC)


def run_label(run_time: datetime) -> str:
    """Message and DynamoDB form, e.g. 2026-10-13T12:00Z."""
    return run_time.strftime(_MESSAGE_RUN_FORMAT)


def run_key_label(run_time: datetime) -> str:
    """S3 key form, e.g. 20261013T1200Z."""
    return run_time.strftime(_KEY_RUN_FORMAT)


def format_lead(hours: int) -> str:
    if not 0 <= hours < LEAD_COUNT:
        raise ValueError(f"lead hours must be 0..{LEAD_COUNT - 1}, got {hours}")
    return f"PT{hours:03d}H00M"


def parse_lead(label: str) -> int:
    match = _LEAD_PATTERN.fullmatch(label)
    if match is None:
        raise ValueError(f"lead must look like 'PT005H00M', got {label!r}")
    hours = int(match.group(1))
    format_lead(hours)  # range check
    return hours


@dataclass(frozen=True)
class ForecastKey:
    run_time: datetime  # UTC
    lead_hours: int

    def __post_init__(self) -> None:
        if self.run_time.tzinfo is None or self.run_time.utcoffset() != timedelta(0):
            raise ValueError("run_time must be timezone-aware UTC")
        format_lead(self.lead_hours)

    @classmethod
    def from_message(cls, message: dict) -> ForecastKey:
        """Message: {"run_time": "2026-10-13T12:00Z", "lead": "PT005H00M"}."""
        return cls(parse_run_label(message["run_time"]), parse_lead(message["lead"]))

    @property
    def run_label(self) -> str:
        """As written in messages and DynamoDB keys, e.g. 2026-10-13T12:00Z."""
        return run_label(self.run_time)

    @property
    def lead_label(self) -> str:
        return format_lead(self.lead_hours)

    @property
    def valid_time(self) -> datetime:
        return self.run_time + timedelta(hours=self.lead_hours)

    @property
    def partition(self) -> str:
        """S3 key fragment, e.g. run=20261013T1200Z/lead=PT005H00M."""
        return f"run={run_key_label(self.run_time)}/lead={self.lead_label}"

    def field_url(self, indicator: str) -> str:
        if indicator not in INDICATORS:
            raise ValueError(f"unsupported indicator {indicator!r}")
        run = self.run_time.strftime(_DWD_RUN_FORMAT)
        return f"{BASE_URL}/{indicator}/r/{run}/s/{self.lead_label}.grib2"
