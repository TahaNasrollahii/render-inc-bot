"""Vow and countdown reminder logic.

Called by APScheduler inside the long-running bot process.
"""

import sys
from datetime import datetime, timezone

from aiogram import Bot

from bot.config import TOKEN
from bot.storage import Store, make_redis
from bot.texts import COUNTDOWN_REMINDER_TEXT, VOW_REMINDER_TEXT

_redis = make_redis()
_store = Store(_redis)
_bot = Bot(token=TOKEN)


async def run_reminders() -> int:
    """Deliver every due, un-reminded vow. Returns how many were sent."""
    now = datetime.now(timezone.utc).timestamp()
    sent = 0

    for key in await _store.all_vow_keys():
        try:
            uid = int(key.rsplit(":", 1)[1])
        except (ValueError, IndexError):
            continue

        vow = await _store.get_vow(uid)
        if not vow or vow.get("reminded"):
            continue
        if vow.get("remind_at", 0) > now:
            continue

        try:
            await _bot.send_message(uid, VOW_REMINDER_TEXT.format(text=vow["text"]))
        except Exception as exc:
            print(f"cron: could not remind {uid}: {exc}", file=sys.stderr)
            continue

        vow["reminded"] = True
        await _store.set_vow(uid, vow)
        sent += 1

    return sent


async def run_countdowns() -> int:
    """Deliver every countdown whose moment has arrived. Returns how many were sent."""
    now = datetime.now(timezone.utc).timestamp()
    sent = 0

    for key in await _store.all_countdown_keys():
        parts = key.split(":")
        if len(parts) != 4:
            continue
        try:
            uid, cid = int(parts[2]), int(parts[3])
        except ValueError:
            continue

        countdown = await _store.get_countdown(uid, cid)
        if not countdown or countdown.get("notified"):
            continue
        if countdown.get("target", 0) > now:
            continue

        try:
            await _bot.send_message(
                uid,
                COUNTDOWN_REMINDER_TEXT.format(
                    label=countdown.get("label", "the unnamed moment"),
                    date=countdown.get("target_jalali", ""),
                ),
            )
        except Exception as exc:
            print(f"cron: could not send countdown to {uid}: {exc}", file=sys.stderr)
            continue

        countdown["notified"] = True
        await _store.save_countdown(uid, cid, countdown)
        sent += 1

    return sent


async def run_all_reminders() -> None:
    """Sweep all vows and countdowns. Called by APScheduler."""
    try:
        vows = await run_reminders()
        countdowns = await run_countdowns()
        print(f"reminders: vows={vows}, countdowns={countdowns}")
    except Exception as exc:
        print(f"reminder sweep error: {exc}", file=sys.stderr)
