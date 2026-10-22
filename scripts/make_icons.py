"""Disegna le icone di AVIO (favicon e PWA) senza dipendenze esterne.

Il marchio e' lo stesso della rail: due punti collegati da una curva, cioe' un
viaggio. Niente auto: AVIO copre passaggi, bus, treni e voli.

    python scripts/make_icons.py

Scrive `static/icons/icon-192.png`, `icon-512.png` e `icon-maskable-512.png`.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

RADICE = Path(__file__).resolve().parent.parent
USCITA = RADICE / "static" / "icons"

# Grafite del design system e mint dell'accento.
FONDO = bytes((20, 20, 23, 255))
MENTA = bytes((110, 231, 183, 255))
TRASPARENTE = bytes((0, 0, 0, 0))

# Geometria del marchio nello spazio 24 x 24 (lo stesso della rail).
PUNTO_A = (6.4, 17.6, 2.0)
PUNTO_B = (17.6, 6.4, 2.0)
CURVA = ((7.9, 16.1), (11.4, 14.6), (12.6, 9.4), (16.1, 7.9))
SPESSORE = 0.85  # raggio del tratto della curva


def _arrotondato(x: float, y: float, lato: float, raggio: float) -> bool:
    cx = min(max(x, raggio), lato - raggio)
    cy = min(max(y, raggio), lato - raggio)
    return (x - cx) ** 2 + (y - cy) ** 2 <= raggio**2


def _disco(pixel: bytearray, lato: int, cx: float, cy: float, raggio: float) -> None:
    x0 = max(0, int(cx - raggio) - 1)
    x1 = min(lato - 1, int(cx + raggio) + 1)
    y0 = max(0, int(cy - raggio) - 1)
    y1 = min(lato - 1, int(cy + raggio) + 1)
    quadrato = raggio * raggio
    for y in range(y0, y1 + 1):
        dy = y + 0.5 - cy
        base = y * lato
        for x in range(x0, x1 + 1):
            dx = x + 0.5 - cx
            if dx * dx + dy * dy <= quadrato:
                i = (base + x) * 4
                pixel[i : i + 4] = MENTA


def _bezier(punti: tuple, passi: int) -> list[tuple[float, float]]:
    (x0, y0), (x1, y1), (x2, y2), (x3, y3) = punti
    risultato = []
    for indice in range(passi + 1):
        t = indice / passi
        u = 1 - t
        x = u**3 * x0 + 3 * u**2 * t * x1 + 3 * u * t**2 * x2 + t**3 * x3
        y = u**3 * y0 + 3 * u**2 * t * y1 + 3 * u * t**2 * y2 + t**3 * y3
        risultato.append((x, y))
    return risultato


def _marchio(pixel: bytearray, lato: int, scala: float, centro: float, fattore: float) -> None:
    def punto(x: float, y: float) -> tuple[float, float]:
        return (
            centro + (x * scala - centro) * fattore,
            centro + (y * scala - centro) * fattore,
        )

    for cx, cy, raggio in (PUNTO_A, PUNTO_B):
        x, y = punto(cx, cy)
        _disco(pixel, lato, x, y, raggio * scala * fattore)
    for x, y in _bezier(CURVA, 320):
        px, py = punto(x, y)
        _disco(pixel, lato, px, py, SPESSORE * scala * fattore)


def disegna(lato: int, maskable: bool = False) -> bytearray:
    """Icona RGBA alla dimensione richiesta, con 4x di antialiasing."""
    fattore = 4
    grande = lato * fattore
    scala = grande / 24
    pixel = bytearray(grande * grande * 4)
    raggio = 0 if maskable else grande * 0.22

    for y in range(grande):
        for x in range(grande):
            if maskable or _arrotondato(x + 0.5, y + 0.5, grande, raggio):
                i = (y * grande + x) * 4
                pixel[i : i + 4] = FONDO

    _marchio(pixel, grande, scala, grande / 2, 0.62 if maskable else 1.0)

    # Riduzione 4x4 per l'antialiasing.
    piccolo = bytearray(lato * lato * 4)
    for y in range(lato):
        for x in range(lato):
            r = g = b = a = 0
            for dy in range(fattore):
                base = ((y * fattore + dy) * grande + x * fattore) * 4
                for dx in range(fattore):
                    i = base + dx * 4
                    r += pixel[i]
                    g += pixel[i + 1]
                    b += pixel[i + 2]
                    a += pixel[i + 3]
            i = (y * lato + x) * 4
            piccolo[i : i + 4] = bytes(
                (r // 16, g // 16, b // 16, a // 16)
            )
    return piccolo


def scrivi_png(percorso: Path, lato: int, pixel: bytearray) -> None:
    def blocco(tipo: bytes, dati: bytes) -> bytes:
        return (
            struct.pack(">I", len(dati))
            + tipo
            + dati
            + struct.pack(">I", zlib.crc32(tipo + dati) & 0xFFFFFFFF)
        )

    righe = bytearray()
    passo = lato * 4
    for y in range(lato):
        righe.append(0)
        righe.extend(pixel[y * passo : (y + 1) * passo])
    intestazione = struct.pack(">IIBBBBB", lato, lato, 8, 6, 0, 0, 0)
    percorso.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + blocco(b"IHDR", intestazione)
        + blocco(b"IDAT", zlib.compress(bytes(righe), 9))
        + blocco(b"IEND", b"")
    )


def main() -> int:
    USCITA.mkdir(parents=True, exist_ok=True)
    for nome, lato, maskable in (
        ("icon-192.png", 192, False),
        ("icon-512.png", 512, False),
        ("icon-maskable-512.png", 512, True),
    ):
        scrivi_png(USCITA / nome, lato, disegna(lato, maskable))
        print("scritta", USCITA / nome)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
