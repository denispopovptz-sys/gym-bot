import os
import asyncio
import base64
import json
import logging
import aiohttp
from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
)
from aiogram.filters import CommandStart

# ── Config ───────────────────────────────────────────────────────────────────
BOT_TOKEN     = os.getenv("BOT_TOKEN", "")
ANTHROPIC_KEY = os.getenv("ANTHROPIC_KEY", "")
MINI_APP_URL  = os.getenv("MINI_APP_URL", "")

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp  = Dispatcher()

# ── Helpers ──────────────────────────────────────────────────────────────────
async def download_file(file_id: str) -> bytes:
    file = await bot.get_file(file_id)
    url  = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file.file_path}"
    async with aiohttp.ClientSession() as s:
        async with s.get(url) as r:
            return await r.read()

async def analyze_with_claude(image_bytes: bytes, media_type: str, prompt: str) -> dict:
    b64 = base64.b64encode(image_bytes).decode()
    payload = {
        "model": "claude-opus-4-6",
        "max_tokens": 800,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64}},
                {"type": "text",  "text": prompt}
            ]
        }]
    }
    headers = {
        "Content-Type": "application/json",
        "x-api-key": ANTHROPIC_KEY,
        "anthropic-version": "2023-06-01"
    }
    async with aiohttp.ClientSession() as s:
        async with s.post("https://api.anthropic.com/v1/messages", json=payload, headers=headers) as r:
            data = await r.json()
            raw  = data["content"][0]["text"]
            try:
                return json.loads(raw.replace("```json","").replace("```","").strip())
            except Exception:
                return {"error": raw}

def calories_keyboard(result: dict) -> InlineKeyboardMarkup:
    import urllib.parse
    param = urllib.parse.quote(json.dumps(result, ensure_ascii=False))
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="📊 Добавить в дневник",
            web_app=WebAppInfo(url=f"{MINI_APP_URL}?action=add_calories&data={param}")
        )
    ]])

def workout_keyboard(result: dict) -> InlineKeyboardMarkup:
    import urllib.parse
    param = urllib.parse.quote(json.dumps(result, ensure_ascii=False))
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="🏋️ Открыть тренировку",
            web_app=WebAppInfo(url=f"{MINI_APP_URL}?action=workout&data={param}")
        )
    ]])

def main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="💪 Открыть GymAnywhere",
            web_app=WebAppInfo(url=MINI_APP_URL)
        )
    ]])

# ── Handlers ─────────────────────────────────────────────────────────────────
@dp.message(CommandStart())
async def cmd_start(msg: Message):
    name = msg.from_user.first_name or "Атлет"
    await msg.answer(
        f"Привет, {name}! 👋\n\n"
        "⚡ *GymAnywhere* — твой фитнес-компаньон для путешествий\n\n"
        "Что я умею:\n"
        "🎥 Отправь *кружочек с едой* → посчитаю калории\n"
        "📸 Отправь *фото блюда* → посчитаю КБЖУ\n"
        "🏋️ Отправь *фото тренажёров* с подписью 'зал' → составлю программу\n"
        "📱 Или открой полное приложение 👇",
        parse_mode="Markdown",
        reply_markup=main_keyboard()
    )

@dp.message(F.video_note)
async def handle_video_note(msg: Message):
    wait = await msg.answer("🎥 Анализирую кружочек...")
    thumb = msg.video_note.thumbnail
    if not thumb:
        await wait.edit_text("❌ Не смог получить превью. Попробуй обычное фото.")
        return
    image_bytes = await download_file(thumb.file_id)
    prompt = (
        "На изображении — еда из кружочка Telegram. "
        "Определи блюдо и верни ТОЛЬКО JSON без маркдауна: "
        '{"name":"название блюда","kcal":число,"protein":число,"fat":число,"carbs":число,"comment":"короткий совет"}'
    )
    result = await analyze_with_claude(image_bytes, "image/jpeg", prompt)
    if "error" in result:
        await wait.edit_text(f"❌ Ошибка:\n{result['error']}")
        return
    text = (
        f"🍽 *{result.get('name','Блюдо')}*\n\n"
        f"🔥 `{result.get('kcal',0)}` ккал\n"
        f"💪 Белки: `{result.get('protein',0)}г`\n"
        f"🧈 Жиры: `{result.get('fat',0)}г`\n"
        f"🌾 Углеводы: `{result.get('carbs',0)}г`\n"
    )
    if result.get("comment"):
        text += f"\n💡 _{result['comment']}_"
    await wait.edit_text(text, parse_mode="Markdown", reply_markup=calories_keyboard(result))

