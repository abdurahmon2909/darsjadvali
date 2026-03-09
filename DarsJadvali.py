import asyncio
from datetime import datetime, timedelta
import os
import json

from aiogram import Bot, Dispatcher, types
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.filters import Command

import gspread
from oauth2client.service_account import ServiceAccountCredentials

# --------------------------------------------------
# TELEGRAM
# --------------------------------------------------
BOT_TOKEN = "8378941872:AAHXIVVtjoHrYVZPTjwRczKHtZg2evZrn6I"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# --------------------------------------------------
# UI UZBEK
# --------------------------------------------------
BTN_TODAY = "Bugungi dars jadvali"
BTN_TOMORROW = "Ertangi dars jadvali"
BTN_WEEKLY = "Haftalik dars jadvali"

kb_main = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text=BTN_TODAY)],
        [KeyboardButton(text=BTN_TOMORROW)],
        [KeyboardButton(text=BTN_WEEKLY)]
    ],
    resize_keyboard=True
)

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
# CACHE
# --------------------------------------------------
schedule_cache = {}

# admin state:
# {
#   chat_id: {"mode": "class_select"},
#   chat_id: {"mode": "class_message", "classes": ["8A", "8B"]},
#   chat_id: {"mode": "all_message"}
# }
admin_broadcast_state = {}

# feedback state:
# {
#   chat_id: {
#       "step": "best" / "worst",
#       "class": "8A",
#       "subjects": ["Matematika", "Tarix"],
#       "best": "Matematika"
#   }
# }
feedback_state = {}

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


def get_user_class(chat_id: int):
    rows = users_sheet.get_all_values()
    if len(rows) <= 1:
        return None

    for row in rows[1:]:
        if len(row) >= 2 and row[0].strip() == str(chat_id):
            return row[1].strip()
    return None


def save_user_class(chat_id: int, user_class: str):
    rows = users_sheet.get_all_values()

    if not rows:
        users_sheet.append_row(["chat_id", "class"])
        rows = users_sheet.get_all_values()

    for index, row in enumerate(rows[1:], start=2):
        if len(row) >= 1 and row[0].strip() == str(chat_id):
            users_sheet.update(f"A{index}:B{index}", [[str(chat_id), user_class]])
            return

    users_sheet.append_row([str(chat_id), user_class])


def get_all_users():
    rows = users_sheet.get_all_values()
    if len(rows) <= 1:
        return []

    result = []
    for row in rows[1:]:
        if len(row) >= 2 and row[0].strip() and row[1].strip():
            result.append({
                "chat_id": row[0].strip(),
                "class": row[1].strip().upper()
            })
    return result


def get_users_by_class(target_class: str):
    target_class = target_class.upper()
    return [u for u in get_all_users() if u["class"] == target_class]

# --------------------------------------------------
# ADMINS HELPERS
# --------------------------------------------------
def get_admins_data():
    rows = admins_sheet.get_all_values()
    if len(rows) <= 1:
        return []

    result = []
    for row in rows[1:]:
        if len(row) < 2:
            continue

        chat_id = row[0].strip()
        role = row[1].strip().lower()
        classes = row[2].strip() if len(row) >= 3 else ""

        result.append({
            "chat_id": chat_id,
            "role": role,
            "classes": classes
        })
    return result


def get_admin_info(chat_id: int):
    chat_id = str(chat_id)
    for admin in get_admins_data():
        if admin["chat_id"] == chat_id:
            return admin
    return None


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


def can_admin_send_to_class(chat_id: int, target_class: str) -> bool:
    if is_superadmin(chat_id):
        return True

    allowed = get_admin_allowed_classes(chat_id)
    return target_class.upper() in allowed

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


def build_subject_keyboard(subjects):
    keyboard = []
    for subject in subjects:
        keyboard.append([KeyboardButton(text=subject)])

    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True
    )


def save_feedback(chat_id, user_class, best, worst):
    date = datetime.now().strftime("%Y-%m-%d")
    feedback_sheet.append_row([
        date,
        str(chat_id),
        user_class,
        best,
        worst
    ])

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


def get_today_day_uz() -> str:
    today_en = datetime.now().strftime("%A")
    return DAY_MAP.get(today_en, today_en)


def get_tomorrow_day_uz() -> str:
    tomorrow_en = (datetime.now() + timedelta(days=1)).strftime("%A")
    return DAY_MAP.get(tomorrow_en, tomorrow_en)

# --------------------------------------------------
# BACKGROUND TASKS
# --------------------------------------------------
async def refresh_schedule_cache_every_hour():
    while True:
        try:
            load_schedule_to_cache()
        except Exception as e:
            print(f"Schedule cache yangilashda xatolik: {e}")

        await asyncio.sleep(3600)


