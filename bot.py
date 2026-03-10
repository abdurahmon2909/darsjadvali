import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import os
import json

from dotenv import load_dotenv
load_dotenv()

from aiogram import Bot, Dispatcher, types, F
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.exceptions import TelegramBadRequest

import gspread
from oauth2client.service_account import ServiceAccountCredentials

# --------------------------------------------------
# TIMEZONE
# --------------------------------------------------
TZ = ZoneInfo("Asia/Tashkent")

# --------------------------------------------------
# TELEGRAM
# --------------------------------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN o'zgaruvchisi topilmadi")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# --------------------------------------------------
# GOOGLE SHEETS
# --------------------------------------------------
creds_json = os.getenv("GOOGLE_CREDS")
if not creds_json:
    raise ValueError("GOOGLE_CREDS o'zgaruvchisi topilmadi")

creds_dict = json.loads(creds_json)

scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive"
]

credentials = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
gc = gspread.authorize(credentials)

SHEET_ID = "1xV9pYkVgBvb0565ZUqe71OMqtrVsYSfo-TlxiaxhQtE"

spreadsheet = gc.open_by_key(SHEET_ID)
schedule_sheet = spreadsheet.worksheet("Schedule")
users_sheet = spreadsheet.worksheet("Users")
admins_sheet = spreadsheet.worksheet("Admins")
feedback_sheet = spreadsheet.worksheet("Feedback")

# --------------------------------------------------
# CACHE / STATE
# --------------------------------------------------
schedule_cache = {}
users_cache = {}
admins_cache = {}

admin_broadcast_state = {}
feedback_state = {}
registration_state = {}

FEEDBACK_STATE_FILE = "feedback_state.json"

# --------------------------------------------------
# DAYS
# --------------------------------------------------
DAY_MAP = {
    "Monday": "Dushanba",
    "Tuesday": "Seshanba",
    "Wednesday": "Chorshanba",
    "Thursday": "Payshanba",
    "Friday": "Juma",
    "Saturday": "Shanba",
    "Sunday": "Yakshanba",
}

ORDERED_DAYS = [
    "Dushanba",
    "Seshanba",
    "Chorshanba",
    "Payshanba",
    "Juma",
    "Shanba",
    "Yakshanba",
]

# --------------------------------------------------
# TIME HELPERS
# --------------------------------------------------
def now_tashkent():
    return datetime.now(TZ)


def today_date_str():
    return now_tashkent().strftime("%Y-%m-%d")


def current_time_str():
    return now_tashkent().strftime("%H:%M")


def get_today_day_uz() -> str:
    today_en = now_tashkent().strftime("%A")
    return DAY_MAP.get(today_en, today_en)


def get_tomorrow_day_uz() -> str:
    tomorrow_en = (now_tashkent() + timedelta(days=1)).strftime("%A")
    return DAY_MAP.get(tomorrow_en, tomorrow_en)

# --------------------------------------------------
# USERS HELPERS
# --------------------------------------------------
def ensure_users_header():
    rows = users_sheet.get_all_values()

    if not rows:
        users_sheet.append_row(["chat_id", "class"])
        return

    first_row = rows[0]
    if len(first_row) < 2 or first_row[0].strip().lower() != "chat_id" or first_row[1].strip().lower() != "class":
        users_sheet.insert_row(["chat_id", "class"], 1)


def load_users_to_cache():
    global users_cache

    rows = users_sheet.get_all_values()
    new_cache = {}

    if len(rows) > 1:
        for row in rows[1:]:
            if len(row) >= 2:
                chat_id = row[0].strip()
                user_class = row[1].strip().upper()

                if chat_id and user_class:
                    new_cache[chat_id] = {
                        "chat_id": chat_id,
                        "class": user_class
                    }

    users_cache = new_cache
    print("Users cache yangilandi")


def get_user_class(chat_id: int):
    user = users_cache.get(str(chat_id))
    if not user:
        return None
    return user["class"]


