"""Pubblica il puntatore del backend: dove sta l'API, adesso.

Due cose da aggiornare quando l'API cambia (per esempio quando passi
dal tunnel sul PC alla VM su Oracle):

1. `backend.json` sul branch: e' il puntatore che il frontend legge subito,
   prima ancora che Pages venga ridistribuito;
2. la variabile `CARPOOL_API_BASE` del repo: e' quella che il deploy di
   Pages scrive in `config.json`.

Uso:
    python scripts\\publish_backend.py https://123.45.67.89
    python scripts\\publish_backend.py https://carpool.dominio.it --no-deploy
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "scripts"))

from tunnel import publish_backend_pointer, update_github_variable  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Pubblica il puntatore del backend")
    parser.add_argument("url", help="URL pubblico dell'API (https://...)")
    parser.add_argument(
        "--no-deploy",
        action="store_true",
        help="non rilanciare il deploy di GitHub Pages",
    )
    parser.add_argument("--repo", default="ItsZale0/avio")
    args = parser.parse_args()

    url = args.url.strip().rstrip("/")
    if not url.startswith("http"):
        print("L'URL deve cominciare con http:// o https://", file=sys.stderr)
        return 1

    print(f"Puntatore del backend: {url}")
    if not publish_backend_pointer(url, repo=args.repo):
        print("  Scrittura di backend.json fallita.", file=sys.stderr)
        return 1

    if args.no_deploy:
        print("  Variabile GitHub non toccata (--no-deploy).")
        return 0

    if not update_github_variable(url, repo=args.repo):
        print(
            "  Variabile GitHub non aggiornata: serve GITHUB_TOKEN con scope repo.\n"
            "  Fallo a mano: Settings -> Secrets and variables -> Actions ->\n"
            "  Variables -> CARPOOL_API_BASE, poi rilancia il deploy di Pages."
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
