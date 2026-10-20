import os
import asyncio
import logging
import random
from datetime import datetime, timezone
from contextlib import asynccontextmanager

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, LabeledPrice, PreCheckoutQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from fastapi import FastAPI, HTTPException, Header
from fastapi.responses import HTMLResponse
import uvicorn

from db import Database

logging.basicConfig(level=logging.INFO)
TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")
DB = Database(os.getenv("DATABASE_PATH", "skin_drop.sqlite3"))
PORT = int(os.getenv("PORT", os.getenv("WEB_PORT", "8080")))

COSMETICS = {
    "neon_fox": {"name": "Neon Fox", "rarity": "Rare", "price": 35, "emoji": "🦊"},
    "void_blade": {"name": "Void Blade", "rarity": "Epic", "price": 75, "emoji": "🗡️"},
    "solar_crown": {"name": "Solar Crown", "rarity": "Legendary", "price": 150, "emoji": "👑"},
}
DROP_POOL = [
    ("pixel_cat", "Pixel Cat", "Common", "🐱", 55),
    ("neon_fox", "Neon Fox", "Rare", "🦊", 28),
    ("void_blade", "Void Blade", "Epic", "🗡️", 13),
    ("solar_crown", "Solar Crown", "Legendary", "👑", 4),
]

bot = Bot(TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML)) if TOKEN else None
dp = Dispatcher()

def menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎁 Бесплатный дроп", callback_data="drop")],
        [InlineKeyboardButton(text="🎒 Инвентарь", callback_data="inventory"), InlineKeyboardButton(text="⬆️ Улучшить", callback_data="upgrade")],
        [InlineKeyboardButton(text="⭐ Магазин", callback_data="shop"), InlineKeyboardButton(text="💰 Баланс", callback_data="balance")],
    ])

@dp.message(CommandStart())
async def start(m: Message):
    await DB.ensure_user(m.from_user.id, m.from_user.username or "")
    await m.answer(
        "🎮 <b>Skin Drop</b>\nСобирай виртуальные косметические предметы.\n\n"
        "• /daily — 3 бесплатных жетона в день.\n"
        "• Дропы и улучшения используют только бесплатные жетоны.\n"
        "• ⭐ покупает конкретный предмет, без случайного результата.\n\n"
        "Это внутренняя коллекция бота, не реальные Telegram-подарки и не предметы из сторонних игр.",
        reply_markup=menu()
    )

@dp.message(Command("help"))
async def help_cmd(m: Message):
    await m.answer("Команды: /start, /daily.")

@dp.message(Command("daily"))
async def daily(m: Message):
    await DB.ensure_user(m.from_user.id, m.from_user.username or "")
    if await DB.claim_daily(m.from_user.id):
        await m.answer("✅ Получено 3 бесплатных жетона.")
    else:
        await m.answer("⏳ Жетоны уже получены сегодня.")

@dp.callback_query(F.data == "balance")
async def balance(c: CallbackQuery):
    await DB.ensure_user(c.from_user.id, c.from_user.username or "")
    u = await DB.get_user(c.from_user.id)
    await c.message.answer(f"💰 Баланс: <b>{u['coins']}</b> бесплатных жетонов.", reply_markup=menu())
    await c.answer()

@dp.callback_query(F.data == "drop")
async def drop(c: CallbackQuery):
    await DB.ensure_user(c.from_user.id, c.from_user.username or "")
    if not await DB.spend_coins(c.from_user.id, 1, "free_drop"):
        await c.answer("Нужен 1 жетон. Получи его через /daily.", show_alert=True)
        return
    roll = random.randint(1, 100)
    upto = 0
    chosen = DROP_POOL[-1]
    for item in DROP_POOL:
        upto += item[4]
        if roll <= upto:
            chosen = item
            break
    key, name, rarity, emoji, _ = chosen
    await DB.add_item(c.from_user.id, key, name, rarity, emoji, "free_drop")
    await c.message.answer(f"🎉 Выпало: {emoji} <b>{name}</b>\nРедкость: {rarity}")
    await c.answer()

@dp.callback_query(F.data == "inventory")
async def inventory(c: CallbackQuery):
    rows = await DB.inventory(c.from_user.id)
    if not rows:
        msg = "Инвентарь пуст. Получи жетоны через /daily."
    else:
        msg = "🎒 <b>Инвентарь</b>\n" + "\n".join(
            f"{x['emoji']} {x['name']} — {x['rarity']} · ур. {x['level']} (x{x['qty']})" for x in rows
        )
    await c.message.answer(msg, reply_markup=menu())
    await c.answer()

@dp.callback_query(F.data == "upgrade")
async def upgrade_menu(c: CallbackQuery):
    rows = await DB.upgrade_candidates(c.from_user.id)
    if not rows:
        await c.message.answer("Пока нечего улучшать. Сначала получи предмет через дроп.")
    else:
        kb = InlineKeyboardBuilder()
        for x in rows:
            kb.button(text=f"{x['emoji']} {x['name']} · ур. {x['level']}", callback_data=f"up:{x['id']}")
        kb.adjust(1)
        await c.message.answer("Улучшение стоит 2 бесплатных жетона. При неудаче уровень сохраняется.", reply_markup=kb.as_markup())
    await c.answer()

