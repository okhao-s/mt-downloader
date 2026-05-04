from __future__ import annotations
from pathlib import Path
from typing import Optional
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError


def _normalize_session_path(session_path: str) -> str:
    path = Path(str(session_path or '/app/data/telegram/telegram.session')).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    return str(path)


def _build_client(api_id: str | int, api_hash: str, session_path: str) -> TelegramClient:
    return TelegramClient(_normalize_session_path(session_path), int(str(api_id).strip()), str(api_hash).strip())


async def telegram_probe_session(api_id: str | int, api_hash: str, session_path: str) -> dict:
    client = _build_client(api_id, api_hash, session_path)
    try:
        await client.connect()
        authorized = await client.is_user_authorized()
        me = await client.get_me() if authorized else None
        return {
            'ok': True,
            'authorized': bool(authorized),
            'session_path': _normalize_session_path(session_path),
            'user': {
                'id': getattr(me, 'id', None),
                'username': getattr(me, 'username', None),
                'phone': getattr(me, 'phone', None),
                'first_name': getattr(me, 'first_name', None),
                'last_name': getattr(me, 'last_name', None),
            } if me else None,
        }
    finally:
        await client.disconnect()


async def telegram_send_code(api_id: str | int, api_hash: str, session_path: str, phone: str) -> dict:
    client = _build_client(api_id, api_hash, session_path)
    try:
        await client.connect()
        sent = await client.send_code_request(str(phone).strip())
        return {
            'ok': True,
            'phone_code_hash': getattr(sent, 'phone_code_hash', None),
            'session_path': _normalize_session_path(session_path),
        }
    finally:
        await client.disconnect()


async def telegram_sign_in(api_id: str | int, api_hash: str, session_path: str, phone: str, code: str, phone_code_hash: Optional[str] = None, password: Optional[str] = None) -> dict:
    client = _build_client(api_id, api_hash, session_path)
    try:
        await client.connect()
        try:
            if phone_code_hash:
                await client.sign_in(phone=str(phone).strip(), code=str(code).strip(), phone_code_hash=str(phone_code_hash).strip())
            else:
                await client.sign_in(phone=str(phone).strip(), code=str(code).strip())
        except SessionPasswordNeededError:
            if not password:
                return {'ok': False, 'need_password': True, 'session_path': _normalize_session_path(session_path)}
            await client.sign_in(password=str(password))
        me = await client.get_me()
        return {
            'ok': True,
            'authorized': True,
            'session_path': _normalize_session_path(session_path),
            'user': {
                'id': getattr(me, 'id', None),
                'username': getattr(me, 'username', None),
                'phone': getattr(me, 'phone', None),
                'first_name': getattr(me, 'first_name', None),
                'last_name': getattr(me, 'last_name', None),
            },
        }
    finally:
        await client.disconnect()
