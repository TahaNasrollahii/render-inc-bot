# the corridor — Telegram bot on Render

Your anonymous-message Telegram bot, running on Render with long-polling.

## Project layout

```
main.py             # bot entry point (polling + APScheduler)
server.py           # Flask app for Mini App API
bot/config.py       # reads env vars
bot/texts.py        # all the copy / quote pools
bot/storage.py      # Redis-backed state + FSM storage
bot/handlers.py     # every command/callback handler
bot/middleware.py    # activity tracking -> notifies the keeper
bot/reminders.py    # vow + countdown reminder logic
Procfile            # Render process types
requirements.txt
```

## Setup

### 1. Create an Upstash Redis database (free)

1. Go to <https://upstash.com>, sign up, create a **Redis** database.
2. Copy the **Redis URL** (looks like `rediss://default:...@...upstash.io:6379`).

### 2. Deploy to Render

1. Push this folder to a Git repo (GitHub/GitLab/Bitbucket) and **Import** it in Render.
2. Create **two services**:

   **Service 1: Worker (Bot)**
   - Type: Background Worker
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `python main.py`

   **Service 2: Web (Mini App API)**
   - Type: Web Service
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `python server.py`

3. Create a **Static Site** for the Mini App:
   - Build Command: `npm install && npm run build`
   - Publish Directory: `dist`

4. In each service's **Environment** tab, add:

   | Name             | Value                                            |
   |------------------|--------------------------------------------------|
   | `TOKEN`          | your bot token from @BotFather                   |
   | `ADMIN_ID`       | your numeric Telegram id (from @userinfobot)     |
   | `REDIS_URL`      | the Upstash Redis URL                             |
   | `WEBAPP_URL`     | your static site URL (for the keyboard button)   |

### 3. Set the menu button (optional)

Run locally once after the Mini App is deployed:

```powershell
$env:TOKEN="123:abc"
$env:WEBAPP_URL="https://your-static.onrender.app"
python set_menu_button.py
```

### 4. Verify

- Open the bot in Telegram and send `/start`
- The bot should respond (polling is working)
- The Mini App should open when tapping the "open the corridor" button

## Notes

- The bot polls Telegram constantly, so it stays alive on Render's free tier
- The Mini App API may have a ~30s cold start after 15 min of inactivity
- APScheduler runs daily at 9 AM to sweep vows and countdowns
- After deploying, the bot connects to Telegram automatically (no webhook setup needed)