@dp.callback_query(F.data.startswith("up:"))
async def upgrade(c: CallbackQuery):
    try:
        item_id = int(c.data.split(":", 1)[1])
    except ValueError:
        await c.answer("Некорректный предмет.", show_alert=True)
        return
    result = await DB.try_upgrade(c.from_user.id, item_id)
    messages = {
        "no_item": "Предмет не найден.",
        "no_coins": "Нужно 2 бесплатных жетона. Используй /daily.",
        "max": "Максимальный уровень уже достигнут.",
        "fail": "Неудача. Уровень предмета сохранён.",
    }
    msg = messages.get(result, f"✨ Успех! Теперь уровень {result.split(':')[1]}.")
    await c.message.answer(msg)
    await c.answer()

@dp.callback_query(F.data == "shop")
async def shop(c: CallbackQuery):
    kb = InlineKeyboardBuilder()
    for key, item in COSMETICS.items():
        kb.button(text=f"{item['emoji']} {item['name']} — {item['price']} ⭐", callback_data=f"buy:{key}")
    kb.adjust(1)
    await c.message.answer("⭐ Покупка выдаёт выбранный предмет напрямую, без случайного результата.", reply_markup=kb.as_markup())
    await c.answer()

@dp.callback_query(F.data.startswith("buy:"))
async def buy(c: CallbackQuery):
    key = c.data.split(":", 1)[1]
    item = COSMETICS.get(key)
    if not item or not bot:
        await c.answer("Товар временно недоступен.", show_alert=True)
        return
    payload = f"cosmetic:{c.from_user.id}:{key}:{int(datetime.now(timezone.utc).timestamp())}"
    await bot.send_invoice(
        chat_id=c.from_user.id, title=item["name"],
        description=f"Виртуальный косметический предмет {item['name']} для коллекции в боте.",
        payload=payload, currency="XTR",
        prices=[LabeledPrice(label=item["name"], amount=item["price"])],
        provider_token=""
    )
    await c.answer()

@dp.pre_checkout_query()
async def pre_checkout(q: PreCheckoutQuery):
    if not q.invoice_payload.startswith(f"cosmetic:{q.from_user.id}:"):
        await q.answer(ok=False, error_message="Платёж не прошёл проверку.")
    else:
        await q.answer(ok=True)

@dp.message(F.successful_payment)
async def successful_payment(m: Message):
    p = m.successful_payment
    parts = p.invoice_payload.split(":")
    if len(parts) != 4 or parts[0] != "cosmetic" or parts[1] != str(m.from_user.id):
        await m.answer("Платёж получен, но заказ не прошёл проверку. Обратись к администратору.")
        logging.error("Invalid payment payload for user %s", m.from_user.id)
        return
    key = parts[2]
    item = COSMETICS.get(key)
    if not item:
        await m.answer("Платёж получен. Товар временно недоступен — обратись к администратору.")
        return
    if p.currency != "XTR" or p.total_amount != item["price"]:
        await m.answer("Платёж не прошёл проверку суммы. Обратись к администратору.")
        logging.error("Unexpected payment amount/currency: %s %s", p.total_amount, p.currency)
        return
    granted = await DB.record_payment_and_grant(
        p.telegram_payment_charge_id, m.from_user.id, p.invoice_payload,
        p.total_amount, p.currency, key, item
    )
    if granted:
        await m.answer(f"✅ Оплата подтверждена. Получен предмет: {item['emoji']} <b>{item['name']}</b>.")
    else:
        await m.answer("Этот платёж уже обработан; повторной выдачи нет.")

@asynccontextmanager
async def lifespan(app: FastAPI):
    await DB.init()
    yield

web = FastAPI(title="Skin Drop Admin", lifespan=lifespan)

def require_admin(token: str | None):
    if not ADMIN_TOKEN or token != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")

@web.get("/", response_class=HTMLResponse)
async def admin_page():
    return """<html><head><meta name="viewport" content="width=device-width, initial-scale=1"><title>Skin Drop Admin</title></head>
    <body style="font-family:sans-serif;max-width:760px;margin:40px auto;padding:0 16px"><h1>Skin Drop — Admin</h1>
    <p>Защищённый JSON API: передавай заголовок <code>X-Admin-Token</code>.</p>
    <ul><li>GET /admin/stats</li><li>GET /admin/users</li><li>GET /admin/payments</li></ul></body></html>"""

@web.get("/health")
async def health():
    return {"ok": True}

@web.get("/admin/stats")
async def stats(x_admin_token: str | None = Header(default=None)):
    require_admin(x_admin_token)
    return await DB.stats()

@web.get("/admin/users")
async def users(x_admin_token: str | None = Header(default=None)):
    require_admin(x_admin_token)
    return await DB.users()

@web.get("/admin/payments")
async def payments(x_admin_token: str | None = Header(default=None)):
    require_admin(x_admin_token)
    return await DB.payments()

async def run_bot():
    if not bot:
        raise RuntimeError("BOT_TOKEN environment variable is required")
    await dp.start_polling(bot)

async def run_web():
    server = uvicorn.Server(uvicorn.Config(web, host="0.0.0.0", port=PORT, log_level="info"))
    await server.serve()

async def main():
    if not TOKEN:
        raise RuntimeError("Set BOT_TOKEN in the hosting service's secret variables")
    await DB.init()
    await asyncio.gather(run_bot(), run_web())

if __name__ == "__main__":
    asyncio.run(main())
