"""Login iniziale per l'account Telegram utente (MTProto).

Uso:
    cd /opt/carpool
    .venv/bin/python -m app.telegram_login --phone +39351...

Il login va fatto UNA sola volta: la sessione viene salvata in
TELEGRAM_USER_SESSION (default /var/lib/carpool/telegram_user.session).

Opzioni:
    --phone NUMERO       numero di telefono (obbligatorio al primo login)
    --code CODICE        codice ricevuto (se lo passi, completa il login)
    --password PWD       password 2FA (se attiva)
    --join LINK          entra in un gruppo tramite link di invito
    --list               elenca i tuoi gruppi/canali
"""
from __future__ import annotations

import argparse
import asyncio
import getpass

from .services import telegram_user


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phone", help="numero di telefono internazionale")
    parser.add_argument("--code", help="codice di verifica ricevuto")
    parser.add_argument("--password", help="password 2FA (se attiva)")
    parser.add_argument("--join", help="link di invito a un gruppo")
    parser.add_argument("--list", action="store_true", help="elenca gruppi/canali")
    args = parser.parse_args()

    if args.join:
        info = await telegram_user.join_invite(args.join)
        print("Entrato nel gruppo:", info)
        return

    if args.list:
        groups = await telegram_user.list_groups()
        for g in groups:
            print(f"{g['id']}\t{g['title']}\t{g.get('username') or ''}")
        return

    if not args.phone:
        print("Serve --phone per il login.")
        return

    if args.code is None:
        status = await telegram_user.login_send_code(args.phone)
        print("Stato:", status)
        if status == "code_sent":
            print("Codice inviato. Riesegui con --code CODICE [--password PWD].")
        return

    password = args.password
    if password is None:
        # Chiedi solo se necessario: prima prova senza.
        pass
    status = await telegram_user.login_verify(args.phone, args.code, password)
    print("Stato:", status)
    if status == "password_needed":
        pwd = getpass.getpass("Password 2FA: ")
        status = await telegram_user.login_verify(args.phone, args.code, pwd)
        print("Stato:", status)


if __name__ == "__main__":
    asyncio.run(main())
