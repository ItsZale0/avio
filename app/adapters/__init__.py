"""Registro delle fonti disponibili."""

from __future__ import annotations

from .base import Adapter
from .blablacar import BlaBlaCarAdapter
from .carsharing import CarsharingAdapter
from .flixbus import FlixBusAdapter
from .itabus import ItabusAdapter
from .omio import OmioAdapter
from .transit import TransitAdapter
from .trenitalia import TrenitaliaAdapter

_INSTANCES: list[Adapter] = [
    BlaBlaCarAdapter(),
    FlixBusAdapter(),
    ItabusAdapter(),
    TrenitaliaAdapter(),
    OmioAdapter(),
    CarsharingAdapter(),
    TransitAdapter(),
]

ADAPTERS: dict[str, Adapter] = {adapter.id: adapter for adapter in _INSTANCES}


def get_adapters(sources: list[str]) -> list[Adapter]:
    ordered = [ADAPTERS[source] for source in sources if source in ADAPTERS]
    return ordered or list(_INSTANCES)


def sources_meta() -> list[dict]:
    return [adapter.meta() for adapter in _INSTANCES]
