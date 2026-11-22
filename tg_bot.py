import os
import json
import time
import asyncio
import urllib.request
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

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


def load_schedule() -> dict:
    if SCHEDULE_URL:
        sep = "&" if "?" in SCHEDULE_URL else "?"
        url = f"{SCHEDULE_URL}{sep}t={int(time.time() // 60)}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    with open("schedule.json", "r", encoding="utf-8") as f:
        return json.load(f)


def grades_kb() -> InlineKeyboardMarkup:
    grades = ["5", "6", "7", "8", "9", "10", "11"]
    rows = [
        [InlineKeyboardButton(text=f"{g} класс", callback_data=f"grade:{g}") for g in grades[:4]],
        [InlineKeyboardButton(text=f"{g} класс", callback_data=f"grade:{g}") for g in grades[4:]],
    ]
    if WEBAPP_URL:
        rows.append([InlineKeyboardButton(text="🌐 Открыть сайт расписания", web_app=WebAppInfo(url=WEBAPP_URL))])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def classes_kb(data: dict, grade: str) -> InlineKeyboardMarkup:
    classes = [c for c in data.get("classes", {}).keys() if c.startswith(f"{grade} ")]
    rows, cur = [], []
    for c in classes:
        cur.append(InlineKeyboardButton(text=c, callback_data=f"cls:{c}"))
        if len(cur) == 4:
            rows.append(cur)
            cur = []
    if cur:
        rows.append(cur)
    rows.append([InlineKeyboardButton(text="⬅️ Назад к параллелям", callback_data="back:grades")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def format_class_schedule(data: dict, cls_name: str) -> str:
    meta = data.get("meta") or {}
    date_str = meta.get("date") or data.get("source_file") or ""
    weekday = f" ({meta['weekday']})" if meta.get("weekday") else ""
    lessons = data.get("classes", {}).get(cls_name, [])
    lines = [f"📅 <b>Расписание {cls_name}</b> — {date_str}{weekday}\n"]
    if not lessons:
        lines.append("Уроков не найдено.")
        return "\n".join(lines)
    for item in lessons:
        num = item.get("lesson") or item.get("num") or ""
        if "subgroups" in item:
            lines.append(f"<b>{num} урок:</b>")
            for sg in item["subgroups"]:
                room = "💻 ДИСТ" if sg["room"] == "ДИСТ" else f"каб. {sg['room']}"
                lines.append(f"  • {sg['group']} гр.: {sg['subject']} ({room}) — <i>{sg['teacher']}</i>")
        elif "subject" in item:
            room = "💻 ДИСТ" if item["room"] == "ДИСТ" else f"каб. {item['room']}"
            lines.append(f"<b>{num}. {item['subject']}</b> ({room})\n    └ <i>{item['teacher']}</i>")
        else:
            lines.append(f"<b>{num}.</b> {item.get('text', '')}")
    return "\n".join(lines)


@dp.message(CommandStart())
async def cmd_start(message: Message):
    data = load_schedule()
    meta = data.get("meta") or {}
    date_label = meta.get("date") or data.get("updated_at") or ""
    await message.answer(
        f"👋 Привет! Актуальное расписание (<b>{date_label}</b>).\n"
        f"Выберите параллель или напишите класс (например, <code>7 Д</code>) либо фамилию учителя:",
        parse_mode="HTML",
        reply_markup=grades_kb(),
    )


@dp.callback_query(F.data == "back:grades")
async def cb_back_grades(call: CallbackQuery):
    await call.message.edit_text("Выберите параллель:", reply_markup=grades_kb())
    await call.answer()


@dp.callback_query(F.data.startswith("grade:"))
async def cb_grade(call: CallbackQuery):
    grade = call.data.split(":", 1)[1]
    data = load_schedule()
    await call.message.edit_text(f"Выберите класс ({grade} параллель):", reply_markup=classes_kb(data, grade))
    await call.answer()


@dp.callback_query(F.data.startswith("cls:"))
async def cb_class(call: CallbackQuery):
    cls_name = call.data.split(":", 1)[1]
    data = load_schedule()
    grade = cls_name.split()[0]
    await call.message.edit_text(
        format_class_schedule(data, cls_name),
        parse_mode="HTML",
        reply_markup=classes_kb(data, grade),
    )
    await call.answer()


@dp.message(F.text)
async def text_lookup(message: Message):
    q = message.text.strip().upper()
    if len(q) >= 2 and q[:-1].isdigit() and q[-1].isalpha():
        q = f"{q[:-1]} {q[-1]}"
    data = load_schedule()
    if q in data.get("classes", {}):
        await message.answer(format_class_schedule(data, q), parse_mode="HTML", reply_markup=grades_kb())
        return
    q_low = message.text.strip().lower()
    matches = []
    for cls_name, lessons in data.get("classes", {}).items():
        for l in lessons:
            num = l.get("lesson") or int(l.get("num") or 0)
            if "subgroups" in l:
                for sg in l["subgroups"]:
                    if q_low in sg.get("teacher", "").lower():
                        matches.append((num, sg["teacher"], f"{cls_name} ({sg['group']} гр.)", sg["subject"], sg["room"]))
            elif q_low in l.get("teacher", "").lower():
                matches.append((num, l["teacher"], cls_name, l["subject"], l["room"]))
    if matches:
        matches.sort(key=lambda x: x[0])
        teacher_name = matches[0][1]
        lines = [f"👩‍🏫 <b>{teacher_name}</b>:\n"]
        for num, _, cls_str, subj, room in matches:
            r_str = "💻 ДИСТ" if room == "ДИСТ" else f"каб. {room}"
            lines.append(f"<b>{num} ур.</b> — {cls_str}: {subj} ({r_str})")
        await message.answer("\n".join(lines), parse_mode="HTML")
    else:
        await message.answer("Не нашёл такой класс или учителя. Напишите, например, <code>5 А</code> или фамилию учителя.", parse_mode="HTML")


if __name__ == "__main__":
    asyncio.run(dp.start_polling(bot))
