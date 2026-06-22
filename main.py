"""Bot entry point — long-running polling process with APScheduler.

The bot runs as a persistent process on Render, polling Telegram for
updates and running scheduled reminder sweeps via APScheduler.
"""

import asyncio

from aiogram import Bot, Dispatcher
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from bot.config import TOKEN
from bot.handlers import router
from bot.middleware import ActivityMiddleware
from bot.reminders import run_all_reminders
from bot.storage import Store, make_fsm_storage, make_redis


async def main() -> None:
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
