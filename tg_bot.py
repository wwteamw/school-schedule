import os
import re
import json
import time
import asyncio
import urllib.request
from datetime import datetime, timezone, timedelta
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from aiogram.filters import CommandStart

BOT_TOKEN = os.environ["TG_BOT_TOKEN"]
SCHEDULE_URL = os.environ.get(
    "SCHEDULE_JSON_URL",
    "https://wwteamw.github.io/school-schedule/schedule.json",
)
WEBAPP_URL = os.environ.get(
    "WEBAPP_URL",
    "https://wwteamw.github.io/school-schedule/",
)

BELLS_MONDAY = {
    1: "08:00–08:30",
    2: "08:40–09:20",
    3: "09:30–10:10",
    4: "10:30–11:10",
    5: "11:30–12:10",
    6: "12:20–13:00",
    7: "13:10–13:50",
    8: "14:00–14:40",
    9: "15:00–15:40",
    10: "15:50–16:30",
    11: "16:40–17:20",
    12: "17:30–18:10",
    13: "18:20–19:00",
}

BELLS_REGULAR = {
    1: "08:00–08:40",
    2: "08:50–09:30",
    3: "09:40–10:20",
    4: "10:40–11:20",
    5: "11:40–12:20",
    6: "12:30–13:10",
    7: "13:20–14:00",
    8: "14:10–14:50",
    9: "15:10–15:50",
    10: "16:00–16:40",
    11: "16:50–17:30",
    12: "17:40–18:20",
    13: "18:30–19:10",
}

MSK = timezone(timedelta(hours=3))
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


def parse_date_tuple(s: str) -> tuple[int, int, int]:
    m = re.search(r"(\d{1,2})[._-](\d{1,2})[._-](\d{4})", s or "")
    if m:
        return (int(m.group(3)), int(m.group(2)), int(m.group(1)))
    return (0, 0, 0)


def get_days_map(raw: dict) -> tuple[dict, list[str]]:
    days = {}
    if isinstance(raw.get("days"), dict) and raw["days"]:
        for k, v in raw["days"].items():
            if isinstance(v, dict) and v.get("classes"):
                days[k] = v
    elif raw.get("classes"):
        k = ((raw.get("meta") or {}).get("date") or "current").strip()
        days[k] = raw
    keys = sorted(days.keys(), key=lambda x: (parse_date_tuple(x), x))
    return days, keys


def pick_default_day_key(keys: list[str]) -> str:
    if not keys:
        return ""
    now = datetime.now(MSK)
    today_str = now.strftime("%d.%m.%Y")
    cur_min = now.hour * 60 + now.minute
    if today_str in keys and cur_min < 19 * 60 + 15:
        return today_str
    today_tuple = (now.year, now.month, now.day)
    future = [k for k in keys if parse_date_tuple(k) > today_tuple]
    if future:
        return future[0]
    if today_str in keys:
        return today_str
    return keys[-1]


def get_bells_for_schedule(day_data: dict) -> dict:
    meta = day_data.get("meta") or {}
    wd = (meta.get("weekday") or "").lower()
    if "понедельник" in wd:
        return BELLS_MONDAY
    if wd:
        return BELLS_REGULAR
    date_raw = meta.get("date") or day_data.get("source_file") or ""
    dt_tuple = parse_date_tuple(date_raw)
    if dt_tuple != (0, 0, 0):
        try:
            if datetime(*dt_tuple).weekday() == 0:
                return BELLS_MONDAY
        except ValueError:
            pass
    return BELLS_REGULAR


def load_schedule() -> dict:
    if SCHEDULE_URL:
        sep = "&" if "?" in SCHEDULE_URL else "?"
        url = f"{SCHEDULE_URL}{sep}t={int(time.time() // 60)}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    with open("schedule.json", "r", encoding="utf-8") as f:
        return json.load(f)


