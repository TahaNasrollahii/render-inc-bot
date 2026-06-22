"""Mini App JSON API — Flask web service.

The client POSTs JSON {"action": "...", ...} and we dispatch on "action"
to a handler.

Auth: the client sends Telegram's signed initData in the
X-Telegram-Init-Data header. We HMAC-validate it and pass the verified
user dict to the handler.
"""

import asyncio
import base64
import binascii
import os
import random
import sys
from datetime import datetime, timedelta, timezone
from threading import Lock

from flask import Flask, Response, jsonify, request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from aiogram import Bot
from aiogram.types import BufferedInputFile

from bot.config import ADMIN_ID, TOKEN
from bot.handlers import (
    get_now_info,
    jalali_str,
    parse_persian_countdown,
    vow_days_left,
)
from bot.storage import Store, make_redis
from bot.timeutil import tehran_stamp
from bot.texts import (
    CONFIRM_MESSAGES,
    DARK_QUOTES,
    FORTUNES,
    KEEPER_REPLY_INTRO,
    KEEPER_REPLY_OUTRO,
    KEEPER_REPLY_TEXT,
    MESSAGE_TYPES,
    MIRROR_RESPONSES,
    MOOD_RESPONSES,
    NIGHT_CONFIRM_MESSAGES,
    RITUAL_QUESTIONS,
    escape_md_v2,
)
from bot.webapp_auth import InitDataError, validate_init_data

app = Flask(__name__)

_loop = asyncio.new_event_loop()
asyncio.set_event_loop(_loop)
_lock = Lock()

_redis = make_redis()
_store = Store(_redis)
_bot = Bot(token=TOKEN)


class Forbidden(Exception):
    pass


def _require_admin(user: dict) -> None:
    if user["id"] != ADMIN_ID:
        raise Forbidden("the keeper's sight is not yours to take")


def _full_name(first, last):
    return " ".join(p for p in (first, last) if p) or None


async def _fetch_identity(uid: int) -> dict:
    try:
        chat = await _bot.get_chat(uid)
        ident = {"name": _full_name(chat.first_name, chat.last_name), "username": chat.username}
    except Exception:
        ident = {"name": None, "username": None}
    await _store.set_identity(uid, ident["name"], ident["username"])
    return ident


async def _me(user: dict, payload: dict) -> dict:
    uid = user["id"]
    return {
        "user": {
            "id": uid,
            "username": user.get("username"),
            "first_name": user.get("first_name"),
        },
        "is_admin": uid == ADMIN_ID,
        "unread": await _store.get_unread(uid),
    }


async def _dark(user: dict, payload: dict) -> dict:
    return {"quote": random.choice(DARK_QUOTES)}


async def _fortune(user: dict, payload: dict) -> dict:
    return {"fortune": random.choice(FORTUNES)}


async def _mood(user: dict, payload: dict) -> dict:
    key = (payload.get("mood") or "").strip().lower()
    response = MOOD_RESPONSES.get(key)
    if not response:
        raise ValueError("unknown mood")
    return {"response": response}


async def _mirror(user: dict, payload: dict) -> dict:
    word = (payload.get("word") or "").strip().lower()
    matched = None
    for key, value in MIRROR_RESPONSES.items():
        if key in word:
            matched = value
            break
    if not matched:
        matched = random.choice(list(MIRROR_RESPONSES.values()))
    return {"response": matched}


MAX_MEDIA_BYTES = 3_000_000

_MEDIA_SENDERS = {
    "photo": ("send_photo", "photo", "photo.jpg"),
    "video": ("send_video", "video", "video.mp4"),
    "voice": ("send_voice", "voice", "voice.ogg"),
}


def _decode_media(media: dict) -> tuple:
    kind = (media.get("kind") or "").lower()
    if kind not in _MEDIA_SENDERS:
        raise ValueError("unsupported media kind")

    data = media.get("data") or ""
    if "," in data and data.lstrip().startswith("data:"):
        data = data.split(",", 1)[1]
    try:
        raw = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("media is not valid base64")

    if not raw:
        raise ValueError("empty media")
    if len(raw) > MAX_MEDIA_BYTES:
        raise ValueError("media too large")

    filename = (media.get("filename") or _MEDIA_SENDERS[kind][2])[:80]
    return kind, raw, filename


