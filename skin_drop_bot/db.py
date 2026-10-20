import aiosqlite
from datetime import date
import random

class Database:
    def __init__(self, path):
        self.path = path

    async def init(self):
        async with aiosqlite.connect(self.path) as db:
            await db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY, username TEXT NOT NULL DEFAULT '',
                coins INTEGER NOT NULL DEFAULT 0, last_daily TEXT
            );
            CREATE TABLE IF NOT EXISTS inventory (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
                item_key TEXT NOT NULL, name TEXT NOT NULL, rarity TEXT NOT NULL,
                emoji TEXT NOT NULL, level INTEGER NOT NULL DEFAULT 1,
                qty INTEGER NOT NULL DEFAULT 1, source TEXT NOT NULL,
                UNIQUE(user_id, item_key, level)
            );
            CREATE TABLE IF NOT EXISTS payments (
                telegram_charge_id TEXT PRIMARY KEY, user_id INTEGER NOT NULL,
                payload TEXT NOT NULL, amount INTEGER NOT NULL, currency TEXT NOT NULL,
                item_key TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS ledger (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
                delta INTEGER NOT NULL, reason TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """)
            await db.commit()

    async def ensure_user(self, user_id, username):
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT INTO users(user_id,username) VALUES(?,?) "
                "ON CONFLICT(user_id) DO UPDATE SET username=excluded.username",
                (user_id, username)
            )
            await db.commit()

    async def get_user(self, user_id):
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
            row = await cur.fetchone()
            return dict(row) if row else {"coins": 0}

    async def claim_daily(self, user_id):
        today = date.today().isoformat()
        async with aiosqlite.connect(self.path) as db:
            await db.execute("BEGIN IMMEDIATE")
            cur = await db.execute("SELECT last_daily FROM users WHERE user_id=?", (user_id,))
            row = await cur.fetchone()
            if not row or row[0] == today:
                await db.rollback()
                return False
            await db.execute("UPDATE users SET coins=coins+3,last_daily=? WHERE user_id=?", (today, user_id))
            await db.execute("INSERT INTO ledger(user_id,delta,reason) VALUES(?,3,'daily_reward')", (user_id,))
            await db.commit()
            return True

    async def spend_coins(self, user_id, amount, reason):
        async with aiosqlite.connect(self.path) as db:
            await db.execute("BEGIN IMMEDIATE")
            cur = await db.execute("SELECT coins FROM users WHERE user_id=?", (user_id,))
            row = await cur.fetchone()
            if not row or row[0] < amount:
                await db.rollback()
                return False
            await db.execute("UPDATE users SET coins=coins-? WHERE user_id=?", (amount, user_id))
            await db.execute("INSERT INTO ledger(user_id,delta,reason) VALUES(?,?,?)", (user_id, -amount, reason))
            await db.commit()
            return True

    async def add_item(self, user_id, key, name, rarity, emoji, source):
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """INSERT INTO inventory(user_id,item_key,name,rarity,emoji,level,qty,source)
                VALUES(?,?,?,?,?,1,1,?)
                ON CONFLICT(user_id,item_key,level) DO UPDATE SET qty=qty+1""",
                (user_id, key, name, rarity, emoji, source)
            )
            await db.commit()

    async def inventory(self, user_id):
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM inventory WHERE user_id=? AND qty>0 ORDER BY level DESC,rarity", (user_id,))
            return [dict(row) for row in await cur.fetchall()]

    async def upgrade_candidates(self, user_id):
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM inventory WHERE user_id=? AND qty>0 AND level<5 ORDER BY id", (user_id,))
            return [dict(row) for row in await cur.fetchall()]

    async def try_upgrade(self, user_id, item_id):
        chances = {1: 75, 2: 55, 3: 35, 4: 20}
        async with aiosqlite.connect(self.path) as db:
            await db.execute("BEGIN IMMEDIATE")
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM inventory WHERE id=? AND user_id=? AND qty>0", (item_id, user_id))
            item = await cur.fetchone()
            if not item:
                await db.rollback()
                return "no_item"
            if item["level"] >= 5:
                await db.rollback()
                return "max"
            cur = await db.execute("SELECT coins FROM users WHERE user_id=?", (user_id,))
            user = await cur.fetchone()
            if not user or user[0] < 2:
                await db.rollback()
                return "no_coins"
            await db.execute("UPDATE users SET coins=coins-2 WHERE user_id=?", (user_id,))
            await db.execute("INSERT INTO ledger(user_id,delta,reason) VALUES(?, -2, 'upgrade_attempt')", (user_id,))
            success = random.randint(1, 100) <= chances[item["level"]]
            if success:
                await db.execute("UPDATE inventory SET qty=qty-1 WHERE id=?", (item_id,))
                await db.execute(
                    """INSERT INTO inventory(user_id,item_key,name,rarity,emoji,level,qty,source)
                    VALUES(?,?,?,?,?,?,1,'upgrade')
                    ON CONFLICT(user_id,item_key,level) DO UPDATE SET qty=qty+1""",
                    (user_id, item["item_key"], item["name"], item["rarity"], item["emoji"], item["level"] + 1)
                )
            await db.commit()
            return f"ok:{item['level']+1}" if success else "fail"

    async def record_payment_and_grant(self, charge_id, user_id, payload, amount, currency, item_key, item):
        async with aiosqlite.connect(self.path) as db:
            await db.execute("BEGIN IMMEDIATE")
            cur = await db.execute("SELECT 1 FROM payments WHERE telegram_charge_id=?", (charge_id,))
            if await cur.fetchone():
                await db.rollback()
                return False
            await db.execute(
                "INSERT INTO payments(telegram_charge_id,user_id,payload,amount,currency,item_key) VALUES(?,?,?,?,?,?)",
                (charge_id, user_id, payload, amount, currency, item_key)
            )
            await db.execute(
                """INSERT INTO inventory(user_id,item_key,name,rarity,emoji,level,qty,source)
                VALUES(?,?,?,?,?,1,1,'stars_purchase')
                ON CONFLICT(user_id,item_key,level) DO UPDATE SET qty=qty+1""",
                (user_id, item_key, item["name"], item["rarity"], item["emoji"])
            )
            await db.commit()
            return True

    async def stats(self):
        async with aiosqlite.connect(self.path) as db:
            users = (await (await db.execute("SELECT COUNT(*) FROM users")).fetchone())[0]
            items = (await (await db.execute("SELECT COALESCE(SUM(qty),0) FROM inventory")).fetchone())[0]
            payments = (await (await db.execute("SELECT COUNT(*) FROM payments")).fetchone())[0]
            stars = (await (await db.execute("SELECT COALESCE(SUM(amount),0) FROM payments WHERE currency='XTR'")).fetchone())[0]
            return {"users": users, "inventory_items": items, "payments": payments, "stars_received": stars}

    async def users(self):
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT user_id,username,coins,last_daily FROM users ORDER BY user_id DESC LIMIT 500")
            return [dict(row) for row in await cur.fetchall()]

    async def payments(self):
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM payments ORDER BY created_at DESC LIMIT 500")
            return [dict(row) for row in await cur.fetchall()]