def grades_kb(day_key: str = "") -> InlineKeyboardMarkup:
    grades = ["5", "6", "7", "8", "9", "10", "11"]
    rows = [
        [InlineKeyboardButton(text=f"{g} класс", callback_data=f"grade:{g}:{day_key}") for g in grades[:4]],
        [InlineKeyboardButton(text=f"{g} класс", callback_data=f"grade:{g}:{day_key}") for g in grades[4:]],
    ]
    if WEBAPP_URL:
        rows.append([InlineKeyboardButton(text="🌐 Открыть удобный сайт", web_app=WebAppInfo(url=WEBAPP_URL))])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def classes_kb(day_data: dict, grade: str, day_key: str, all_keys: list[str]) -> InlineKeyboardMarkup:
    classes = [c for c in day_data.get("classes", {}).keys() if c.startswith(f"{grade} ")]
    rows, cur = [], []
    for c in classes:
        cur.append(InlineKeyboardButton(text=c, callback_data=f"cls:{c}:{day_key}"))
        if len(cur) == 4:
            rows.append(cur)
            cur = []
    if cur:
        rows.append(cur)
    if len(all_keys) > 1:
        rows.append([
            InlineKeyboardButton(
                text=f"{'✅ ' if k == day_key else '📅 '}{k[:5]}",
                callback_data=f"grade:{grade}:{k}",
            )
            for k in all_keys[-2:]
        ])
    rows.append([InlineKeyboardButton(text="⬅️ Назад к параллелям", callback_data=f"back:grades:{day_key}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def class_view_kb(day_data: dict, grade: str, cls_name: str, day_key: str, all_keys: list[str]) -> InlineKeyboardMarkup:
    kb = classes_kb(day_data, grade, day_key, all_keys)
    if len(all_keys) > 1:
        kb.inline_keyboard[-2] = [
            InlineKeyboardButton(
                text=f"{'✅ ' if k == day_key else '📅 '}{k[:5]}",
                callback_data=f"cls:{cls_name}:{k}",
            )
            for k in all_keys[-2:]
        ]
    return kb


def format_class_schedule(day_data: dict, cls_name: str) -> str:
    meta = day_data.get("meta") or {}
    bells = get_bells_for_schedule(day_data)
    date_str = meta.get("date") or day_data.get("source_file") or ""
    weekday = f" ({meta['weekday']})" if meta.get("weekday") else ""
    lessons = day_data.get("classes", {}).get(cls_name, [])
    lines = [f"📅 <b>Расписание {cls_name}</b> — {date_str}{weekday}\n"]
    if not lessons:
        lines.append("Уроков не найдено.")
        return "\n".join(lines)
    for item in lessons:
        num = int(item.get("lesson") or item.get("num") or 0)
        bell = f" <code>{bells[num]}</code>" if num in bells else ""
        if "subgroups" in item:
            lines.append(f"<b>{num} ур.</b>{bell}:")
            for sg in item["subgroups"]:
                room = "💻 ДИСТ" if sg["room"] == "ДИСТ" else f"каб. {sg['room']}"
                lines.append(f"  • {sg['group']} гр.: {sg['subject']} ({room}) — <i>{sg['teacher']}</i>")
        elif "subject" in item:
            room = "💻 ДИСТ" if item["room"] == "ДИСТ" else f"каб. {item['room']}"
            lines.append(f"<b>{num} ур.</b>{bell} — <b>{item['subject']}</b> ({room})\n    └ <i>{item['teacher']}</i>")
        else:
            lines.append(f"<b>{num} ур.</b>{bell} — {item.get('text', '')}")
    return "\n".join(lines)


@dp.message(CommandStart())
async def cmd_start(message: Message):
    raw = load_schedule()
    days, keys = get_days_map(raw)
    day_key = pick_default_day_key(keys)
    day_data = days.get(day_key, raw)
    meta = day_data.get("meta") or {}
    date_label = meta.get("date") or raw.get("updated_at") or ""
    await message.answer(
        f"👋 Привет! Актуальное расписание (<b>{date_label}</b>).\n"
        f"Выберите параллель или напишите класс (например, <code>7 Д</code>) либо фамилию учителя:",
        parse_mode="HTML",
        reply_markup=grades_kb(day_key),
    )


@dp.callback_query(F.data.startswith("back:grades"))
async def cb_back_grades(call: CallbackQuery):
    parts = call.data.split(":", 2)
    day_key = parts[2] if len(parts) > 2 else ""
    await call.message.edit_text("Выберите параллель:", reply_markup=grades_kb(day_key))
    await call.answer()


@dp.callback_query(F.data.startswith("grade:"))
async def cb_grade(call: CallbackQuery):
    parts = call.data.split(":", 2)
    grade = parts[1]
    req_day = parts[2] if len(parts) > 2 else ""
    raw = load_schedule()
    days, keys = get_days_map(raw)
    day_key = req_day if req_day in days else pick_default_day_key(keys)
    day_data = days.get(day_key, raw)
    await call.message.edit_text(
        f"Выберите класс ({grade} параллель • {day_key}):",
        reply_markup=classes_kb(day_data, grade, day_key, keys),
    )
    await call.answer()


@dp.callback_query(F.data.startswith("cls:"))
async def cb_class(call: CallbackQuery):
    parts = call.data.split(":", 2)
    cls_name = parts[1]
    req_day = parts[2] if len(parts) > 2 else ""
    raw = load_schedule()
    days, keys = get_days_map(raw)
    day_key = req_day if req_day in days else pick_default_day_key(keys)
    day_data = days.get(day_key, raw)
    grade = cls_name.split()[0]
    await call.message.edit_text(
        format_class_schedule(day_data, cls_name),
        parse_mode="HTML",
        reply_markup=class_view_kb(day_data, grade, cls_name, day_key, keys),
    )
    await call.answer()


@dp.message(F.text)
async def text_lookup(message: Message):
    q = message.text.strip().upper()
    if len(q) >= 2 and q[:-1].isdigit() and q[-1].isalpha():
        q = f"{q[:-1]} {q[-1]}"
    raw = load_schedule()
    days, keys = get_days_map(raw)
    day_key = pick_default_day_key(keys)
    day_data = days.get(day_key, raw)
    if q in day_data.get("classes", {}):
        grade = q.split()[0]
        await message.answer(
            format_class_schedule(day_data, q),
            parse_mode="HTML",
            reply_markup=class_view_kb(day_data, grade, q, day_key, keys),
        )
        return
    bells = get_bells_for_schedule(day_data)
    q_low = message.text.strip().lower().replace("ё", "е")
    matches = []
    for cls_name, lessons in day_data.get("classes", {}).items():
        for l in lessons:
            num = int(l.get("lesson") or l.get("num") or 0)
            if "subgroups" in l:
                for sg in l["subgroups"]:
                    if q_low in sg.get("teacher", "").lower().replace("ё", "е"):
                        matches.append((num, sg["teacher"], f"{cls_name} ({sg['group']} гр.)", sg["subject"], sg["room"]))
            elif q_low in l.get("teacher", "").lower().replace("ё", "е"):
                matches.append((num, l["teacher"], cls_name, l["subject"], l["room"]))
    if matches:
        matches.sort(key=lambda x: x[0])
        teacher_name = matches[0][1]
        lines = [f"👩‍🏫 <b>{teacher_name}</b> ({day_key}):\n"]
        for num, _, cls_str, subj, room in matches:
            bell = f" <code>{bells[num]}</code>" if num in bells else ""
            r_str = "💻 ДИСТ" if room == "ДИСТ" else f"каб. {room}"
            lines.append(f"<b>{num} ур.</b>{bell} — {cls_str}: {subj} ({r_str})")
        await message.answer("\n".join(lines), parse_mode="HTML")
    else:
        await message.answer("Не нашёл такой класс или учителя. Напишите, например, <code>5 А</code> или фамилию учителя.", parse_mode="HTML")


if __name__ == "__main__":
    asyncio.run(dp.start_polling(bot))