async def _deliver_media(chat_id: int, kind: str, raw: bytes, filename: str, caption: str) -> None:
    method_name, kwarg, _ = _MEDIA_SENDERS[kind]
    file = BufferedInputFile(raw, filename=filename)
    try:
        await getattr(_bot, method_name)(chat_id, **{kwarg: file}, caption=caption)
    except Exception:
        await _bot.send_document(
            chat_id,
            document=BufferedInputFile(raw, filename=filename),
            caption=caption,
        )


async def _send(user: dict, payload: dict) -> dict:
    uid = user["id"]
    text = (payload.get("text") or "").strip()[:4000]

    media = payload.get("media")
    decoded = _decode_media(media) if media else None

    if not text and not decoded:
        raise ValueError("nothing to send")

    msg_type = payload.get("type") or "just_words"
    label = MESSAGE_TYPES.get(msg_type, MESSAGE_TYPES["just_words"])

    full_time, date_str, is_night = get_now_info()
    confirm = random.choice(NIGHT_CONFIRM_MESSAGES if is_night else CONFIRM_MESSAGES)

    if await _store.is_blocked(uid):
        return {"confirm": confirm}

    username = user.get("username") or "no_username"
    alias = await _store.get_alias(uid)
    alias_line = f"Alias: {alias}\n" if alias else ""

    counter = await _store.incr_counter()
    await _store.add_sender(uid)
    await _store.incr_day(date_str)
    await _store.incr_user_messages(uid)
    await _store.set_identity(
        uid, _full_name(user.get("first_name"), user.get("last_name")), user.get("username")
    )

    attach_line = f"carries a {decoded[0]}\n" if decoded else ""
    try:
        await _bot.send_message(
            ADMIN_ID,
            f"{label}  #{counter}\n\n"
            f"Sender: {uid} (@{username})\n"
            f"{alias_line}"
            f"Carried: {text or '-'}\n"
            f"{attach_line}"
            f"{full_time}\n"
            f"via the corridor (mini app)\n\n"
            f"To answer:\n/reply {uid} your message",
        )
        if decoded:
            kind, raw, filename = decoded
            await _deliver_media(
                ADMIN_ID, kind, raw, filename,
                caption=f"{label} #{counter}\nSender: {uid} (@{username})",
            )
    except Exception as exc:
        print(f"app send: keeper delivery failed: {exc}", file=sys.stderr)

    await _store.add_thread_message(uid, {
        "dir": "out",
        "text": text,
        "kind": label,
        "media": decoded[0] if decoded else None,
        "ts": datetime.now(timezone.utc).timestamp(),
    })

    return {"confirm": confirm}


async def _inbox(user: dict, payload: dict) -> dict:
    uid = user["id"]
    messages = await _store.get_thread(uid)
    await _store.clear_unread(uid)
    return {"messages": messages}


async def _ritual_questions(user: dict, payload: dict) -> dict:
    return {"questions": list(RITUAL_QUESTIONS)}


async def _ritual(user: dict, payload: dict) -> dict:
    answers = payload.get("answers")
    if not isinstance(answers, list) or len(answers) != 4:
        raise ValueError("the ritual needs four answers")
    answers = [(str(a).strip() or "[no answer]")[:2000] for a in answers]

    uid = user["id"]
    username = user.get("username") or "no_username"
    alias = await _store.get_alias(uid)
    alias_line = f"Alias: {alias}\n" if alias else ""
    full_time, _, _ = get_now_info()

    record = (
        "RITUAL COMPLETED\n\n"
        f"{uid} (@{username})\n"
        f"{alias_line}"
        f"{full_time}\n"
        f"via the corridor (mini app)\n\n"
        f"I. {RITUAL_QUESTIONS[0]}\n-> {answers[0]}\n\n"
        f"II. {RITUAL_QUESTIONS[1]}\n-> {answers[1]}\n\n"
        f"III. {RITUAL_QUESTIONS[2]}\n-> {answers[2]}\n\n"
        f"IV. {RITUAL_QUESTIONS[3]}\n-> {answers[3]}"
    )

    await _store.incr_user_rituals(uid)
    try:
        await _bot.send_message(ADMIN_ID, record)
    except Exception as exc:
        print(f"app ritual: keeper notify failed: {exc}", file=sys.stderr)
    return {}