async def send_today_schedule():
    await asyncio.sleep(1)

    while True:
        now = datetime.now()
        target_time = now.replace(hour=7, minute=0, second=0, microsecond=0)

        if now >= target_time:
            target_time += timedelta(days=1)

        wait_seconds = (target_time - now).total_seconds()
        await asyncio.sleep(wait_seconds)

        users = get_all_users()
        today_day_uz = get_today_day_uz()

        for user in users:
            chat_id = user["chat_id"]
            user_class = user["class"]

            try:
                response = format_schedule_for_day(user_class, today_day_uz)
                await bot.send_message(chat_id, response)
                await asyncio.sleep(0.05)
            except Exception as e:
                print(f"Bugungi jadval yuborishda xatolik {chat_id}: {e}")


async def send_daily_feedback_poll():
    await asyncio.sleep(1)

    while True:
        now = datetime.now()
        target = now.replace(hour=14, minute=0, second=0, microsecond=0)

        if now >= target:
            target += timedelta(days=1)

        wait_seconds = (target - now).total_seconds()
        await asyncio.sleep(wait_seconds)

        users = get_all_users()

        for user in users:
            chat_id = user["chat_id"]
            user_class = user["class"]

            subjects = get_unique_subjects_for_today(user_class)

            if not subjects:
                continue

            keyboard = build_subject_keyboard(subjects)

            feedback_state[chat_id] = {
                "step": "best",
                "class": user_class,
                "subjects": subjects
            }

            try:
                await bot.send_message(
                    chat_id,
                    "Bugun qaysi fan sizga eng yoqqan bo‘ldi?",
                    reply_markup=keyboard
                )
                await asyncio.sleep(0.05)
            except Exception as e:
                print(f"Feedback poll yuborishda xatolik {chat_id}: {e}")


async def send_tomorrow_schedule():
    await asyncio.sleep(1)

    while True:
        now = datetime.now()
        target_time = now.replace(hour=20, minute=0, second=0, microsecond=0)

        if now >= target_time:
            target_time += timedelta(days=1)

        wait_seconds = (target_time - now).total_seconds()
        await asyncio.sleep(wait_seconds)

        users = get_all_users()
        tomorrow_day_uz = get_tomorrow_day_uz()

        for user in users:
            chat_id = user["chat_id"]
            user_class = user["class"]

            try:
                response = format_schedule_for_day(user_class, tomorrow_day_uz)
                await bot.send_message(chat_id, response)
                await asyncio.sleep(0.05)
            except Exception as e:
                print(f"Ertangi jadval yuborishda xatolik {chat_id}: {e}")

# --------------------------------------------------
# SEND HELPERS
# --------------------------------------------------
async def broadcast_to_all_users(text: str):
    users = get_all_users()
    sent_count = 0

    for user in users:
        try:
            await bot.send_message(user["chat_id"], f"📢 E'lon:\n\n{text}")
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
                    f"📢 {', '.join(target_classes)} sinflari uchun e'lon:\n\n{text}"
                )
                sent_count += 1
                await asyncio.sleep(0.05)
            except Exception as e:
                print(f"Broadcast xatolik {user['chat_id']}: {e}")

    return sent_count

# --------------------------------------------------
# HANDLERS
# --------------------------------------------------
@dp.message(Command("start"))
async def start_handler(message: types.Message):
    ensure_users_header()

    chat_id = message.chat.id
    user_class = get_user_class(chat_id)

    if not user_class:
        await message.answer("Assalomu alaykum! Marhamat, sinfingizni kiriting.\nMasalan: 5A, 7B, 10A")
        return

    await message.answer(
        f"Sizning sinfingiz: {user_class}\nKerakli bo‘limni tanlang:",
        reply_markup=kb_main
    )


@dp.message(Command("adminsendall"))
async def admin_send_all_handler(message: types.Message):
    chat_id = message.chat.id

    if not is_superadmin(chat_id):
        await message.answer("Bu buyruq faqat bosh admin uchun.")
        return

    admin_broadcast_state[chat_id] = {"mode": "all_message"}
    await message.answer("Barcha foydalanuvchilarga yuboriladigan xabarni kiriting.")


@dp.message(Command("adminxabar"))
async def admin_send_class_handler(message: types.Message):
    chat_id = message.chat.id

    if not is_any_admin(chat_id):
        await message.answer("Sizda bu buyruqdan foydalanish huquqi yo‘q.")
        return

    if is_superadmin(chat_id):
        await message.answer("Qaysi sinfga yubormoqchisiz?\nMasalan: 8A yoki 8A, 8B, 8V")
    else:
        allowed = get_admin_allowed_classes(chat_id)
        if not allowed:
            await message.answer("Sizga birorta sinf biriktirilmagan.")
            return
        await message.answer(
            "Qaysi sinfga yubormoqchisiz?\n"
            "Bir yoki bir nechta sinfni kiriting.\n"
            f"Sizga ruxsat etilgan sinflar: {', '.join(allowed)}\n\n"
            "Masalan: 8A yoki 8A,8B"
        )

    admin_broadcast_state[chat_id] = {"mode": "class_select"}


