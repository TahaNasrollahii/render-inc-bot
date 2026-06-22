import os

# ================== CONFIG ==================
# Telegram bot token from @BotFather
TOKEN = os.getenv("TOKEN", "")

# Your Telegram numeric user id (the "keeper")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

# Redis connection url (Upstash gives you a rediss://... url)
REDIS_URL = os.getenv("REDIS_URL", "")

# Public URL of the deployed Mini App (e.g. https://your-static.onrender.app).
# Used to add an "open the corridor" button to the reply keyboard. Leave blank
# and that button is simply omitted.
WEBAPP_URL = os.getenv("WEBAPP_URL", "")
