import asyncio
import sqlite3
import os
from datetime import datetime
from typing import Optional
import logging

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, LabeledPrice
from aiogram.utils.keyboard import InlineKeyboardBuilder

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ================= КОНФИГУРАЦИЯ =================
BOT_TOKEN = "8655931539:AAE9DjvBYScMBrutC17TP0UaLBc_jj_bo2U"  # Замените на ваш токен
ADMIN_IDS = [7673683792]  # ID администраторов (укажите свой ID)
STAR_PRICE = 500  # Цена удаления в звёздах

# ================= СОСТОЯНИЯ FSM =================
class ReportStates(StatesGroup):
    waiting_for_username = State()
    waiting_for_proof_photos = State()
    waiting_for_proof_videos = State()

class AdminAddStates(StatesGroup):
    waiting_for_username = State()

class AdminRemoveStates(StatesGroup):
    waiting_for_username = State()

class AdminLabelStates(StatesGroup):
    waiting_for_username = State()

class MailingStates(StatesGroup):
    waiting_for_message = State()
    waiting_for_confirmation = State()

# ================= ИНИЦИАЛИЗАЦИЯ =================
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# Получаем путь к директории бота
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'scambase.db')


# ================= РАБОТА С БАЗОЙ ДАННЫХ =================
def init_db():
    """Инициализация базы данных (не удаляется при перезагрузке)"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    # Таблица пользователей в ScamBase
    cur.execute('''
        CREATE TABLE IF NOT EXISTS scambase (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            label TEXT DEFAULT 'SCAM',
            added_by INTEGER,
            added_date TEXT,
            proof_photos TEXT DEFAULT '',
            proof_videos TEXT DEFAULT ''
        )
    ''')
    
    # Таблица заявок от пользователей
    cur.execute('''
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            reported_by INTEGER,
            proof_photos TEXT,
            proof_videos TEXT,
            status TEXT DEFAULT 'pending',
            report_date TEXT
        )
    ''')
    
    # Таблица для отслеживания оплат
    cur.execute('''
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            amount INTEGER,
            payment_id TEXT,
            status TEXT DEFAULT 'pending',
            payment_date TEXT
        )
    ''')
    
    # Таблица для пользователей (для рассылки)
    cur.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            joined_date TEXT,
            last_activity TEXT
        )
    ''')
    
    conn.commit()
    conn.close()
    logger.info("База данных инициализирована: %s", DB_PATH)


def normalize_username(username: str) -> str:
    """Нормализация username - убираем @ и пробелы, приводим к нижнему регистру"""
    return username.strip().replace('@', '').lower()


def is_in_scambase(username: str) -> Optional[str]:
    """Проверка, есть ли человек в базе"""
    normalized = normalize_username(username)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('SELECT label FROM scambase WHERE LOWER(username) = ?', (normalized,))
    result = cur.fetchone()
    conn.close()
    return result[0] if result else None


def add_to_scambase(username: str, label: str, admin_id: int, proof_photos: str = '', proof_videos: str = '') -> bool:
    """Добавление в базу"""
    try:
        normalized = normalize_username(username)
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute('''
            INSERT INTO scambase (username, label, added_by, added_date, proof_photos, proof_videos)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (normalized, label, admin_id, datetime.now().isoformat(), proof_photos, proof_videos))
        conn.commit()
        conn.close()
        return True
    except sqlite3.IntegrityError:
        return False


def remove_from_scambase(username: str) -> bool:
    """Удаление из базы"""
    normalized = normalize_username(username)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('DELETE FROM scambase WHERE LOWER(username) = ?', (normalized,))
    deleted = cur.rowcount > 0
    conn.commit()
    conn.close()
    return deleted


def update_label(username: str, new_label: str) -> bool:
    """Обновление метки"""
    if new_label not in ['FAKE', 'SCAM', 'WORKER']:
        return False
    normalized = normalize_username(username)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('UPDATE scambase SET label = ? WHERE LOWER(username) = ?', (new_label, normalized))
    updated = cur.rowcount > 0
    conn.commit()
    conn.close()
    return updated


def add_report(username: str, user_id: int, photos: list, videos: list) -> bool:
    """Добавление заявки"""
    normalized = normalize_username(username)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('''
        INSERT INTO reports (username, reported_by, proof_photos, proof_videos, status, report_date)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (normalized, user_id, ','.join(photos), ','.join(videos), 'pending', datetime.now().isoformat()))
    conn.commit()
    conn.close()
    return True


def get_pending_reports() -> list:
    """Получение заявок"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('SELECT id, username, reported_by, proof_photos, proof_videos, report_date FROM reports WHERE status = "pending"')
    results = cur.fetchall()
    conn.close()
    return results