@dp.message()
async def handle_message(message: types.Message):
    chat_id = message.chat.id
    text = (message.text or "").strip()

    # -------------------------
    # FEEDBACK STATE
    # -------------------------
    if chat_id in feedback_state:
        state = feedback_state[chat_id]
        subjects = state["subjects"]

        if text not in subjects:
            await message.answer("Iltimos, tugmalardan birini tanlang.")
            return

        if state["step"] == "best":
            state["best"] = text
            state["step"] = "worst"

            keyboard = build_subject_keyboard(subjects)

            await message.answer(
                "Bugun qaysi fan eng qiyin yoki yoqmagan bo‘ldi?",
                reply_markup=keyboard
            )
            return

        if state["step"] == "worst":
            best = state["best"]
            worst = text
            user_class = state["class"]

            save_feedback(chat_id, user_class, best, worst)
            feedback_state.pop(chat_id, None)

            await message.answer(
                "Rahmat! Sizning fikringiz saqlandi.",
                reply_markup=kb_main
            )
            return

    # -------------------------
    # ADMIN STATE
    # -------------------------
    if chat_id in admin_broadcast_state:
        state = admin_broadcast_state[chat_id]
        mode = state.get("mode")

        if mode == "all_message":
            sent_count = await broadcast_to_all_users(text)
            admin_broadcast_state.pop(chat_id, None)
            await message.answer(f"Xabar barcha foydalanuvchilarga yuborildi. Jami: {sent_count} ta.")
            return

        if mode == "class_select":
            classes_input = text.upper().replace(" ", "")
            target_classes = [c for c in classes_input.split(",") if c]

            if not target_classes:
                await message.answer("Iltimos, kamida bitta sinf kiriting. Masalan: 8A yoki 8A,8B")
                return

            valid_classes = []
            invalid_classes = []

            for c in target_classes:
                if class_exists_in_schedule(c):
                    valid_classes.append(c)
                else:
                    invalid_classes.append(c)

            if invalid_classes:
                await message.answer(
                    f"Quyidagi sinflar topilmadi: {', '.join(invalid_classes)}"
                )
                return

            if not is_superadmin(chat_id):
                allowed = get_admin_allowed_classes(chat_id)

                for c in valid_classes:
                    if c not in allowed:
                        await message.answer(
                            f"Siz {c} sinfiga xabar yubora olmaysiz.\n"
                            f"Ruxsat etilgan sinflar: {', '.join(allowed)}"
                        )
                        return

            admin_broadcast_state[chat_id] = {
                "mode": "class_message",
                "classes": valid_classes
            }

            await message.answer(
                f"{', '.join(valid_classes)} sinfiga yuboriladigan xabarni kiriting."
            )
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

    # -------------------------
    # USER FLOW
    # -------------------------
    user_class = get_user_class(chat_id)

    if not user_class:
        if text in [BTN_TODAY, BTN_TOMORROW, BTN_WEEKLY]:
            await message.answer("Iltimos, avval sinfingizni yozing. Masalan: 8A")
            return

        if text.startswith("/"):
            await message.answer("Avval sinfingizni kiriting. Masalan: 8A")
            return

        if len(text) > 10 or " " in text:
            await message.answer(
                "Sinf noto‘g‘ri kiritildi.\n"
                "Iltimos, sinfni quyidagicha kiriting: 8A"
            )
            return

        new_class = text.upper()

        if not class_exists_in_schedule(new_class):
            existing_classes = get_existing_classes()
            preview = ", ".join(existing_classes[:10]) if existing_classes else "Mavjud sinflar topilmadi"

            await message.answer(
                f"{new_class} sinfi jadvalda topilmadi.\n"
                f"Iltimos, sinfni to‘g‘ri kiriting. Masalan: 8A\n\n"
                f"Ba'zi mavjud sinflar: {preview}"
            )
            return

        save_user_class(chat_id, new_class)
        await message.answer(
            f"{new_class} sinfi saqlandi.\n"
            f"Endi bot har kuni soat 07:00 da bugungi jadvalni, 14:00 da so‘rovnomani va 20:00 da ertangi jadvalni yuboradi.",
            reply_markup=kb_main
        )
        return

    if text == BTN_TODAY:
        today_day_uz = get_today_day_uz()
        response = format_schedule_for_day(user_class, today_day_uz)
        await message.answer(response, reply_markup=kb_main)
        return

    if text == BTN_TOMORROW:
        tomorrow_day_uz = get_tomorrow_day_uz()
        response = format_schedule_for_day(user_class, tomorrow_day_uz)
        await message.answer(response, reply_markup=kb_main)
        return

    if text == BTN_WEEKLY:
        response = format_weekly_schedule(user_class)
        await message.answer(response, reply_markup=kb_main)
        return

    await message.answer("Pastdagi tugmalardan foydalaning.", reply_markup=kb_main)

# --------------------------------------------------
# RUN
# --------------------------------------------------
async def main():
    ensure_users_header()
    load_schedule_to_cache()
    asyncio.create_task(refresh_schedule_cache_every_hour())
    asyncio.create_task(send_today_schedule())
    asyncio.create_task(send_daily_feedback_poll())
    asyncio.create_task(send_tomorrow_schedule())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())