@dp.message(F.photo)
async def handle_photo(msg: Message):
    caption  = (msg.caption or "").lower()
    is_gym   = any(w in caption for w in ["зал","тренажёр","тренировка","gym","workout","штанга","гантел"])
    wait     = await msg.answer("📸 Анализирую фото...")
    photo_bytes = await download_file(msg.photo[-1].file_id)

    if is_gym:
        prompt = (
            "На фото — тренажёры в спортзале. Распознай оборудование и составь программу. "
            "Верни ТОЛЬКО JSON: "
            '{"title":"название","duration":"время","difficulty":"сложность",'
            '"exercises":[{"name":"упражнение","sets":"подходы","reps":"повторения","muscles":"мышцы","tips":"совет"}]}'
        )
        result = await analyze_with_claude(photo_bytes, "image/jpeg", prompt)
        if "error" in result:
            await wait.edit_text(f"❌ Ошибка: {result['error']}")
            return
        exs   = result.get("exercises", [])
        lines = [
            f"🏋️ *{result.get('title','Тренировка')}*",
            f"⏱ {result.get('duration','')}  📊 {result.get('difficulty','')}",
            f"💪 {len(exs)} упражнений\n"
        ]
        for i, ex in enumerate(exs[:5], 1):
            lines.append(f"{i}. *{ex['name']}* — {ex['sets']}×{ex['reps']}")
        if len(exs) > 5:
            lines.append(f"...и ещё {len(exs)-5} упражнений в приложении")
        await wait.edit_text("\n".join(lines), parse_mode="Markdown", reply_markup=workout_keyboard(result))
    else:
        prompt = (
            "На фото — еда. Определи блюдо и верни ТОЛЬКО JSON: "
            '{"name":"название","kcal":число,"protein":число,"fat":число,"carbs":число,"comment":"совет"}'
        )
        result = await analyze_with_claude(photo_bytes, "image/jpeg", prompt)
        if "error" in result:
            await wait.edit_text(f"❌ Ошибка: {result['error']}")
            return
        text = (
            f"🍽 *{result.get('name','Блюдо')}*\n\n"
            f"🔥 `{result.get('kcal',0)}` ккал\n"
            f"💪 Белки: `{result.get('protein',0)}г`\n"
            f"🧈 Жиры: `{result.get('fat',0)}г`\n"
            f"🌾 Углеводы: `{result.get('carbs',0)}г`\n"
        )
        if result.get("comment"):
            text += f"\n💡 _{result['comment']}_"
        await wait.edit_text(text, parse_mode="Markdown", reply_markup=calories_keyboard(result))

@dp.message(F.text & ~F.text.startswith("/"))
async def handle_text(msg: Message):
    wait = await msg.answer("🤔 Считаю калории...")
    prompt = (
        f"Блюдо: {msg.text}. "
        "Оцени КБЖУ и верни ТОЛЬКО JSON: "
        '{"name":"название","kcal":число,"protein":число,"fat":число,"carbs":число,"comment":"совет"}'
    )
    headers = {
        "Content-Type": "application/json",
        "x-api-key": ANTHROPIC_KEY,
        "anthropic-version": "2023-06-01"
    }
    async with aiohttp.ClientSession() as s:
        async with s.post(
            "https://api.anthropic.com/v1/messages",
            json={"model":"claude-opus-4-6","max_tokens":400,"messages":[{"role":"user","content":prompt}]},
            headers=headers
        ) as r:
            data = await r.json()
            raw  = data["content"][0]["text"]
    try:
        result = json.loads(raw.replace("```json","").replace("```","").strip())
    except Exception:
        await wait.edit_text("❌ Не смог распознать блюдо. Опиши точнее.")
        return
    text = (
        f"🍽 *{result.get('name','Блюдо')}*\n\n"
        f"🔥 `{result.get('kcal',0)}` ккал\n"
        f"💪 Белки: `{result.get('protein',0)}г`\n"
        f"🧈 Жиры: `{result.get('fat',0)}г`\n"
        f"🌾 Углеводы: `{result.get('carbs',0)}г`\n"
    )
    if result.get("comment"):
        text += f"\n💡 _{result['comment']}_"
    await wait.edit_text(text, parse_mode="Markdown", reply_markup=calories_keyboard(result))

# ── Run ───────────────────────────────────────────────────────────────────────
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())