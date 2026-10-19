"""Registro delle fonti disponibili."""

from __future__ import annotations

from .base import Adapter
from .blablacar import BlaBlaCarAdapter
from .carsharing import CarsharingAdapter
from .demo import DemoAdapter
from .flixbus import FlixBusAdapter
from .transit import TransitAdapter

_INSTANCES: list[Adapter] = [
    DemoAdapter(),
    BlaBlaCarAdapter(),
    FlixBusAdapter(),
    CarsharingAdapter(),
    TransitAdapter(),
]

ADAPTERS: dict[str, Adapter] = {adapter.id: adapter for adapter in _INSTANCES}


def get_adapters(sources: list[str]) -> list[Adapter]:
    ordered = [ADAPTERS[source] for source in sources if source in ADAPTERS]
    return ordered or [ADAPTERS["demo"]]


def sources_meta() -> list[dict]:
    return [adapter.meta() for adapter in _INSTANCES]
