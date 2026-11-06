"""Login iniziale per l'account Telegram utente (MTProto).

Uso interattivo:
    cd /opt/carpool
    .venv/bin/python -m app.telegram_login

Chiede il numero, invia il codice, lo legge da tastiera, gestisce la 2FA
e salva la sessione. Va eseguito UNA sola volta.

Opzioni:
    --phone NUMERO   numero (altrimenti lo chiede)
    --join LINK      entra in un gruppo tramite link di invito
    --list           elenca i tuoi gruppi/canali
"""
from __future__ import annotations

import argparse
import asyncio
import getpass

from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

from .services import telegram_user


async def interactive_login(phone_arg: str | None) -> None:
    if not telegram_user.API_ID or not telegram_user.API_HASH:
        print("TELEGRAM_API_ID / TELEGRAM_API_HASH non configurati in .env")
        return

    phone = phone_arg or input("Numero di telefono (es. +39351...): ").strip()

    client = telegram_user._client()
    await client.connect()
    if await client.is_user_authorized():
        print("Sessione gia' valida.")
        await client.disconnect()
        return

    result = await client.send_code_request(phone)
    print("Codice inviato. Controlla Telegram/SMS.")
    code = input("Inserisci il codice: ").strip()

    try:
        await client.sign_in(phone, code, phone_code_hash=result.phone_code_hash)
    except SessionPasswordNeededError:
        pwd = getpass.getpass("Password 2FA: ")
        await client.sign_in(password=pwd)

    await client.disconnect()
    print("Login completato. Sessione salvata in", telegram_user.SESSION_PATH)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phone", help="numero di telefono internazionale")
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

    await interactive_login(args.phone)


if __name__ == "__main__":
    asyncio.run(main())
PY