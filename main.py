"""Bot entry point — long-running Web Service with polling + APScheduler.

Runs as a Web Service on Render (free tier). Includes a health check
endpoint so Render keeps the service alive.
"""

import asyncio
import os
import threading
from http.server import BaseHTTPRequestHandler

from aiogram import Bot, Dispatcher
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from bot.config import TOKEN
from bot.handlers import router
from bot.middleware import ActivityMiddleware
from bot.reminders import run_all_reminders
from bot.storage import Store, make_fsm_storage, make_redis


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"the corridor is open.")

    def log_message(self, format, *args):
        pass  # suppress request logs


def _start_http_server() -> None:
    port = int(os.getenv("PORT", "10000"))
    server = __import__("http.server").HTTPServer(("0.0.0.0", port), HealthHandler)
    server.serve_forever()


async def main() -> None:
    # Start health check server in a daemon thread
    http_thread = threading.Thread(target=_start_http_server, daemon=True)
    http_thread.start()

    redis = make_redis()
    store = Store(redis)
    bot = Bot(token=TOKEN)
    fsm = make_fsm_storage()
    dp = Dispatcher(storage=fsm)

    activity = ActivityMiddleware()
    dp.message.outer_middleware(activity)
    dp.callback_query.outer_middleware(activity)
    dp.include_router(router)

    # Schedule daily reminder sweep (vows + countdowns)
    scheduler = AsyncIOScheduler()
    scheduler.add_job(run_all_reminders, "cron", hour=9, minute=0)
    scheduler.start()

    print("the corridor is polling the dark...")
    await dp.start_polling(bot, store=store)


if __name__ == "__main__":
    asyncio.run(main())