def save_user_class(chat_id: int, user_class: str):
    rows = users_sheet.get_all_values()

    if not rows:
        users_sheet.append_row(["chat_id", "class"])
        rows = users_sheet.get_all_values()

    updated = False

    for index, row in enumerate(rows[1:], start=2):
        if len(row) >= 1 and row[0].strip() == str(chat_id):
            users_sheet.update(f"A{index}:B{index}", [[str(chat_id), user_class]])
            updated = True
            break

    if not updated:
        users_sheet.append_row([str(chat_id), user_class])

    users_cache[str(chat_id)] = {
        "chat_id": str(chat_id),
        "class": user_class.upper()
    }


def get_all_users():
    return list(users_cache.values())

# --------------------------------------------------
# ADMINS HELPERS
# --------------------------------------------------
def load_admins_to_cache():
    global admins_cache

    rows = admins_sheet.get_all_values()
    new_cache = {}

    if len(rows) > 1:
        for row in rows[1:]:
            if len(row) < 2:
                continue

            chat_id = row[0].strip()
            role = row[1].strip().lower()
            classes = row[2].strip() if len(row) >= 3 else ""

            if chat_id:
                new_cache[chat_id] = {
                    "chat_id": chat_id,
                    "role": role,
                    "classes": classes
                }

    admins_cache = new_cache
    print("Admins cache yangilandi")


def get_admins_data():
    return list(admins_cache.values())


def get_admin_info(chat_id: int):
    return admins_cache.get(str(chat_id))


def is_superadmin(chat_id: int) -> bool:
    admin = get_admin_info(chat_id)
    return bool(admin and admin["role"] == "superadmin")


def is_any_admin(chat_id: int) -> bool:
    return get_admin_info(chat_id) is not None


def get_admin_allowed_classes(chat_id: int):
    admin = get_admin_info(chat_id)
    if not admin:
        return []

    if admin["role"] == "superadmin":
        return ["ALL"]

    classes_raw = admin.get("classes", "").strip()
    if not classes_raw:
        return []

    return [c.strip().upper() for c in classes_raw.split(",") if c.strip()]

# --------------------------------------------------
# SCHEDULE CACHE HELPERS
# --------------------------------------------------
def load_schedule_to_cache():
    global schedule_cache

    all_rows = schedule_sheet.get_all_values()
    if not all_rows:
        schedule_cache = {}
        return

    headers = all_rows[0][2:]
    new_cache = {}

    for row in all_rows[1:]:
        if len(row) < 2:
            continue

        row_class = row[0].strip().upper()
        day = row[1].strip()

        if not row_class or not day:
            continue

        if row_class not in new_cache:
            new_cache[row_class] = {}

        lessons = []
        for i, subject in enumerate(row[2:]):
            subject = subject.strip()
            if subject:
                lesson_time = headers[i].strip() if i < len(headers) else f"Dars {i + 1}"
                lessons.append((lesson_time, subject))

        new_cache[row_class][day] = lessons

    schedule_cache = new_cache
    print("Schedule cache yangilandi")


def get_schedule_for_class(user_class: str):
    return schedule_cache.get(user_class.upper(), {})


def class_exists_in_schedule(user_class: str) -> bool:
    return user_class.upper() in schedule_cache


def get_existing_classes():
    return sorted(schedule_cache.keys())


def get_parallel_numbers():
    nums = set()
    for class_name in schedule_cache.keys():
        digits = ""
        for ch in class_name:
            if ch.isdigit():
                digits += ch
            else:
                break
        if digits:
            nums.add(digits)
    return sorted(nums, key=lambda x: int(x))


def get_letters_for_parallel(parallel: str):
    letters = []
    prefix = str(parallel)
    for class_name in sorted(schedule_cache.keys()):
        if class_name.startswith(prefix):
            suffix = class_name[len(prefix):]
            if suffix and suffix not in letters:
                letters.append(suffix)
    return letters

# --------------------------------------------------
# FEEDBACK HELPERS
# --------------------------------------------------
def get_unique_subjects_for_today(user_class: str):
    today = get_today_day_uz()
    schedule = get_schedule_for_class(user_class)
    lessons = schedule.get(today, [])

    subjects = []
    for _, subject in lessons:
        subject = subject.strip()
        if subject and subject not in subjects:
            subjects.append(subject)

    return subjects


def save_feedback(chat_id, user_class, best, worst):
    date = now_tashkent().strftime("%Y-%m-%d")
    feedback_sheet.append_row([
        date,
        str(chat_id),
        user_class,
        best,
        worst
    ])