async def _letter(user: dict, payload: dict) -> dict:
    text = (payload.get("text") or "").strip()
    if not text:
        raise ValueError("empty letter")
    text = text[:4000]

    uid = user["id"]
    username = user.get("username") or "no_username"
    alias = await _store.get_alias(uid)
    alias_line = f"Alias: {alias}\n" if alias else ""
    full_time, _, _ = get_now_info()

    await _store.incr_user_letters(uid)
    try:
        await _bot.send_message(
            ADMIN_ID,
            f"UNSENT LETTER\n\n"
            f"{uid} (@{username})\n"
            f"{alias_line}"
            f"{full_time}\n"
            f"via the corridor (mini app)\n\n"
            f"{text}",
        )
    except Exception as exc:
        print(f"app letter: keeper notify failed: {exc}", file=sys.stderr)
    return {}


async def _vow_get(user: dict, payload: dict) -> dict:
    vow = await _store.get_vow(user["id"])
    if not vow:
        return {"vow": None}
    return {"vow": {"text": vow["text"], "days_left": vow_days_left(vow)}}


async def _vow_set(user: dict, payload: dict) -> dict:
    text = (payload.get("text") or "").strip()
    if not text:
        raise ValueError("empty vow")
    text = text[:2000]

    try:
        days = int(payload.get("days"))
    except (TypeError, ValueError):
        raise ValueError("days must be a whole number")
    if not 1 <= days <= 365:
        raise ValueError("days must be between 1 and 365")

    now = datetime.now(timezone.utc)
    vow = {
        "text": text,
        "created_at": now.isoformat(),
        "remind_at": (now + timedelta(days=days)).timestamp(),
        "reminded": False,
    }
    await _store.set_vow(user["id"], vow)
    return {"vow": {"text": text, "days_left": days}}


async def _countdown(user: dict, payload: dict) -> dict:
    raw = (payload.get("date") or "").strip()
    now = datetime.now(timezone.utc)
    if not raw:
        return {"today": jalali_str(now)}

    try:
        target, label = parse_persian_countdown(raw)
    except ValueError:
        return {"error": "unreadable"}

    target_jalali = jalali_str(target)
    if target < now:
        return {"label": label, "target_jalali": target_jalali, "passed": True}

    cid = await _store.next_countdown_id()
    await _store.save_countdown(
        user["id"],
        cid,
        {
            "label": label,
            "target": target.timestamp(),
            "target_jalali": target_jalali,
            "created_at": now.isoformat(),
            "notified": False,
        },
    )

    delta = target - now
    hours, remainder = divmod(delta.seconds, 3600)
    return {
        "id": cid,
        "label": label,
        "target_jalali": target_jalali,
        "passed": False,
        "days": delta.days,
        "hours": hours,
        "minutes": remainder // 60,
    }


async def _alias_get(user: dict, payload: dict) -> dict:
    return {"alias": await _store.get_alias(user["id"])}


async def _alias_set(user: dict, payload: dict) -> dict:
    alias = (payload.get("alias") or "").strip()[:32]
    if not alias:
        raise ValueError("empty alias")
    await _store.set_alias(user["id"], alias)
    return {"alias": alias}


