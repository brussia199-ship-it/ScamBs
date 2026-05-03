import asyncio
import sqlite3
import os
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import Message, CallbackQuery, FSInputFile
import json

# ========== КОНФИГУРАЦИЯ ==========
BOT_TOKEN = "8655931539:AAE9DjvBYScMBrutC17TP0UaLBc_jj_bo2U"  # Замените на токен вашего бота
ADMIN_IDS = [7673683792]  # ID администраторов (укажите свои)

# Цена удаления из базы в звездах (Telegram Stars)
DELETE_PRICE = 500  # 500 звезд

# Инициализация бота
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ========== БАЗА ДАННЫХ ==========
def init_db():
    conn = sqlite3.connect('scambase.db')
    cur = conn.cursor()
    
    # Таблица пользователей в ScamBase
    cur.execute('''
        CREATE TABLE IF NOT EXISTS scam_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,  -- ID человека в Telegram (или username)
            name TEXT,
            tags TEXT DEFAULT '[]',  -- JSON массив меток
            added_by INTEGER,
            added_date TEXT,
            proof TEXT  -- Ссылка на доказательства
        )
    ''')
    
    # Таблица заявок
    cur.execute('''
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            reporter_id INTEGER,
            reported_user TEXT,
            reason TEXT,
            proof_photo TEXT,
            proof_video TEXT,
            status TEXT DEFAULT 'pending',  -- pending, approved, rejected
            created_at TEXT
        )
    ''')
    
    # Таблица для хранения звезд (баланс пользователей)
    cur.execute('''
        CREATE TABLE IF NOT EXISTS user_stars (
            user_id INTEGER PRIMARY KEY,
            balance INTEGER DEFAULT 0
        )
    ''')
    
    conn.commit()
    conn.close()

init_db()

# ========== FSM СОСТОЯНИЯ ==========
class AddUserState(StatesGroup):
    waiting_for_user_id = State()
    waiting_for_name = State()
    waiting_for_tags = State()
    waiting_for_proof = State()

class RemoveUserState(StatesGroup):
    waiting_for_user_id = State()

class ReportUserState(StatesGroup):
    waiting_for_user_id = State()
    waiting_for_reason = State()
    waiting_for_photo = State()
    waiting_for_video = State()