# --------------------------------------------------
# FEEDBACK STATE PERSISTENCE
# --------------------------------------------------
def load_feedback_state():
    global feedback_state

    if not os.path.exists(FEEDBACK_STATE_FILE):
        feedback_state = {}
        return

    try:
        with open(FEEDBACK_STATE_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)

        restored = {}
        for chat_id, state in raw.items():
            restored[str(chat_id)] = state

        feedback_state = restored
        print("Feedback state yuklandi")
    except Exception as e:
        print(f"Feedback state yuklashda xatolik: {e}")
        feedback_state = {}


def save_feedback_state():
    try:
        with open(FEEDBACK_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(feedback_state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Feedback state saqlashda xatolik: {e}")


def set_feedback_state(chat_id, state_data):
    feedback_state[str(chat_id)] = state_data
    save_feedback_state()


def get_feedback_state(chat_id):
    return feedback_state.get(str(chat_id))


def remove_feedback_state(chat_id):
    feedback_state.pop(str(chat_id), None)
    save_feedback_state()

# --------------------------------------------------
# FORMAT HELPERS
# --------------------------------------------------
def format_schedule_for_day(user_class: str, day_uz: str) -> str:
    schedule = get_schedule_for_class(user_class)

    if not schedule:
        return f"{user_class} sinfi uchun dars jadvali topilmadi."

    lessons = schedule.get(day_uz, [])

    if not lessons:
        if day_uz == get_today_day_uz():
            return f"{user_class} sinfi uchun bugun dars yo‘q."
        if day_uz == get_tomorrow_day_uz():
            return f"{user_class} sinfi uchun ertaga darslar kiritilmagan."
        return f"{user_class} sinfi uchun {day_uz} kuni dars yo‘q."

    response = f"📘 {user_class} sinfi uchun {day_uz} kungi dars jadvali:\n\n"

    for index, (lesson_time, subject) in enumerate(lessons, start=1):
        response += f"{index}) {lesson_time} — {subject}\n"

    return response.strip()


def format_weekly_schedule(user_class: str) -> str:
    schedule = get_schedule_for_class(user_class)

    if not schedule:
        return f"{user_class} sinfi uchun haftalik dars jadvali topilmadi."

    response_parts = [f"📚 {user_class} sinfi uchun haftalik dars jadvali:\n"]
    has_any_lessons = False

    for day in ORDERED_DAYS:
        lessons = schedule.get(day, [])

        if not lessons:
            continue

        has_any_lessons = True
        response_parts.append(f"\n🔹 {day}")
        for index, (lesson_time, subject) in enumerate(lessons, start=1):
            response_parts.append(f"{index}) {lesson_time} — {subject}")

    if not has_any_lessons:
        return f"{user_class} sinfi uchun haftalik dars jadvali kiritilmagan."

    return "\n".join(response_parts).strip()

# --------------------------------------------------
# INLINE KEYBOARDS
# --------------------------------------------------
def kb_main_inline():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📘 Bugungi dars jadvali", callback_data="menu_today")],
            [InlineKeyboardButton(text="📗 Ertangi dars jadvali", callback_data="menu_tomorrow")],
            [InlineKeyboardButton(text="📚 Haftalik dars jadvali", callback_data="menu_weekly")],
        ]
    )