def approve_report(report_id: int, username: str, label: str, admin_id: int):
    """Одобрение заявки"""
    normalized = normalize_username(username)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('UPDATE reports SET status = "approved" WHERE id = ?', (report_id,))
    cur.execute('''
        INSERT OR IGNORE INTO scambase (username, label, added_by, added_date)
        VALUES (?, ?, ?, ?)
    ''', (normalized, label, admin_id, datetime.now().isoformat()))
    conn.commit()
    conn.close()


def reject_report(report_id: int):
    """Отклонение заявки"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('UPDATE reports SET status = "rejected" WHERE id = ?', (report_id,))
    conn.commit()
    conn.close()


def list_all_users() -> list:
    """Для отладки - показать всех пользователей в базе"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('SELECT username, label, added_date FROM scambase ORDER BY added_date DESC')
    results = cur.fetchall()
    conn.close()
    return results


def save_payment(user_id: int, username: str, amount: int, payment_id: str) -> bool:
    """Сохранение информации об оплате"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute('''
            INSERT INTO payments (user_id, username, amount, payment_id, status, payment_date)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (user_id, username, amount, payment_id, 'completed', datetime.now().isoformat()))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"Ошибка сохранения платежа: {e}")
        return False


def register_user(user_id: int, username: str, first_name: str, last_name: str = ""):
    """Регистрация пользователя для рассылки"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute('''
            INSERT OR REPLACE INTO users (user_id, username, first_name, last_name, joined_date, last_activity)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (user_id, username or str(user_id), first_name, last_name, datetime.now().isoformat(), datetime.now().isoformat()))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Ошибка регистрации пользователя: {e}")


def get_all_users() -> list:
    """Получение всех пользователей для рассылки"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('SELECT user_id, username, first_name FROM users')
    results = cur.fetchall()
    conn.close()
    return results


def update_user_activity(user_id: int):
    """Обновление активности пользователя"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute('UPDATE users SET last_activity = ? WHERE user_id = ?', (datetime.now().isoformat(), user_id))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Ошибка обновления активности: {e}")


