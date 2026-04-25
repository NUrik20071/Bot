from aiogram import Bot, Dispatcher, types
import time
import json

async def on_request(request, env):
    # Инициализация бота внутри обработчика для доступа к env
    bot = Bot(token=env.BOT_TOKEN)
    dp = Dispatcher(bot)
    kv = env.WARNS_DB

    # 1. Ответ в ЛС только владельцу
    @dp.message_handler(chat_type=types.ChatType.PRIVATE)
    async def private_handler(message: types.Message):
        if str(message.from_user.id) != str(env.OWNER_ID):
            return # Бот просто молчит для остальных
        
        # Настройка времени мута: /set 604800
        if message.text.startswith('/set'):
            try:
                seconds = message.text.split()[1]
                await kv.put("mute_time", seconds)
                await message.answer(f"✅ Время мута по дефолту: {seconds} сек.")
            except:
                await message.answer("Ошибка. Юзай: /set 604800")

    # 2. Выдача варна (Админы и Владелец)
    @dp.message_handler(commands=['warn'])
    async def give_warn(message: types.Message):
        is_admin = (message.from_user.id == int(env.OWNER_ID)) or                    (await bot.get_chat_member(message.chat.id, message.from_user.id)).is_chat_admin()
        
        if not is_admin or not message.reply_to_message:
            return

        target = message.reply_to_message.from_user
        reason = message.get_args() or "Причина не указана"
        
        # Получаем данные из KV
        raw_data = await kv.get(f"user_{target.id}")
        data = json.loads(raw_data) if raw_data else {"warns": 0, "history": []}
        
        data["warns"] += 1
        data["history"].append({"reason": reason, "at": int(time.time())})
        
        await kv.put(f"user_{target.id}", json.dumps(data))
        
        # Проверка на мут (например, за каждый 3-й варн)
        if data["warns"] % 3 == 0:
            mute_set = await kv.get("mute_time") or "604800"
            until = int(time.time()) + int(mute_set)
            try:
                await bot.restrict_chat_member(message.chat.id, target.id, 
                    permissions=types.ChatPermissions(can_send_messages=False),
                    until_date=until)
                await message.answer(f"🚫 {target.first_name} получил 3-й варн и улетел в мут!")
            except:
                await message.answer("⚠️ Не удалось замутить. Проверь мои права.")
        else:
            await message.answer(f"⚠️ {target.first_name}, вам выдан варн ({data['warns']}/3).\nПричина: {reason}")

    # 3. Просмотр варнов (Для всех)
    @dp.message_handler(commands=['my_warns', 'warns'])
    async def show_warns(message: types.Message):
        target = message.reply_to_message.from_user if message.reply_to_message else message.from_user
        raw_data = await kv.get(f"user_{target.id}")
        
        if not raw_data:
            await message.answer(f"У {target.first_name} нет варнов.")
            return
            
        data = json.loads(raw_data)
        mute_set = int(await kv.get("mute_time") or 604800)
        
        text = f"📊 Статус: {target.first_name}\nВсего варнов: {data['warns']}\n\n"
        for i, w in enumerate(data['history'], 1):
            expires_in = (w['at'] + mute_set) - int(time.time())
            status = f"⌛ истекает через {expires_in//3600}ч" if expires_in > 0 else "✅ истек"
            text += f"{i}. {w['reason']} | {status}\n"
            
        await message.answer(text)

    # Обработка входящего Update
    data = await request.json()
    update = types.Update(**data)
    await dp.process_update(update)
    return Response("OK", status=200)