async def _archive(user: dict, payload: dict) -> dict:
    uid = user["id"]
    stats = await _store.get_user_stats(uid)
    alias = await _store.get_alias(uid)
    vow = await _store.get_vow(uid)
    vow_out = {"text": vow["text"], "days_left": vow_days_left(vow)} if vow else None

    now_ts = datetime.now(timezone.utc).timestamp()
    countdowns_out = [
        {
            "id": cid,
            "label": c.get("label", "the unnamed moment"),
            "target_jalali": c.get("target_jalali", ""),
            "days_left": max(0, int((c.get("target", 0) - now_ts) // 86400)),
        }
        for cid, c in await _store.user_countdowns(uid)
    ]
    return {"alias": alias, "stats": stats, "vow": vow_out, "countdowns": countdowns_out}


async def _unread(user: dict, payload: dict) -> dict:
    return {"unread": await _store.get_unread(user["id"])}


async def _activity(user: dict, payload: dict) -> dict:
    uid = user["id"]
    if uid == ADMIN_ID:
        return {}

    label = (payload.get("label") or "stirred").strip()[:120]
    username = user.get("username")
    name = f"@{username}" if username else (user.get("first_name") or "a nameless soul")
    alias = await _store.get_alias(uid)
    alias_line = f"{alias}\n" if alias else ""
    when = tehran_stamp()

    notice = (
        "a soul stirs in the corridor - app\n"
        "-------------------------\n"
        f"{name}\n"
        f"{uid}\n"
        f"{alias_line}"
        f"{label}\n"
        f"{when}"
    )
    try:
        await _bot.send_message(ADMIN_ID, notice)
    except Exception as exc:
        print(f"app activity: notify failed: {exc}", file=sys.stderr)
    return {}


async def _admin_threads(user: dict, payload: dict) -> dict:
    _require_admin(user)

    identities = await _store.all_identities()

    threads = []
    for uid in await _store.all_thread_uids():
        tail = await _store.thread_tail(uid)
        if not tail:
            continue
        ident = identities.get(uid) or await _fetch_identity(uid)
        threads.append({
            "uid": uid,
            "name": ident.get("name"),
            "username": ident.get("username"),
            "alias": await _store.get_alias(uid),
            "last_text": tail.get("text") or "",
            "last_kind": tail.get("kind"),
            "last_media": tail.get("media"),
            "last_dir": tail.get("dir"),
            "ts": tail.get("ts") or 0,
            "new": tail.get("dir") == "out",
        })

    threads.sort(key=lambda t: t["ts"], reverse=True)
    return {"threads": threads}


async def _admin_thread(user: dict, payload: dict) -> dict:
    _require_admin(user)
    try:
        uid = int(payload.get("uid"))
    except (TypeError, ValueError):
        raise ValueError("which soul?")
    return {
        "uid": uid,
        "alias": await _store.get_alias(uid),
        "messages": await _store.get_thread(uid),
    }


async def _admin_reply(user: dict, payload: dict) -> dict:
    _require_admin(user)
    try:
        uid = int(payload.get("uid"))
    except (TypeError, ValueError):
        raise ValueError("which soul?")

    text = (payload.get("text") or "").strip()[:4000]
    media = payload.get("media")
    decoded = _decode_media(media) if media else None

    if not text and not decoded:
        raise ValueError("nothing to send")

    if decoded:
        kind, raw, filename = decoded
        await _bot.send_message(uid, KEEPER_REPLY_INTRO, parse_mode="Markdown")
        await _deliver_media(uid, kind, raw, filename, caption=text or None)
        await _bot.send_message(uid, KEEPER_REPLY_OUTRO, parse_mode="MarkdownV2")
    else:
        await _bot.send_message(
            uid,
            KEEPER_REPLY_TEXT.format(reply=escape_md_v2(text)),
            parse_mode="MarkdownV2",
        )

    await _store.add_thread_message(uid, {
        "dir": "in",
        "text": text,
        "kind": "reply",
        "media": decoded[0] if decoded else None,
        "ts": datetime.now(timezone.utc).timestamp(),
    })
    await _store.incr_unread(uid)
    return {}


ACTIONS = {
    "me": _me,
    "dark": _dark,
    "fortune": _fortune,
    "mood": _mood,
    "mirror": _mirror,
    "send": _send,
    "inbox": _inbox,
    "ritual_questions": _ritual_questions,
    "ritual": _ritual,
    "letter": _letter,
    "vow_get": _vow_get,
    "vow_set": _vow_set,
    "countdown": _countdown,
    "alias_get": _alias_get,
    "alias_set": _alias_set,
    "archive": _archive,
    "unread": _unread,
    "activity": _activity,
    "admin_threads": _admin_threads,
    "admin_thread": _admin_thread,
    "admin_reply": _admin_reply,
}


async def _dispatch(action: str, user: dict, payload: dict) -> dict:
    handler = ACTIONS.get(action)
    if handler is None:
        raise ValueError(f"unknown action: {action}")
    return await handler(user, payload)


@app.route("/ping", methods=["GET", "HEAD"])
def ping():
    return Response("the corridor is open.", mimetype="text/plain")


@app.route("/api", methods=["GET"])
def api_info():
    return jsonify({"ok": True, "corridor": "open"})


@app.route("/api", methods=["POST"])
def api_dispatch():
    body = request.get_json(silent=True) or {}

    init_data = request.headers.get("X-Telegram-Init-Data", "")
    try:
        user = validate_init_data(init_data)
    except InitDataError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 401

    action = body.pop("action", None)
    if not action:
        return jsonify({"ok": False, "error": "missing action"}), 400

    try:
        with _lock:
            result = _loop.run_until_complete(_dispatch(action, user, body))
        return jsonify({"ok": True, **result})
    except Forbidden as exc:
        return jsonify({"ok": False, "error": str(exc)}), 403
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        print(f"app error ({action}): {exc}", file=sys.stderr)
        return jsonify({"ok": False, "error": "the dark swallowed something"}), 500


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