# ================= КЛАВИАТУРЫ =================
def main_menu_keyboard(is_admin_user: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🔍 Поиск в ScamBase", callback_data="search")
    builder.button(text="⭐ Удалить себя (В разработке...)", callback_data="")
    builder.button(text="📝 Подать заявку на скамера", callback_data="report")
    builder.button(text="📊 Статистика", callback_data="stats")
    
    if is_admin_user:
        builder.button(text="👑 Админ-панель", callback_data="admin_panel")
    
    builder.adjust(1)
    return builder.as_markup()


def admin_panel_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Добавить в базу", callback_data="admin_add")
    builder.button(text="❌ Удалить из базы", callback_data="admin_remove")
    builder.button(text="🏷️ Выдать метку", callback_data="admin_label")
    builder.button(text="📋 Заявки от пользователей", callback_data="admin_reports")
    builder.button(text="📜 Список всех в базе", callback_data="admin_list")
    builder.button(text="📢 Рассылка", callback_data="admin_mailing")
    builder.button(text="📊 Статистика бота", callback_data="admin_stats")
    builder.button(text="🔙 Назад", callback_data="back_to_menu")
    builder.adjust(1)
    return builder.as_markup()


def label_keyboard(username: str, action: str = "add") -> InlineKeyboardMarkup:
    """Клавиатура с метками: FAKE, SCAM, WORKER"""
    builder = InlineKeyboardBuilder()
    builder.button(text="🎭 FAKE", callback_data=f"{action}_label_{username}_FAKE")
    builder.button(text="⚠️ SCAM", callback_data=f"{action}_label_{username}_SCAM")
    builder.button(text="🛠️ WORKER", callback_data=f"{action}_label_{username}_WORKER")
    builder.button(text="🔙 Отмена", callback_data="admin_panel")
    builder.adjust(1)
    return builder.as_markup()


def mailing_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура для рассылки"""
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Отправить", callback_data="mailing_send")
    builder.button(text="❌ Отмена", callback_data="mailing_cancel")
    builder.adjust(2)
    return builder.as_markup()


# ================= ОБЩИЕ ХЕНДЛЕРЫ =================
@dp.message(Command("start"))
async def cmd_start(message: Message):
    user_id = message.from_user.id
    username = message.from_user.username or str(user_id)
    first_name = message.from_user.first_name or ""
    last_name = message.from_user.last_name or ""
    
    # Регистрируем пользователя
    register_user(user_id, username, first_name, last_name)
    update_user_activity(user_id)
    
    is_admin_user = user_id in ADMIN_IDS
    await message.answer(
        "👋 Добро пожаловать в ScamBase Bot!\n\n"
        "🔍 Я помогу проверить, есть ли человек в базе скамеров.\n"
        "📝 Также ты можешь подать заявку на добавление скамера с доказательствами.\n\n"
        "Используй кнопки ниже для навигации:",
        reply_markup=main_menu_keyboard(is_admin_user)
    )


@dp.message(Command("listdb"))
async def list_database(message: Message):
    """Команда для админов - показать всех в базе"""
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ Доступ запрещён!")
        return
    
    users = list_all_users()
    
    if not users:
        await message.answer("📭 База данных пуста.")
        return
    
    label_emoji = {"FAKE": "🎭", "SCAM": "⚠️", "WORKER": "🛠️"}
    
    text = "📋 <b>Список всех в базе:</b>\n\n"
    for username, label, date in users[:20]:
        emoji = label_emoji.get(label, "📌")
        text += f"{emoji} @{username} — {label}\n"
        text += f"   📅 {date[:16]}\n\n"
    
    if len(users) > 20:
        text += f"\n... и ещё {len(users) - 20} записей"
    
    await message.answer(text, parse_mode="HTML")


@dp.callback_query(F.data == "search")
async def search_prompt(callback: CallbackQuery, state: FSMContext):
    update_user_activity(callback.from_user.id)
    await callback.message.answer("🔍 Введите username человека для проверки (без @):")
    await callback.answer()
    await state.set_state("waiting_for_search")


@dp.message(StateFilter("waiting_for_search"))
async def perform_search(message: Message, state: FSMContext):
    update_user_activity(message.from_user.id)
    username = normalize_username(message.text.strip())
    label = is_in_scambase(username)
    
    label_emoji = {
        "FAKE": "🎭",
        "SCAM": "⚠️",
        "WORKER": "🛠️"
    }
    
    if label:
        emoji = label_emoji.get(label, "🔴")
        await message.answer(
            f"⚠️ <b>РЕЗУЛЬТАТ ПОИСКА</b> ⚠️\n\n"
            f"👤 Username: @{username}\n"
            f"🏷️ Метка: {emoji} {label}\n"
            f"📌 Статус: <b>В БАЗЕ СКАМЕРОВ</b>\n\n"
            f"Будьте осторожны в общении с этим человеком!",
            parse_mode="HTML"
        )
    else:
        await message.answer(
            f"✅ <b>РЕЗУЛЬТАТ ПОИСКА</b> ✅\n\n"
            f"👤 Username: @{username}\n"
            f"📌 Статус: <b>НЕ НАЙДЕН В БАЗЕ</b>\n\n"
            f"Человек не числится в ScamBase.",
            parse_mode="HTML"
        )
    
    await state.clear()


@dp.callback_query(F.data == "stats")
async def show_stats(callback: CallbackQuery):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    cur.execute('SELECT COUNT(*) FROM scambase')
    total_scammers = cur.fetchone()[0]
    
    cur.execute('SELECT label, COUNT(*) FROM scambase GROUP BY label')
    labels_stats = cur.fetchall()
    
    cur.execute('SELECT COUNT(*) FROM reports WHERE status = "pending"')
    pending_reports = cur.fetchone()[0]
    
    cur.execute('SELECT COUNT(*) FROM users')
    total_users = cur.fetchone()[0]
    
    cur.execute('SELECT COUNT(*) FROM payments')
    total_payments = cur.fetchone()[0]
    
    conn.close()
    
    label_emoji = {"FAKE": "🎭", "SCAM": "⚠️", "WORKER": "🛠️"}
    
    stats_text = f"📊 <b>Статистика ScamBase</b> 📊\n\n"
    stats_text += f"👥 Всего в базе: <b>{total_scammers}</b>\n"
    stats_text += f"👤 Пользователей бота: <b>{total_users}</b>\n"
    stats_text += f"💰 Оплат звёздами: <b>{total_payments}</b>\n"
    stats_text += f"📋 Ожидающих заявок: <b>{pending_reports}</b>\n\n"
    stats_text += "<b>По меткам:</b>\n"
    
    for label, count in labels_stats:
        emoji = label_emoji.get(label, "📌")
        stats_text += f"{emoji} {label}: {count}\n"
    
    await callback.message.answer(stats_text, parse_mode="HTML")
    await callback.answer()


@dp.callback_query(F.data == "delete_self")
async def delete_self_prompt(callback: CallbackQuery):
    username = callback.from_user.username or f"user_{callback.from_user.id}"
    user_label = is_in_scambase(username)
    
    if not user_label:
        await callback.message.answer("✅ Вы не найдены в базе ScamBase. Удаление не требуется.")
        await callback.answer()
        return
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"⭐ Удалить за {STAR_PRICE} звёзд", callback_data="confirm_star_delete")],
        [InlineKeyboardButton(text="🔙 Отмена", callback_data="back_to_menu")]
    ])
    
    await callback.message.answer(
        f"⚠️ <b>Вы находитесь в ScamBase!</b> ⚠️\n\n"
        f"Ваша метка: {user_label}\n\n"
        f"Вы можете удалить себя из базы за <b>{STAR_PRICE} ⭐</b>\n\n"
        f"Нажмите на кнопку ниже, чтобы оплатить звёздами Telegram.\n\n"
        f"<i>После оплаты вы будете автоматически удалены из базы</i>",
        parse_mode="HTML",
        reply_markup=keyboard
    )
    await callback.answer()


@dp.callback_query(F.data == "confirm_star_delete")
async def process_star_delete(callback: CallbackQuery):
    user_id = callback.from_user.id
    
    try:
        await bot.send_invoice(
            chat_id=user_id,
            title="Удаление из ScamBase",
            description="Удаление вашего профиля из базы ScamBase",
            payload=f"delete_{user_id}_{datetime.now().timestamp()}",
            currency="XTR",
            prices=[LabeledPrice(label="Удаление из ScamBase", amount=STAR_PRICE)],
            need_name=False,
            need_phone_number=False,
            need_email=False
        )
        await callback.answer("💫 Отправлен счёт на оплату звёздами!")
    except Exception as e:
        logger.error(f"Ошибка отправки инвойса: {e}")
        await callback.message.answer(f"❌ Ошибка: {str(e)}\nПопробуйте позже.")
        await callback.answer()


@dp.pre_checkout_query()
async def pre_checkout_query(pre_checkout: types.PreCheckoutQuery):
    await pre_checkout.answer(ok=True)


@dp.message(F.successful_payment)
async def successful_payment(message: Message):
    user_id = message.from_user.id
    username = message.from_user.username or f"user_{user_id}"
    payment = message.successful_payment
    
    # Сохраняем информацию об оплате
    save_payment(user_id, username, payment.total_amount, payment.invoice_payload)
    
    # Удаляем пользователя из базы
    if remove_from_scambase(username):
        # Уведомляем админов об оплате
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(
                    admin_id,
                    f"💰 <b>Новая оплата звёздами!</b>\n\n"
                    f"👤 Пользователь: @{username}\n"
                    f"💸 Сумма: {payment.total_amount} ⭐\n"
                    f"📅 Дата: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                    f"✅ Статус: Удалён из базы",
                    parse_mode="HTML"
                )
            except:
                pass
        
        await message.answer(
            "✅ <b>Оплата получена! Вы удалены из ScamBase.</b> ✅\n\n"
            "Ваше имя больше не числится в базе скамеров.\n"
            "Спасибо за оплату звёздами! 🌟",
            parse_mode="HTML"
        )
    else:
        await message.answer(
            "❌ Ошибка: вас не было в базе данных.\n"
            "Возможно, вы уже были удалены ранее. Деньги будут возвращены?",
            parse_mode="HTML"
        )
    
    is_admin_user = message.from_user.id in ADMIN_IDS
    await message.answer("Главное меню:", reply_markup=main_menu_keyboard(is_admin_user))


# ================= ЗАЯВКИ НА СКАММЕРА =================
@dp.callback_query(F.data == "report")
async def report_start(callback: CallbackQuery, state: FSMContext):
    update_user_activity(callback.from_user.id)
    await callback.message.answer(
        "📝 <b>Подача заявки на скамера</b>\n\n"
        "Введите username человека, которого вы хотите добавить в базу (без @):\n\n"
        "⚠️ <i>Бот проверит, есть ли уже этот человек в базе</i>",
        parse_mode="HTML"
    )
    await callback.answer()
    await state.set_state(ReportStates.waiting_for_username)


@dp.message(ReportStates.waiting_for_username)
async def report_get_username(message: Message, state: FSMContext):
    username = normalize_username(message.text.strip())
    
    existing_label = is_in_scambase(username)
    if existing_label:
        await message.answer(
            f"⚠️ Человек @{username} <b>УЖЕ НАХОДИТСЯ</b> в ScamBase!\n\n"
            f"Его метка: {existing_label}\n\n"
            f"Вы можете проверить его через поиск.",
            parse_mode="HTML"
        )
        await state.clear()
        return
    
    await state.update_data(report_username=username)
    await message.answer(
        "📸 Теперь отправьте <b>фото-доказательства</b> (можно несколько фото)\n\n"
        "Отправьте 'готово', если фото нет:",
        parse_mode="HTML"
    )
    await state.set_state(ReportStates.waiting_for_proof_photos)


@dp.message(ReportStates.waiting_for_proof_photos)
async def report_get_photos(message: Message, state: FSMContext):
    photos = []
    
    if message.photo:
        photos = [message.photo[-1].file_id]
    elif message.text and message.text.lower() == 'готово':
        pass
    elif message.text:
        await message.answer("Пожалуйста, отправьте фото или напишите 'готово'")
        return
    
    await state.update_data(report_photos=photos)
    
    await message.answer(
        "🎥 Теперь отправьте <b>видео-доказательства</b> (можно несколько видео)\n\n"
        "Отправьте 'готово', если видео нет:",
        parse_mode="HTML"
    )
    await state.set_state(ReportStates.waiting_for_proof_videos)


@dp.message(ReportStates.waiting_for_proof_videos)
async def report_get_videos(message: Message, state: FSMContext):
    videos = []
    
    if message.video:
        videos = [message.video.file_id]
    elif message.text and message.text.lower() == 'готово':
        pass
    elif message.text:
        await message.answer("Пожалуйста, отправьте видео или напишите 'готово'")
        return
    
    data = await state.get_data()
    username = data.get('report_username')
    photos = data.get('report_photos', [])
    
    add_report(username, message.from_user.id, photos, videos)
    
    # Уведомляем админов о новой заявке
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id,
                f"📋 <b>НОВАЯ ЗАЯВКА В SCAMBASE!</b>\n\n"
                f"👤 Пользователь: @{username}\n"
                f"📝 Подал: {message.from_user.full_name} (ID: {message.from_user.id})\n"
                f"📸 Фото: {len(photos)} шт.\n"
                f"🎥 Видео: {len(videos)} шт.\n\n"
                f"Используйте админ-панель для рассмотрения заявки.",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Ошибка уведомления админа: {e}")
    
    await message.answer(
        "✅ <b>Заявка успешно отправлена!</b> ✅\n\n"
        f"👤 Человек: @{username}\n"
        f"📸 Доказательств: {len(photos)} фото, {len(videos)} видео\n\n"
        "Администраторы рассмотрят вашу заявку в ближайшее время.\n"
        "Спасибо за помощь в борьбе со скамерами! 🙏",
        parse_mode="HTML"
    )
    
    is_admin_user = message.from_user.id in ADMIN_IDS
    await message.answer("Главное меню:", reply_markup=main_menu_keyboard(is_admin_user))
    await state.clear()


# ================= АДМИН-ПАНЕЛЬ =================
def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


@dp.callback_query(F.data == "admin_panel")
async def admin_panel(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён! Вы не администратор.", show_alert=True)
        return
    
    await callback.message.answer(
        "👑 <b>Панель администратора ScamBase</b> 👑\n\n"
        "Выберите действие:",
        parse_mode="HTML",
        reply_markup=admin_panel_keyboard()
    )
    await callback.answer()


@dp.callback_query(F.data == "admin_stats")
async def admin_bot_stats(callback: CallbackQuery):
    """Показать подробную статистику бота для админов"""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён!", show_alert=True)
        return
    
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    cur.execute('SELECT COUNT(*) FROM users')
    total_users = cur.fetchone()[0]
    
    cur.execute('SELECT COUNT(*) FROM scambase')
    total_scammers = cur.fetchone()[0]
    
    cur.execute('SELECT COUNT(*) FROM reports')
    total_reports = cur.fetchone()[0]
    
    cur.execute('SELECT COUNT(*) FROM reports WHERE status = "pending"')
    pending_reports = cur.fetchone()[0]
    
    cur.execute('SELECT COUNT(*) FROM payments')
    total_payments = cur.fetchone()[0]
    
    cur.execute('SELECT SUM(amount) FROM payments')
    total_stars = cur.fetchone()[0] or 0
    
    cur.execute('SELECT COUNT(*) FROM users WHERE julianday("now") - julianday(last_activity) < 7')
    active_users = cur.fetchone()[0]
    
    conn.close()
    
    stats_text = f"📊 <b>Детальная статистика бота</b> 📊\n\n"
    stats_text += f"👥 Всего пользователей бота: <b>{total_users}</b>\n"
    stats_text += f"🟢 Активных (за 7 дней): <b>{active_users}</b>\n"
    stats_text += f"⚠️ В базе скамеров: <b>{total_scammers}</b>\n"
    stats_text += f"📋 Всего заявок: <b>{total_reports}</b>\n"
    stats_text += f"⏳ Ожидают рассмотрения: <b>{pending_reports}</b>\n"
    stats_text += f"💰 Всего оплат: <b>{total_payments}</b>\n"
    stats_text += f"⭐ Собрано звёзд: <b>{total_stars}</b>\n"
    
    await callback.message.answer(stats_text, parse_mode="HTML")
    await callback.answer()


@dp.callback_query(F.data == "admin_list")
async def admin_show_list(callback: CallbackQuery):
    """Показать список всех в базе (админ)"""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён!", show_alert=True)
        return
    
    users = list_all_users()
    
    if not users:
        await callback.message.answer("📭 База данных пуста.")
        await callback.answer()
        return
    
    label_emoji = {"FAKE": "🎭", "SCAM": "⚠️", "WORKER": "🛠️"}
    
    text = "📋 <b>Список всех в базе:</b>\n\n"
    for username, label, date in users[:20]:
        emoji = label_emoji.get(label, "📌")
        text += f"{emoji} @{username} — {label}\n"
        text += f"   📅 {date[:16]}\n\n"
    
    if len(users) > 20:
        text += f"\n... и ещё {len(users) - 20} записей"
    
    await callback.message.answer(text, parse_mode="HTML")
    await callback.answer()


# ================= ДОБАВЛЕНИЕ В БАЗУ (АДМИН) =================
@dp.callback_query(F.data == "admin_add")
async def admin_add_prompt(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён!", show_alert=True)
        return
    
    await callback.message.answer(
        "➕ <b>Добавление в базу</b>\n\n"
        "Введите username человека для добавления (без @):",
        parse_mode="HTML"
    )
    await callback.answer()
    await state.set_state(AdminAddStates.waiting_for_username)


@dp.message(AdminAddStates.waiting_for_username)
async def admin_add_get_username(message: Message, state: FSMContext):
    username = normalize_username(message.text.strip())
    
    existing = is_in_scambase(username)
    if existing:
        await message.answer(
            f"⚠️ Пользователь @{username} <b>УЖЕ ЕСТЬ</b> в базе!\n\n"
            f"Текущая метка: {existing}\n\n"
            f"Используйте 'Выдать метку' для изменения.",
            parse_mode="HTML"
        )
        await state.clear()
        return
    
    await state.update_data(add_username=username)
    await message.answer(
        f"🏷️ Выберите метку для @{username}:",
        reply_markup=label_keyboard(username, "add")
    )
    await state.clear()


@dp.callback_query(F.data.startswith("add_label_"))
async def admin_add_with_label(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён!", show_alert=True)
        return
    
    parts = callback.data.split("_")
    username = parts[2]
    label = parts[3]
    
    existing = is_in_scambase(username)
    if existing:
        await callback.message.answer(
            f"⚠️ Пользователь @{username} <b>УЖЕ ЕСТЬ</b> в базе!\n"
            f"Текущая метка: {existing}",
            parse_mode="HTML"
        )
        await callback.answer()
        return
    
    if add_to_scambase(username, label, callback.from_user.id):
        emoji = {"FAKE": "🎭", "SCAM": "⚠️", "WORKER": "🛠️"}.get(label, "📌")
        await callback.message.answer(
            f"✅ <b>Пользователь добавлен в ScamBase!</b>\n\n"
            f"👤 Username: @{username}\n"
            f"🏷️ Метка: {emoji} {label}\n"
            f"👮 Добавил: {callback.from_user.full_name}\n\n"
            f"🔍 <i>Теперь вы можете найти его через поиск</i>",
            parse_mode="HTML"
        )
    else:
        await callback.message.answer(f"❌ Ошибка при добавлении пользователя @{username}.")
    
    await callback.answer()
    await callback.message.answer("👑 Админ-панель:", reply_markup=admin_panel_keyboard())


# ================= УДАЛЕНИЕ ИЗ БАЗЫ (АДМИН) =================
@dp.callback_query(F.data == "admin_remove")
async def admin_remove_prompt(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён!", show_alert=True)
        return
    
    await callback.message.answer(
        "❌ <b>Удаление из базы</b>\n\n"
        "Введите username человека для удаления (без @):",
        parse_mode="HTML"
    )
    await callback.answer()
    await state.set_state(AdminRemoveStates.waiting_for_username)


@dp.message(AdminRemoveStates.waiting_for_username)
async def admin_remove_user(message: Message, state: FSMContext):
    username = normalize_username(message.text.strip())
    
    existing = is_in_scambase(username)
    if not existing:
        await message.answer(
            f"❌ Пользователь @{username} <b>НЕ НАЙДЕН</b> в базе ScamBase!\n\n"
            f"Проверьте правильность ввода username.",
            parse_mode="HTML"
        )
        await state.clear()
        return
    
    if remove_from_scambase(username):
        await message.answer(
            f"✅ <b>Пользователь удалён из ScamBase!</b>\n\n"
            f"👤 Username: @{username}\n"
            f"📌 Старая метка: {existing}\n"
            f"👮 Удалил: {message.from_user.full_name}",
            parse_mode="HTML"
        )
    else:
        await message.answer(f"❌ Ошибка при удалении пользователя @{username}.")
    
    await state.clear()
    await message.answer("👑 Админ-панель:", reply_markup=admin_panel_keyboard())


# ================= ВЫДАЧА МЕТКИ (АДМИН) =================
@dp.callback_query(F.data == "admin_label")
async def admin_label_prompt(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён!", show_alert=True)
        return
    
    await callback.message.answer(
        "🏷️ <b>Выдача метки</b>\n\n"
        "Введите username человека для изменения метки (без @):",
        parse_mode="HTML"
    )
    await callback.answer()
    await state.set_state(AdminLabelStates.waiting_for_username)


@dp.message(AdminLabelStates.waiting_for_username)
async def admin_label_get_username(message: Message, state: FSMContext):
    username = normalize_username(message.text.strip())
    
    existing = is_in_scambase(username)
    if not existing:
        await message.answer(
            f"❌ Пользователь @{username} <b>НЕ НАЙДЕН</b> в базе ScamBase!\n\n"
            f"Сначала добавьте пользователя через 'Добавить в базу'.",
            parse_mode="HTML"
        )
        await state.clear()
        return
    
    await state.update_data(label_username=username)
    await message.answer(
        f"🏷️ <b>Изменение метки для @{username}</b>\n\n"
        f"Текущая метка: {existing}\n\n"
        f"Выберите новую метку:",
        parse_mode="HTML",
        reply_markup=label_keyboard(username, "change")
    )
    await state.clear()


@dp.callback_query(F.data.startswith("change_label_"))
async def admin_change_label(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён!", show_alert=True)
        return
    
    parts = callback.data.split("_")
    username = parts[2]
    new_label = parts[3]
    
    emoji = {"FAKE": "🎭", "SCAM": "⚠️", "WORKER": "🛠️"}.get(new_label, "📌")
    
    if update_label(username, new_label):
        await callback.message.answer(
            f"✅ <b>Метка изменена!</b>\n\n"
            f"👤 Username: @{username}\n"
            f"🏷️ Новая метка: {emoji} {new_label}",
            parse_mode="HTML"
        )
    else:
        await callback.message.answer(f"❌ Ошибка: пользователь @{username} не найден.")
    
    await callback.answer()
    await callback.message.answer("👑 Админ-панель:", reply_markup=admin_panel_keyboard())


# ================= ЗАЯВКИ ОТ ПОЛЬЗОВАТЕЛЕЙ =================
@dp.callback_query(F.data == "admin_reports")
async def admin_show_reports(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён!", show_alert=True)
        return
    
    reports = get_pending_reports()
    
    if not reports:
        await callback.message.answer("📭 Нет ожидающих заявок.")
        await callback.answer()
        return
    
    for report in reports:
        report_id, username, reported_by, photos, videos, date = report
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="🎭 FAKE", callback_data=f"approve_{report_id}_{username}_FAKE"),
                InlineKeyboardButton(text="⚠️ SCAM", callback_data=f"approve_{report_id}_{username}_SCAM"),
                InlineKeyboardButton(text="🛠️ WORKER", callback_data=f"approve_{report_id}_{username}_WORKER")
            ],
            [InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject_{report_id}")]
        ])
        
        text = f"📋 <b>Заявка #{report_id}</b>\n"
        text += f"👤 Username: @{username}\n"
        text += f"👮 Подал: ID {reported_by}\n"
        text += f"📅 Дата: {date[:19]}\n"
        text += f"📸 Фото: {len(photos.split(',')) if photos else 0}\n"
        text += f"🎥 Видео: {len(videos.split(',')) if videos else 0}\n"
        
        await callback.message.answer(text, parse_mode="HTML", reply_markup=keyboard)
    
    await callback.answer()


@dp.callback_query(F.data.startswith("approve_"))
async def admin_approve_report(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён!", show_alert=True)
        return
    
    parts = callback.data.split("_")
    report_id = int(parts[1])
    username = parts[2]
    label = parts[3]
    
    approve_report(report_id, username, label, callback.from_user.id)
    
    emoji = {"FAKE": "🎭", "SCAM": "⚠️", "WORKER": "🛠️"}.get(label, "📌")
    
    await callback.message.answer(
        f"✅ <b>Заявка #{report_id} одобрена!</b>\n\n"
        f"👤 @{username} добавлен в базу\n"
        f"🏷️ Метка: {emoji} {label}",
        parse_mode="HTML"
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("reject_"))
async def admin_reject_report(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён!", show_alert=True)
        return
    
    report_id = int(callback.data.split("_")[1])
    reject_report(report_id)
    
    await callback.message.answer(f"❌ Заявка #{report_id} отклонена.")
    await callback.answer()


# ================= РАССЫЛКА ДЛЯ АДМИНОВ =================
@dp.callback_query(F.data == "admin_mailing")
async def admin_mailing_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён!", show_alert=True)
        return
    
    await callback.message.answer(
        "📢 <b>Рассылка сообщений</b>\n\n"
        "Отправьте текст сообщения для рассылки всем пользователям бота.\n\n"
        "<i>Поддерживается форматирование HTML.</i>\n"
        "<i>Можно отправлять фото, видео, документы.</i>\n\n"
        "Для отмены отправьте /cancel",
        parse_mode="HTML"
    )
    await callback.answer()
    await state.set_state(MailingStates.waiting_for_message)


@dp.message(MailingStates.waiting_for_message)
async def admin_mailing_get_message(message: Message, state: FSMContext):
    if message.text == "/cancel":
        await message.answer("❌ Рассылка отменена.")
        await state.clear()
        return
    
    # Сохраняем сообщение
    await state.update_data(mailing_message=message)
    
    # Получаем количество пользователей
    users = get_all_users()
    user_count = len(users)
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, отправить", callback_data="mailing_confirm")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="mailing_cancel")]
    ])
    
    await message.answer(
        f"📢 <b>Предпросмотр рассылки</b>\n\n"
        f"Сообщение будет отправлено <b>{user_count}</b> пользователям.\n\n"
        f"<i>Вот как увидят ваше сообщение:</i>\n"
        f"{'-'*30}\n",
        parse_mode="HTML"
    )
    
    # Пересылаем сообщение как предпросмотр
    await message.forward(message.chat.id)
    
    await message.answer(
        f"\n{'-'*30}\n\n"
        f"Отправить рассылку?",
        reply_markup=keyboard
    )
    await state.set_state(MailingStates.waiting_for_confirmation)


@dp.callback_query(F.data == "mailing_confirm")
async def admin_mailing_confirm(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён!", show_alert=True)
        return
    
    data = await state.get_data()
    original_message = data.get('mailing_message')
    
    if not original_message:
        await callback.message.answer("❌ Ошибка: сообщение не найдено.")
        await state.clear()
        return
    
    users = get_all_users()
    sent = 0
    failed = 0
    
    status_message = await callback.message.answer("📤 Начинаю рассылку...")
    
    for user_id, username, first_name in users:
        try:
            if original_message.text:
                await bot.send_message(
                    user_id,
                    original_message.text,
                    parse_mode="HTML"
                )
            elif original_message.photo:
                await bot.send_photo(
                    user_id,
                    original_message.photo[-1].file_id,
                    caption=original_message.caption
                )
            elif original_message.video:
                await bot.send_video(
                    user_id,
                    original_message.video.file_id,
                    caption=original_message.caption
                )
            sent += 1
        except Exception as e:
            failed += 1
            logger.error(f"Ошибка отправки пользователю {user_id}: {e}")
        
        # Небольшая задержка, чтобы не спамить
        await asyncio.sleep(0.05)
    
    await status_message.edit_text(
        f"✅ <b>Рассылка завершена!</b>\n\n"
        f"📨 Отправлено: <b>{sent}</b>\n"
        f"❌ Ошибок: <b>{failed}</b>\n"
        f"👥 Всего пользователей: <b>{len(users)}</b>",
        parse_mode="HTML"
    )
    
    await state.clear()


@dp.callback_query(F.data == "mailing_cancel")
async def admin_mailing_cancel(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён!", show_alert=True)
        return
    
    await callback.message.answer("❌ Рассылка отменена.")
    await callback.answer()
    await state.clear()


@dp.callback_query(F.data == "back_to_menu")
async def back_to_menu(callback: CallbackQuery):
    is_admin_user = callback.from_user.id in ADMIN_IDS
    await callback.message.answer("Главное меню:", reply_markup=main_menu_keyboard(is_admin_user))
    await callback.answer()


# ================= ЗАПУСК =================
async def main():
    init_db()
    logger.info("✅ Бот ScamBase запущен!")
    logger.info(f"👑 Администраторы: {ADMIN_IDS}")
    logger.info(f"🏷️ Доступные метки: FAKE, SCAM, WORKER")
    logger.info(f"💾 База данных: {DB_PATH}")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
