"""Crea la cartella statica da pubblicare su Cloudflare Pages.

Il frontend e' identico a quello servito in locale, ma `config.json` punta
all'API pubblica (il tunnel Cloudflare verso il tuo PC).

Uso:
    python scripts/build_pages.py --api https://carpool.miodominio.it --out dist-pages
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
WEB = BASE / "static"

SKIP = {"__pycache__", ".DS_Store"}


def build(api_base: str, out: Path) -> Path:
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(WEB, out, ignore=shutil.ignore_patterns(*SKIP))

    (out / "config.json").write_text(
        json.dumps({"apiBase": api_base.strip(), "app": "Carpool Hunter"}, ensure_ascii=False),
        encoding="utf-8",
    )
    (out / "_headers").write_text(
        "\n".join(
            [
                "/config.json",
                "  Cache-Control: no-store",
                "/sw.js",
                "  Cache-Control: no-cache",
                "/*",
                "  X-Content-Type-Options: nosniff",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepara il bundle per Cloudflare Pages")
    parser.add_argument("--api", required=True, help="URL pubblico dell'API (tunnel)")
    parser.add_argument("--out", default=str(BASE / "dist-pages"), help="cartella di output")
    args = parser.parse_args()
    target = build(args.api, Path(args.out))
    print(f"Pronto: {target}")
    print("Deploy:  npx wrangler pages deploy dist-pages --project-name carpool-hunter")
    print("Oppure trascina la cartella nella dashboard Cloudflare Pages.")


if __name__ == "__main__":
    main()
