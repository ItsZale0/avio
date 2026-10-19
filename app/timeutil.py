"""Utility per date e fusi orari.

Convenzione: nel database salviamo sempre UTC in ISO 8601. L'interfaccia
mostra l'ora locale italiana. Le date senza fuso inviate dalla UI sono
interpretate come Europe/Rome.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

ROME = ZoneInfo("Europe/Rome")

GIORNI = ["lun", "mar", "mer", "gio", "ven", "sab", "dom"]
MESI = [
    "gen", "feb", "mar", "apr", "mag", "giu",
    "lug", "ago", "set", "ott", "nov", "dic",
]


def parse_dt(value: str | datetime | None) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ROME)
    return dt


def to_utc(value: str | datetime | None) -> datetime | None:
    dt = parse_dt(value)
    return dt.astimezone(timezone.utc) if dt else None


def iso(value: str | datetime | None) -> str | None:
    dt = to_utc(value)
    return dt.isoformat() if dt else None


def from_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    return parse_dt(value)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_rome() -> datetime:
    return datetime.now(ROME)


def human_rome(value: str | datetime | None, fmt: str = "%a %d %b, %H:%M") -> str:
    dt = parse_dt(value)
    if not dt:
        return "-"
    local = dt.astimezone(ROME)
    # strftime usa la locale di sistema (spesso inglese): sostituiamo i nomi.
    text = local.strftime(fmt)
    text = text.replace(local.strftime("%a"), GIORNI[local.weekday()])
    text = text.replace(local.strftime("%b"), MESI[local.month - 1])
    return text


def ceil_minute(dt: datetime, minutes: int = 1) -> datetime:
    discard = timedelta(
        minutes=dt.minute % minutes,
        seconds=dt.second,
        microseconds=dt.microsecond,
    )
    return (dt - discard + timedelta(minutes=minutes)).replace(second=0, microsecond=0)