def kb_registration_numbers():
    numbers = get_parallel_numbers()
    rows = []

    row = []
    for num in numbers:
        row.append(InlineKeyboardButton(text=num, callback_data=f"reg_num:{num}"))
        if len(row) == 4:
            rows.append(row)
            row = []

    if row:
        rows.append(row)

    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_registration_letters(parallel: str):
    letters = get_letters_for_parallel(parallel)
    rows = []

    row = []
    for letter in letters:
        full_class = f"{parallel}{letter}"
        row.append(InlineKeyboardButton(text=letter, callback_data=f"reg_class:{full_class}"))
        if len(row) == 4:
            rows.append(row)
            row = []

    if row:
        rows.append(row)

    rows.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="reg_back_numbers")])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_subjects_inline(subjects):
    rows = []
    for i, subject in enumerate(subjects):
        rows.append([
            InlineKeyboardButton(text=subject, callback_data=f"fb_subject:{i}")
        ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_admin_classes_select(available_classes, selected_classes):
    available_classes = sorted(set(available_classes))
    selected_classes = sorted(set(selected_classes))

    rows = []
    row = []

    for class_name in available_classes:
        mark = "✅ " if class_name in selected_classes else ""
        row.append(
            InlineKeyboardButton(
                text=f"{mark}{class_name}",
                callback_data=f"admin_toggle_class:{class_name}"
            )
        )
        if len(row) == 3:
            rows.append(row)
            row = []

    if row:
        rows.append(row)

    rows.append([InlineKeyboardButton(text="✅ Tayyor", callback_data="admin_classes_done")])
    rows.append([InlineKeyboardButton(text="❌ Bekor qilish", callback_data="admin_cancel")])

    return InlineKeyboardMarkup(inline_keyboard=rows)

# --------------------------------------------------
# SAFE EDIT / SEND HELPERS
# --------------------------------------------------
async def edit_or_send_message(target, text, reply_markup=None):
    try:
        if isinstance(target, types.CallbackQuery):
            await target.message.edit_text(text, reply_markup=reply_markup)
        else:
            await target.answer(text, reply_markup=reply_markup)
    except TelegramBadRequest:
        if isinstance(target, types.CallbackQuery):
            await target.message.answer(text, reply_markup=reply_markup)
        else:
            await target.answer(text, reply_markup=reply_markup)

# --------------------------------------------------
# BACKGROUND CACHE REFRESH
# --------------------------------------------------
async def refresh_schedule_cache_every_hour():
    while True:
        try:
            load_schedule_to_cache()
        except Exception as e:
            print(f"Schedule cache yangilashda xatolik: {e}")

        await asyncio.sleep(3600)


async def refresh_users_and_admins_cache_every_5_minutes():
    while True:
        try:
            load_users_to_cache()
            load_admins_to_cache()
        except Exception as e:
            print(f"Users/Admins cache yangilashda xatolik: {e}")

        await asyncio.sleep(300)

# --------------------------------------------------
# RUN TASKS ONCE
# --------------------------------------------------
async def run_today_schedule():
    users = get_all_users()
    today_day_uz = get_today_day_uz()

    for user in users:
        chat_id = user["chat_id"]
        user_class = user["class"]

        try:
            response = format_schedule_for_day(user_class, today_day_uz)
            await bot.send_message(chat_id, response, reply_markup=kb_main_inline())
            await asyncio.sleep(0.05)
        except Exception as e:
            print(f"Bugungi jadval yuborishda xatolik {chat_id}: {e}")


async def run_feedback_poll():
    users = get_all_users()
    today = today_date_str()

    for user in users:
        chat_id = user["chat_id"]
        user_class = user["class"]

        subjects = get_unique_subjects_for_today(user_class)

        if not subjects:
            continue

        set_feedback_state(chat_id, {
            "step": "best",
            "class": user_class,
            "subjects": subjects,
            "poll_date": today,
            "reminded_18": False
        })

        try:
            await bot.send_message(
                chat_id,
                "Bugun qaysi fan sizga eng yoqqan bo‘ldi?",
                reply_markup=kb_subjects_inline(subjects)
            )
            await asyncio.sleep(0.05)
        except Exception as e:
            print(f"Feedback poll yuborishda xatolik {chat_id}: {e}")


async def run_feedback_reminder():
    today = today_date_str()

    for chat_id_str, state in list(feedback_state.items()):
        try:
            if state.get("poll_date") != today:
                continue

            if state.get("reminded_18") is True:
                continue

            subjects = state.get("subjects", [])
            step = state.get("step", "best")

            if not subjects:
                continue

            if step == "best":
                text = (
                    "⏰ Eslatma:\n"
                    "Bugungi so‘rovnomaga hali javob bermadingiz.\n\n"
                    "Bugun qaysi fan sizga eng yoqqan bo‘ldi?"
                )
            else:
                text = (
                    "⏰ Eslatma:\n"
                    "So‘rovnomaning ikkinchi qismiga hali javob bermadingiz.\n\n"
                    "Bugun qaysi fan eng qiyin yoki yoqmagan bo‘ldi?"
                )

            await bot.send_message(
                chat_id_str,
                text,
                reply_markup=kb_subjects_inline(subjects)
            )

            state["reminded_18"] = True
            set_feedback_state(chat_id_str, state)

            await asyncio.sleep(0.05)

        except Exception as e:
            print(f"Feedback reminder yuborishda xatolik {chat_id_str}: {e}")


async def close_expired_feedback_polls():
    today = today_date_str()
    to_remove = []

    for chat_id_str, state in list(feedback_state.items()):
        try:
            poll_date = state.get("poll_date")

            if poll_date != today:
                try:
                    await bot.send_message(
                        chat_id_str,
                        "🕛 Kechagi so‘rovnoma yopildi.",
                        reply_markup=kb_main_inline()
                    )
                    await asyncio.sleep(0.05)
                except Exception as e:
                    print(f"Feedback close notify xatolik {chat_id_str}: {e}")

                to_remove.append(chat_id_str)

        except Exception as e:
            print(f"Feedback close check xatolik {chat_id_str}: {e}")

    for chat_id_str in to_remove:
        feedback_state.pop(str(chat_id_str), None)

    if to_remove:
        save_feedback_state()


async def run_tomorrow_schedule():
    users = get_all_users()
    tomorrow_day_uz = get_tomorrow_day_uz()

    for user in users:
        chat_id = user["chat_id"]
        user_class = user["class"]

        try:
            response = format_schedule_for_day(user_class, tomorrow_day_uz)
            await bot.send_message(chat_id, response, reply_markup=kb_main_inline())
            await asyncio.sleep(0.05)
        except Exception as e:
            print(f"Ertangi jadval yuborishda xatolik {chat_id}: {e}")

# --------------------------------------------------
# STABLE SCHEDULER
# --------------------------------------------------
async def scheduler_loop():
    last_run = {
        "00": None,
        "07": None,
        "14": None,
        "18": None,
        "20": None
    }

    while True:
        try:
            now = now_tashkent()
            hour = now.strftime("%H")
            minute = now.strftime("%M")
            today = now.strftime("%Y-%m-%d")

            if hour == "00" and minute == "00":
                if last_run["00"] != today:
                    print("00:00 feedback close ishga tushdi")
                    await close_expired_feedback_polls()
                    last_run["00"] = today

            if hour == "07" and minute == "00":
                if last_run["07"] != today:
                    print("07:00 task ishga tushdi")
                    await run_today_schedule()
                    last_run["07"] = today

            if hour == "14" and minute == "00":
                if last_run["14"] != today:
                    print("14:00 task ishga tushdi")
                    await run_feedback_poll()
                    last_run["14"] = today

            if hour == "18" and minute == "00":
                if last_run["18"] != today:
                    print("18:00 feedback reminder ishga tushdi")
                    await run_feedback_reminder()
                    last_run["18"] = today

            if hour == "20" and minute == "00":
                if last_run["20"] != today:
                    print("20:00 task ishga tushdi")
                    await run_tomorrow_schedule()
                    last_run["20"] = today

        except Exception as e:
            print(f"Scheduler loop xatolik: {e}")

        await asyncio.sleep(20)

# --------------------------------------------------
# SEND HELPERS
# --------------------------------------------------
async def broadcast_to_all_users(text: str):
    users = get_all_users()
    sent_count = 0

    for user in users:
        try:
            await bot.send_message(
                user["chat_id"],
                f"📢 E'lon:\n\n{text}",
                reply_markup=kb_main_inline()
            )
            sent_count += 1
            await asyncio.sleep(0.05)
        except Exception as e:
            print(f"Broadcast all xatolik {user['chat_id']}: {e}")

    return sent_count


async def broadcast_to_classes(target_classes, text: str):
    users = get_all_users()
    sent_count = 0

    target_classes = [c.upper() for c in target_classes]

    for user in users:
        user_class = user["class"]

        if user_class in target_classes:
            try:
                await bot.send_message(
                    user["chat_id"],
                    f"📢 {', '.join(target_classes)} sinflari uchun e'lon:\n\n{text}",
                    reply_markup=kb_main_inline()
                )
                sent_count += 1
                await asyncio.sleep(0.05)
            except Exception as e:
                print(f"Broadcast xatolik {user['chat_id']}: {e}")

    return sent_count

# --------------------------------------------------
# HANDLERS: START / REGISTRATION
# --------------------------------------------------
@dp.message(Command("start"))
async def start_handler(message: types.Message):
    ensure_users_header()

    chat_id = message.chat.id
    user_class = get_user_class(chat_id)

    if not user_class:
        registration_state[chat_id] = {"step": "choose_number"}
        await message.answer(
            "Salom! Sinfingizni tanlang:",
            reply_markup=kb_registration_numbers()
        )
        return

    await message.answer(
        f"Sizning sinfingiz: {user_class}\nKerakli bo‘limni tanlang:",
        reply_markup=kb_main_inline()
    )

# --------------------------------------------------
# HANDLERS: ADMIN COMMANDS
# --------------------------------------------------
@dp.message(Command("adminsendall"))
async def admin_send_all_handler(message: types.Message):
    chat_id = message.chat.id

    if not is_superadmin(chat_id):
        await message.answer("Bu buyruq faqat bosh admin uchun.")
        return

    admin_broadcast_state[chat_id] = {"mode": "all_message"}
    await message.answer("Barcha foydalanuvchilarga yuboriladigan xabarni kiriting.")


@dp.message(Command("adminsendclass"))
async def admin_send_class_handler(message: types.Message):
    chat_id = message.chat.id

    if not is_any_admin(chat_id):
        await message.answer("Sizda bu buyruqdan foydalanish huquqi yo‘q.")
        return

    if is_superadmin(chat_id):
        available_classes = get_existing_classes()
    else:
        allowed = get_admin_allowed_classes(chat_id)
        if not allowed:
            await message.answer("Sizga birorta sinf biriktirilmagan.")
            return
        available_classes = [c for c in allowed if class_exists_in_schedule(c)]

    if not available_classes:
        await message.answer("Siz uchun tanlash mumkin bo‘lgan sinflar topilmadi.")
        return

    admin_broadcast_state[chat_id] = {
        "mode": "class_select_inline",
        "available_classes": available_classes,
        "selected_classes": []
    }

    await message.answer(
        "E'lon yuboriladigan sinflarni tanlang:",
        reply_markup=kb_admin_classes_select(available_classes, [])
    )

# --------------------------------------------------
# HANDLERS: CALLBACKS REGISTRATION
# --------------------------------------------------
@dp.callback_query(F.data == "reg_back_numbers")
async def reg_back_numbers_handler(callback: types.CallbackQuery):
    chat_id = callback.message.chat.id
    registration_state[chat_id] = {"step": "choose_number"}

    await callback.answer()
    await edit_or_send_message(
        callback,
        "Sinfingizni tanlang:",
        reply_markup=kb_registration_numbers()
    )


@dp.callback_query(F.data.startswith("reg_num:"))
async def reg_num_handler(callback: types.CallbackQuery):
    chat_id = callback.message.chat.id
    parallel = callback.data.split(":", 1)[1]

    registration_state[chat_id] = {
        "step": "choose_letter",
        "parallel": parallel
    }

    await callback.answer()
    await edit_or_send_message(
        callback,
        f"{parallel}-sinf uchun harfni tanlang:",
        reply_markup=kb_registration_letters(parallel)
    )


@dp.callback_query(F.data.startswith("reg_class:"))
async def reg_class_handler(callback: types.CallbackQuery):
    chat_id = callback.message.chat.id
    selected_class = callback.data.split(":", 1)[1].upper()

    await callback.answer()

    if not class_exists_in_schedule(selected_class):
        registration_state[chat_id] = {"step": "choose_number"}
        await callback.message.answer(
            f"{selected_class} sinfi uchun darslar hali qo‘shilmagan.",
            reply_markup=kb_registration_numbers()
        )
        return

    save_user_class(chat_id, selected_class)
    registration_state.pop(chat_id, None)

    await edit_or_send_message(
        callback,
        f"{selected_class} sinfi saqlandi.\n"
        f"Endi bot har kuni soat 07:00 da bugungi jadvalni, "
        f"14:00 da so‘rovnomani, 18:00 da eslatmani va 20:00 da ertangi jadvalni yuboradi.\n"
        f"So‘rovnoma esa 00:00 da yopiladi.",
        reply_markup=kb_main_inline()
    )

# --------------------------------------------------
# HANDLERS: CALLBACKS MAIN MENU
# --------------------------------------------------
@dp.callback_query(F.data == "menu_today")
async def menu_today_handler(callback: types.CallbackQuery):
    chat_id = callback.message.chat.id
    user_class = get_user_class(chat_id)

    await callback.answer()

    if not user_class:
        registration_state[chat_id] = {"step": "choose_number"}
        await edit_or_send_message(
            callback,
            "Avval sinfingizni tanlang:",
            reply_markup=kb_registration_numbers()
        )
        return

    today_day_uz = get_today_day_uz()
    response = format_schedule_for_day(user_class, today_day_uz)
    await edit_or_send_message(callback, response, reply_markup=kb_main_inline())


@dp.callback_query(F.data == "menu_tomorrow")
async def menu_tomorrow_handler(callback: types.CallbackQuery):
    chat_id = callback.message.chat.id
    user_class = get_user_class(chat_id)

    await callback.answer()

    if not user_class:
        registration_state[chat_id] = {"step": "choose_number"}
        await edit_or_send_message(
            callback,
            "Avval sinfingizni tanlang:",
            reply_markup=kb_registration_numbers()
        )
        return

    tomorrow_day_uz = get_tomorrow_day_uz()
    response = format_schedule_for_day(user_class, tomorrow_day_uz)
    await edit_or_send_message(callback, response, reply_markup=kb_main_inline())


@dp.callback_query(F.data == "menu_weekly")
async def menu_weekly_handler(callback: types.CallbackQuery):
    chat_id = callback.message.chat.id
    user_class = get_user_class(chat_id)

    await callback.answer()

    if not user_class:
        registration_state[chat_id] = {"step": "choose_number"}
        await edit_or_send_message(
            callback,
            "Avval sinfingizni tanlang:",
            reply_markup=kb_registration_numbers()
        )
        return

    response = format_weekly_schedule(user_class)
    await edit_or_send_message(callback, response, reply_markup=kb_main_inline())

# --------------------------------------------------
# HANDLERS: CALLBACKS FEEDBACK
# --------------------------------------------------
@dp.callback_query(F.data.startswith("fb_subject:"))
async def feedback_subject_handler(callback: types.CallbackQuery):
    chat_id = callback.message.chat.id
    state = get_feedback_state(chat_id)

    await callback.answer()

    if not state:
        await callback.message.answer(
            "So‘rovnoma yopilgan yoki muddati tugagan.",
            reply_markup=kb_main_inline()
        )
        return

    if state.get("poll_date") != today_date_str():
        remove_feedback_state(chat_id)
        await callback.message.answer(
            "So‘rovnoma yopilgan yoki muddati tugagan.",
            reply_markup=kb_main_inline()
        )
        return

    subjects = state.get("subjects", [])
    idx_str = callback.data.split(":", 1)[1]

    if not idx_str.isdigit():
        return

    idx = int(idx_str)
    if idx < 0 or idx >= len(subjects):
        return

    selected_subject = subjects[idx]

    if state["step"] == "best":
        state["best"] = selected_subject
        state["step"] = "worst"
        set_feedback_state(chat_id, state)

        await edit_or_send_message(
            callback,
            "Bugun qaysi fan eng qiyin yoki yoqmagan bo‘ldi?",
            reply_markup=kb_subjects_inline(subjects)
        )
        return

    if state["step"] == "worst":
        best = state["best"]
        worst = selected_subject
        user_class = state["class"]

        save_feedback(chat_id, user_class, best, worst)
        remove_feedback_state(chat_id)

        await edit_or_send_message(
            callback,
            "Rahmat! Sizning fikringiz saqlandi.",
            reply_markup=kb_main_inline()
        )
        return

# --------------------------------------------------
# HANDLERS: CALLBACKS ADMIN
# --------------------------------------------------
@dp.callback_query(F.data == "admin_cancel")
async def admin_cancel_handler(callback: types.CallbackQuery):
    chat_id = callback.message.chat.id
    admin_broadcast_state.pop(chat_id, None)

    await callback.answer("Bekor qilindi")
    await edit_or_send_message(
        callback,
        "Amal bekor qilindi.",
        reply_markup=kb_main_inline()
    )


@dp.callback_query(F.data.startswith("admin_toggle_class:"))
async def admin_toggle_class_handler(callback: types.CallbackQuery):
    chat_id = callback.message.chat.id
    state = admin_broadcast_state.get(chat_id)

    await callback.answer()

    if not state or state.get("mode") != "class_select_inline":
        return

    class_name = callback.data.split(":", 1)[1].upper()
    available_classes = state.get("available_classes", [])
    selected_classes = state.get("selected_classes", [])

    if class_name not in available_classes:
        return

    if class_name in selected_classes:
        selected_classes.remove(class_name)
    else:
        selected_classes.append(class_name)

    state["selected_classes"] = selected_classes
    admin_broadcast_state[chat_id] = state

    await edit_or_send_message(
        callback,
        "E'lon yuboriladigan sinflarni tanlang:",
        reply_markup=kb_admin_classes_select(available_classes, selected_classes)
    )


@dp.callback_query(F.data == "admin_classes_done")
async def admin_classes_done_handler(callback: types.CallbackQuery):
    chat_id = callback.message.chat.id
    state = admin_broadcast_state.get(chat_id)

    await callback.answer()

    if not state or state.get("mode") != "class_select_inline":
        return

    selected_classes = state.get("selected_classes", [])

    if not selected_classes:
        await callback.answer("Kamida bitta sinfni tanlang.", show_alert=True)
        return

    admin_broadcast_state[chat_id] = {
        "mode": "class_message",
        "classes": selected_classes
    }

    await edit_or_send_message(
        callback,
        f"{', '.join(selected_classes)} sinflariga yuboriladigan xabarni kiriting."
    )

# --------------------------------------------------
# HANDLERS: MESSAGES
# --------------------------------------------------
@dp.message()
async def handle_message(message: types.Message):
    chat_id = message.chat.id
    text = (message.text or "").strip()

    state = get_feedback_state(chat_id)
    if state:
        if state.get("poll_date") == today_date_str():
            await message.answer("Iltimos, so‘rovnomaga tugmalar orqali javob bering.")
        else:
            remove_feedback_state(chat_id)
            await message.answer("So‘rovnoma yopilgan yoki muddati tugagan.")
        return

    if chat_id in registration_state:
        await message.answer("Iltimos, sinfni tugmalar orqali tanlang.")
        return

    if chat_id in admin_broadcast_state:
        state = admin_broadcast_state[chat_id]
        mode = state.get("mode")

        if mode == "all_message":
            sent_count = await broadcast_to_all_users(text)
            admin_broadcast_state.pop(chat_id, None)
            await message.answer(f"Xabar barcha foydalanuvchilarga yuborildi. Jami: {sent_count} ta.")
            return

        if mode == "class_message":
            target_classes = state["classes"]
            sent_count = await broadcast_to_classes(target_classes, text)
            admin_broadcast_state.pop(chat_id, None)

            await message.answer(
                f"Xabar {', '.join(target_classes)} sinflariga yuborildi.\n"
                f"Jami: {sent_count} ta foydalanuvchi."
            )
            return

        if mode == "class_select_inline":
            await message.answer("Iltimos, sinflarni tugmalar orqali tanlang.")
            return

    user_class = get_user_class(chat_id)

    if not user_class:
        registration_state[chat_id] = {"step": "choose_number"}
        await message.answer(
            "Avval sinfingizni tanlang:",
            reply_markup=kb_registration_numbers()
        )
        return

    await message.answer(
        "Kerakli bo‘limni tanlang:",
        reply_markup=kb_main_inline()
    )

# --------------------------------------------------
# RUN
# --------------------------------------------------
async def main():
    ensure_users_header()
    load_schedule_to_cache()
    load_users_to_cache()
    load_admins_to_cache()
    load_feedback_state()

    await bot.delete_webhook(drop_pending_updates=False)

    asyncio.create_task(refresh_schedule_cache_every_hour())
    asyncio.create_task(refresh_users_and_admins_cache_every_5_minutes())
    asyncio.create_task(scheduler_loop())

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