class BroadcastState(StatesGroup):
    waiting_for_message = State()

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def add_user_to_scamdb(user_id: str, name: str, tags: list, admin_id: int, proof: str = ""):
    conn = sqlite3.connect('scambase.db')
    cur = conn.cursor()
    cur.execute('''
        INSERT INTO scam_users (user_id, name, tags, added_by, added_date, proof)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (user_id, name, json.dumps(tags), admin_id, datetime.now().isoformat(), proof))
    conn.commit()
    conn.close()

def remove_user_from_scamdb(user_id: str):
    conn = sqlite3.connect('scambase.db')
    cur = conn.cursor()
    cur.execute('DELETE FROM scam_users WHERE user_id = ?', (user_id,))
    deleted = cur.rowcount > 0
    conn.commit()
    conn.close()
    return deleted

def search_user(user_id: str):
    conn = sqlite3.connect('scambase.db')
    cur = conn.cursor()
    cur.execute('SELECT user_id, name, tags, added_date, proof FROM scam_users WHERE user_id = ?', (user_id,))
    user = cur.fetchone()
    conn.close()
    return user

def update_user_tags(user_id: str, tags: list):
    conn = sqlite3.connect('scambase.db')
    cur = conn.cursor()
    cur.execute('UPDATE scam_users SET tags = ? WHERE user_id = ?', (json.dumps(tags), user_id))
    conn.commit()
    conn.close()

def get_all_users_for_broadcast():
    conn = sqlite3.connect('scambase.db')
    cur = conn.cursor()
    cur.execute('SELECT DISTINCT reporter_id FROM reports WHERE status="approved"')
    users = cur.fetchall()
    conn.close()
    return [u[0] for u in users]

# ========== ОБЫЧНЫЕ КОМАНДЫ ==========
@dp.message(Command("start"))
async def cmd_start(message: Message):
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="🔍 Поиск по ScamBase", callback_data="search")],
        [types.InlineKeyboardButton(text="⭐ Удалить пользователя (500⭐)", callback_data="delete_for_stars")],
        [types.InlineKeyboardButton(text="📝 Подать заявку на скаммера", callback_data="report")],
        [types.InlineKeyboardButton(text="💰 Мой баланс звезд", callback_data="my_balance")]
    ])
    
    if is_admin(message.from_user.id):
        keyboard.inline_keyboard.append([types.InlineKeyboardButton(text="👑 Админ-панель", callback_data="admin_panel")])
    
    await message.answer("🔫 Добро пожаловать в ScamBase бот!\n\nВыберите действие:", reply_markup=keyboard)

@dp.callback_query(F.data == "search")
async def search_menu(callback: CallbackQuery):
    await callback.message.answer("Введите ID пользователя (или @username) для поиска в ScamBase:")
    await callback.answer()

@dp.message(F.text & ~F.text.startswith('/'))
async def handle_search(message: Message):
    user_id = message.text.strip()
    user = search_user(user_id)
    
    if user:
        user_id_db, name, tags_json, added_date, proof = user
        tags = json.loads(tags_json)
        tags_text = ", ".join(tags) if tags else "❌ Нет меток"
        
        response = f"⚠️ НАЙДЕН В SCAMBASE ⚠️\n\n"
        response += f"🆔 ID: {user_id_db}\n"
        response += f"👤 Имя: {name}\n"
        response += f"🏷️ Метки: {tags_text}\n"
        response += f"📅 Дата добавления: {added_date}\n"
        if proof:
            response += f"🔗 Доказательства: {proof}\n"
        
        await message.answer(response)
    else:
        await message.answer(f"✅ Пользователь {user_id} не найден в ScamBase.")

@dp.callback_query(F.data == "delete_for_stars")
async def delete_for_stars(callback: CallbackQuery):
    await callback.message.answer(f"Введите ID пользователя для удаления из ScamBase.\nСтоимость: {DELETE_PRICE} ⭐")
    await callback.answer()

@dp.message(StateFilter(None))
async def handle_delete_request(message: Message):
    # Простая логика удаления (в реальном боте нужно использовать FSM)
    user_to_delete = message.text.strip()
    user = search_user(user_to_delete)
    
    if not user:
        await message.answer("❌ Пользователь не найден в ScamBase.")
        return
    
    conn = sqlite3.connect('scambase.db')
    cur = conn.cursor()
    cur.execute('SELECT balance FROM user_stars WHERE user_id = ?', (message.from_user.id,))
    result = cur.fetchone()
    balance = result[0] if result else 0
    conn.close()
    
    if balance >= DELETE_PRICE:
        # Снимаем звезды
        conn = sqlite3.connect('scambase.db')
        cur = conn.cursor()
        cur.execute('UPDATE user_stars SET balance = balance - ? WHERE user_id = ?', (DELETE_PRICE, message.from_user.id))
        if cur.rowcount == 0:
            cur.execute('INSERT INTO user_stars (user_id, balance) VALUES (?, ?)', (message.from_user.id, -DELETE_PRICE))
        conn.commit()
        conn.close()
        
        # Удаляем пользователя
        if remove_user_from_scamdb(user_to_delete):
            await message.answer(f"✅ Пользователь {user_to_delete} удален из ScamBase!\nСписано {DELETE_PRICE} ⭐")
        else:
            await message.answer("❌ Ошибка удаления")
    else:
        await message.answer(f"❌ Недостаточно звезд! Нужно {DELETE_PRICE} ⭐, у вас {balance} ⭐")

@dp.callback_query(F.data == "report")
async def report_menu(callback: CallbackQuery, state: FSMContext):
    await state.set_state(ReportUserState.waiting_for_user_id)
    await callback.message.answer("📝 Введите ID или @username подозреваемого в скаме:")
    await callback.answer()

@dp.message(ReportUserState.waiting_for_user_id)
async def process_report_user(message: Message, state: FSMContext):
    await state.update_data(reported_user=message.text.strip())
    await state.set_state(ReportUserState.waiting_for_reason)
    await message.answer("📝 Опишите причину заявки:")

@dp.message(ReportUserState.waiting_for_reason)
async def process_report_reason(message: Message, state: FSMContext):
    await state.update_data(reason=message.text.strip())
    await state.set_state(ReportUserState.waiting_for_photo)
    await message.answer("📸 Отправьте ФОТО доказательства (или отправьте 'пропустить'):")

@dp.message(ReportUserState.waiting_for_photo)
async def process_report_photo(message: Message, state: FSMContext):
    photo_id = None
    if message.photo:
        photo_id = message.photo[-1].file_id
    elif message.text and message.text.lower() == "пропустить":
        photo_id = ""
    else:
        await message.answer("Пожалуйста, отправьте фото или нажмите 'пропустить'")
        return
    
    await state.update_data(proof_photo=photo_id)
    await state.set_state(ReportUserState.waiting_for_video)
    await message.answer("🎥 Отправьте ВИДЕО доказательства (или отправьте 'пропустить'):")

@dp.message(ReportUserState.waiting_for_video)
async def process_report_video(message: Message, state: FSMContext):
    video_id = None
    if message.video:
        video_id = message.video.file_id
    elif message.text and message.text.lower() == "пропустить":
        video_id = ""
    else:
        await message.answer("Пожалуйста, отправьте видео или нажмите 'пропустить'")
        return
    
    data = await state.get_data()
    
    conn = sqlite3.connect('scambase.db')
    cur = conn.cursor()
    cur.execute('''
        INSERT INTO reports (reporter_id, reported_user, reason, proof_photo, proof_video, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (message.from_user.id, data['reported_user'], data['reason'], 
          data['proof_photo'], video_id, 'pending', datetime.now().isoformat()))
    conn.commit()
    conn.close()
    
    await message.answer("✅ Заявка отправлена администраторам на рассмотрение!")
    
    # Уведомляем админов
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, f"📢 Новая заявка!\nОт: {message.from_user.id}\nНа: {data['reported_user']}\nПричина: {data['reason']}")
        except:
            pass
    
    await state.clear()

