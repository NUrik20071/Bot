# Skin Drop Bot

Python Telegram bot starter using aiogram, SQLite and FastAPI. This is a demo: cosmetics are virtual items inside the bot, not transferable Telegram gifts or game inventory.

- `/start`, `/daily`, free drops, inventory, upgrades
- Telegram Stars purchase for specific, disclosed cosmetic items
- Payment idempotency using Telegram charge ID
- Token-protected minimal admin API at `/admin/stats`, `/admin/users`, `/admin/payments`

Random drops and upgrades use only earned free coins; Stars do not buy random outcomes. Set `BOT_TOKEN` and a long random `ADMIN_TOKEN` as environment variables. Set `DATABASE_PATH` to persistent storage for durable data. The app listens on Railway's `PORT` automatically.

This is a starter, not a fully audited production service. Test payment flows before real launch.
