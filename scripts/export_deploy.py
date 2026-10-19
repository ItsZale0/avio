"""Esporta i dati locali per il deploy su Northflank (o altro container).

Crea un archivio con:
- `carpool.db`: il database con account, ricerche e impostazioni (i segreti
  compresi: stanno gia' nel DB, che non lascia mai il tuo PC senza backup);
- `gtfs.db`: gli orari GTFS gia' importati (opzionale, sono ~100 MB: si
  ricostruiscono con `python -m app.cli gtfs-update` se non lo passi).

Uso:
    python scripts/export_deploy.py                       # solo carpool.db
    python scripts/export_deploy.py --with-gtfs           # anche gli orari
    python scripts/export_deploy.py --out data/deploy     # altra cartella
"""

from __future__ import annotations

import argparse
import shutil
import sys
import zipfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from app import db  # noqa: E402  (import dopo il sys.path)


def export(out_dir: Path, with_gtfs: bool) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)

    # Copia coerente del database: `backup` e' sicuro con il WAL attivo.
    source = db.DB_PATH
    if not source.exists():
        print(f"Database non trovato: {source}", file=sys.stderr)
        print("Avvia il server almeno una volta, poi riprova.", file=sys.stderr)
        raise SystemExit(1)

    target = out_dir / "carpool.db"
    with db.db() as conn:
        conn.execute("VACUUM INTO ?", (str(target),))

    archive = out_dir / "carpool-deploy.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(target, "carpool.db")

    gtfs_source = BASE / "data" / "gtfs.db"
    if with_gtfs and gtfs_source.exists():
        gtfs_target = out_dir / "gtfs.db"
        shutil.copy2(gtfs_source, gtfs_target)
        with zipfile.ZipFile(archive, "a", zipfile.ZIP_DEFLATED) as zf:
            zf.write(gtfs_target, "gtfs.db")
        gtfs_target.unlink()
        print(f"  + gtfs.db ({gtfs_source.stat().st_size // (1024 * 1024)} MB)")

    target.unlink()
    print(f"\nArchivio pronto: {archive}")
    print(f"  carpool.db: {(archive.stat().st_size) // 1024} KB compressi")
    print("\nSul container:")
    print("  1. monta il volume persistente su /app/data")
    print("  2. scompatta l'archivio in /app/data")
    print("  3. riavvia il servizio")
    return archive


def main() -> int:
    parser = argparse.ArgumentParser(description="Esporta i dati per il deploy")
    parser.add_argument("--out", default=str(BASE / "data" / "deploy"), help="cartella di output")
    parser.add_argument("--with-gtfs", action="store_true", help="include anche gli orari GTFS")
    args = parser.parse_args()
    export(Path(args.out), args.with_gtfs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