@dp.callback_query(F.data == "my_balance")
async def show_balance(callback: CallbackQuery):
    conn = sqlite3.connect('scambase.db')
    cur = conn.cursor()
    cur.execute('SELECT balance FROM user_stars WHERE user_id = ?', (callback.from_user.id,))
    result = cur.fetchone()
    balance = result[0] if result else 0
    conn.close()
    await callback.message.answer(f"💰 Ваш баланс: {balance} ⭐")
    await callback.answer()

# ========== АДМИН-ПАНЕЛЬ ==========
@dp.callback_query(F.data == "admin_panel")
async def admin_panel(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ только для администраторов", show_alert=True)
        return
    
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="➕ Занести человека в базу", callback_data="admin_add")],
        [types.InlineKeyboardButton(text="➖ Вынести человека из базы", callback_data="admin_remove")],
        [types.InlineKeyboardButton(text="🏷️ Выдать/изменить метки", callback_data="admin_tags")],
        [types.InlineKeyboardButton(text="📢 Сделать рассылку", callback_data="admin_broadcast")],
        [types.InlineKeyboardButton(text="📋 Просмотреть заявки", callback_data="admin_reports")]
    ])
    
    await callback.message.answer("👑 Админ-панель:", reply_markup=keyboard)
    await callback.answer()

@dp.callback_query(F.data == "admin_add")
async def admin_add_menu(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AddUserState.waiting_for_user_id)
    await callback.message.answer("Введите ID пользователя для добавления в ScamBase:")
    await callback.answer()

@dp.message(AddUserState.waiting_for_user_id)
async def admin_add_user_id(message: Message, state: FSMContext):
    await state.update_data(user_id=message.text.strip())
    await state.set_state(AddUserState.waiting_for_name)
    await message.answer("Введите имя пользователя:")

@dp.message(AddUserState.waiting_for_name)
async def admin_add_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text.strip())
    await state.set_state(AddUserState.waiting_for_tags)
    await message.answer("Введите метки через запятую (доступно: Scammer, Face, Worker)\nПример: Scammer, Face")

@dp.message(AddUserState.waiting_for_tags)
async def admin_add_tags(message: Message, state: FSMContext):
    tags_input = message.text.strip()
    tags = [t.strip() for t in tags_input.split(',') if t.strip() in ['Scammer', 'Face', 'Worker']]
    await state.update_data(tags=tags)
    await state.set_state(AddUserState.waiting_for_proof)
    await message.answer("Введите ссылку на доказательства (или отправьте 'нет'):")

@dp.message(AddUserState.waiting_for_proof)
async def admin_add_proof(message: Message, state: FSMContext):
    proof = message.text.strip() if message.text.lower() != 'нет' else ""
    data = await state.get_data()
    
    add_user_to_scamdb(data['user_id'], data['name'], data['tags'], message.from_user.id, proof)
    await message.answer(f"✅ Пользователь {data['user_id']} добавлен в ScamBase с метками: {', '.join(data['tags'])}")
    await state.clear()

@dp.callback_query(F.data == "admin_remove")
async def admin_remove_menu(callback: CallbackQuery, state: FSMContext):
    await state.set_state(RemoveUserState.waiting_for_user_id)
    await callback.message.answer("Введите ID пользователя для удаления из ScamBase:")
    await callback.answer()

@dp.message(RemoveUserState.waiting_for_user_id)
async def admin_remove_user(message: Message, state: FSMContext):
    user_id = message.text.strip()
    if remove_user_from_scamdb(user_id):
        await message.answer(f"✅ Пользователь {user_id} удален из ScamBase")
    else:
        await message.answer(f"❌ Пользователь {user_id} не найден")
    await state.clear()

@dp.callback_query(F.data == "admin_tags")
async def admin_tags_menu(callback: CallbackQuery):
    await callback.message.answer("Введите ID пользователя и новые метки через пробел\nПример: @username Scammer,Face")
    await callback.answer()

@dp.message(F.text & ~F.text.startswith('/'))
async def handle_tag_update(message: Message):
    if not is_admin(message.from_user.id):
        return
    
    parts = message.text.split(maxsplit=1)
    if len(parts) != 2:
        await message.answer("❌ Неверный формат. Используйте: ID пользователя метки\nПример: @john Scammer,Face")
        return
    
    user_id, tags_str = parts
    tags = [t.strip() for t in tags_str.split(',') if t.strip() in ['Scammer', 'Face', 'Worker']]
    
    if not tags:
        await message.answer("❌ Некорректные метки. Доступно: Scammer, Face, Worker")
        return
    
    update_user_tags(user_id, tags)
    await message.answer(f"✅ Метки для {user_id} обновлены: {', '.join(tags)}")

@dp.callback_query(F.data == "admin_broadcast")
async def admin_broadcast_menu(callback: CallbackQuery, state: FSMContext):
    await state.set_state(BroadcastState.waiting_for_message)
    await callback.message.answer("Введите текст рассылки (можно с фото, видео, документом):")
    await callback.answer()

@dp.message(BroadcastState.waiting_for_message)
async def send_broadcast(message: Message, state: FSMContext):
    users = get_all_users_for_broadcast()
    success = 0
    
    for user_id in users:
        try:
            if message.text:
                await bot.send_message(user_id, f"📢 РАССЫЛКА SCAMBASE:\n\n{message.text}")
            elif message.photo:
                await bot.send_photo(user_id, message.photo[-1].file_id, caption=f"📢 РАССЫЛКА SCAMBASE:\n\n{message.caption or ''}")
            elif message.video:
                await bot.send_video(user_id, message.video.file_id, caption=f"📢 РАССЫЛКА SCAMBASE:\n\n{message.caption or ''}")
            success += 1
        except:
            pass
    
    await message.answer(f"✅ Рассылка завершена! Отправлено {success} пользователям")
    await state.clear()

@dp.callback_query(F.data == "admin_reports")
async def view_reports(callback: CallbackQuery):
    conn = sqlite3.connect('scambase.db')
    cur = conn.cursor()
    cur.execute('SELECT id, reporter_id, reported_user, reason, status, created_at FROM reports WHERE status="pending"')
    reports = cur.fetchall()
    conn.close()
    
    if not reports:
        await callback.message.answer("Нет новых заявок")
        await callback.answer()
        return
    
    for report in reports:
        report_id, reporter_id, reported_user, reason, status, created_at = report
        keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="✅ Одобрить", callback_data=f"approve_{report_id}"),
             types.InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject_{report_id}")]
        ])
        
        await callback.message.answer(
            f"📋 Заявка #{report_id}\n"
            f"От: {reporter_id}\n"
            f"На: {reported_user}\n"
            f"Причина: {reason}\n"
            f"Дата: {created_at}",
            reply_markup=keyboard
        )
    await callback.answer()

@dp.callback_query(F.data.startswith("approve_"))
async def approve_report(callback: CallbackQuery):
    report_id = int(callback.data.split("_")[1])
    
    conn = sqlite3.connect('scambase.db')
    cur = conn.cursor()
    cur.execute('SELECT reported_user, reporter_id FROM reports WHERE id = ?', (report_id,))
    report = cur.fetchone()
    
    if report:
        reported_user, reporter_id = report
        cur.execute('UPDATE reports SET status = "approved" WHERE id = ?', (report_id,))
        # Начисляем звезды за успешный репорт
        cur.execute('UPDATE user_stars SET balance = balance + 50 WHERE user_id = ?', (reporter_id,))
        if cur.rowcount == 0:
            cur.execute('INSERT INTO user_stars (user_id, balance) VALUES (?, ?)', (reporter_id, 50))
        conn.commit()
        
        await callback.message.answer(f"✅ Заявка #{report_id} одобрена! Репортеру начислено 50 ⭐")
        try:
            await bot.send_message(reporter_id, f"✅ Ваша заявка на {reported_user} одобрена! Получено 50 ⭐")
        except:
            pass
    else:
        await callback.message.answer("❌ Заявка не найдена")
    
    conn.close()
    await callback.answer()

@dp.callback_query(F.data.startswith("reject_"))
async def reject_report(callback: CallbackQuery):
    report_id = int(callback.data.split("_")[1])
    
    conn = sqlite3.connect('scambase.db')
    cur = conn.cursor()
    cur.execute('UPDATE reports SET status = "rejected" WHERE id = ?', (report_id,))
    conn.commit()
    conn.close()
    
    await callback.message.answer(f"❌ Заявка #{report_id} отклонена")
    await callback.answer()

# ========== ЗАПУСК БОТА ==========
async def main():
    print("🤖 ScamBase бот запущен")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
