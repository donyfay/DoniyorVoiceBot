import os
import logging
import asyncio
import io
from dotenv import load_dotenv

# ДОБАВЛЕНО: Библиотека для прямого асинхронного HTTP-запроса
import aiohttp 

# Aiogram v3 импорты
from aiogram import Bot, Dispatcher, types, F
from aiogram.fsm.storage.memory import MemoryStorage 
from aiogram.types import FSInputFile 

# OpenAI (Используем Async версию)
from openai import AsyncOpenAI 

# --- 1. ЗАГРУЗКА КЛЮЧЕЙ И КОНФИГУРАЦИЯ ---
load_dotenv()

# Переменные окружения
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID") 

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- 2. СИСТЕМНЫЙ ПРОМПТ (Ваш стиль общения) ---
SYSTEM_PROMPT = """
Ты — это Дониёр. Ты переписываешься в Telegram со своими друзьями, знакомыми, родственниками и девушками.
Ты не бот, ты реальный человек. Пиши естественно, с эмоциями, иногда с юмором, по-дружески.
Иногда сокращай слова, используй разговорные выражения и эмодзи. Не будь формальным.

Стиль: лёгкий, уверенный, спокойный. Иногда шути, но не перегибай.
Если пишет девушка — можешь отвечать чуть теплее и мягче.
Если пишет друг — свободнее, с лёгким юмором.
Если пишет родственник — уважительно, спокойно, без флирта.

Не используй слова: "искусственный интеллект", "бот", "как программа", "я не знаю".
Если не уверен в ответе — отвечай нейтрально, с лёгкой неопределённостью, как человек.

Примеры твоего общения:
- "ага щас гляну )"
- "да норм всё, чё ты 😄"
- "я на улице, позже отвечу ок?"
- "понял, потом обсудим 👍"
- "а ты как? "
- "ты как обычно 😂"
- "кофе допью и напишу"

Пиши коротко, не идеально — живо. Можно иногда ошибку или «слово не туда».
Не используй сложные конструкции и не будь слишком вежливым.
"""
# --- КОНЕЦ СИСТЕМНОГО ПРОМПТА ---

# Инициализация API
dp = Dispatcher(storage=MemoryStorage())
bot = Bot(token=TELEGRAM_BOT_TOKEN)

# Инициализация асинхронных клиентов
openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)


# --- 3. ЛОГИКА ПАМЯТИ (ИСТОРИЯ ЧАТА) ---
user_histories = {}
MAX_CONTEXT_MESSAGES = 10 

def get_history(user_id):
    """Инициализирует или возвращает историю для пользователя."""
    if user_id not in user_histories:
        # Используем встроенный SYSTEM_PROMPT
        user_histories[user_id] = [
            {"role": "system", "content": SYSTEM_PROMPT},
        ]
    return user_histories[user_id]

def update_history(user_id, role, content):
    """Обновляет историю и обрезает ее до MAX_CONTEXT_MESSAGES."""
    history = get_history(user_id)
    history.append({"role": role, "content": content})

    # Обрезаем историю, сохраняя SYSTEM_PROMPT (индекс 0)
    if len(history) > MAX_CONTEXT_MESSAGES + 1:
        # Сохраняем SYSTEM_PROMPT и последние MAX_CONTEXT_MESSAGES
        user_histories[user_id] = [history[0]] + history[-(MAX_CONTEXT_MESSAGES):]


# --- 4. ФУНКЦИИ УТИЛИТЫ ---
async def delete_temp_file(file_path):
    """Асинхронно удаляет временный аудиофайл."""
    await asyncio.sleep(1)
    if os.path.exists(file_path):
        os.remove(file_path)
        logging.info(f"Временный файл удален: {file_path}")


# --- 5. ОБРАБОТЧИКИ СООБЩЕНИЙ ---

# 5.1. Сброс контекста
@dp.message(F.text == '/start', F.chat.type == 'private')
async def handle_start(message: types.Message):
    user_id = message.from_user.id
    if user_id in user_histories:
        del user_histories[user_id]
    
    await message.reply(
        "Память сброшена. Начинаем с чистого листа! Готов общаться в стиле Дониёра. 👋"
    )


# 5.2. ТЕКСТ -> ТЕКСТ (С памятью)
@dp.message(F.text, F.chat.type == 'private')
async def handle_text_to_text(message: types.Message):
    await bot.send_chat_action(chat_id=message.chat.id, action="typing")
    user_id = message.from_user.id
    
    update_history(user_id, "user", message.text)

    try:
        response = await openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=get_history(user_id),
            temperature=0.8 # Повышаем температуру для более живого ответа
        )
        
        reply_text = response.choices[0].message.content
        
        update_history(user_id, "assistant", reply_text)
        await message.reply(reply_text)
        
    except Exception as e:
        logging.error(f"Ошибка при обработке текста: {e}")
        await message.reply("Извини, Дониёр сейчас занят и не смог ответить текстом. 😥")


# 5.3. ГОЛОС -> ГОЛОС (С памятью и синтезом)
@dp.message(F.voice, F.chat.type == 'private')
async def handle_voice_to_voice(message: types.Message):
    await bot.send_chat_action(chat_id=message.chat.id, action="record_voice") 
    user_id = message.from_user.id
    audio_file_path = None
    
    try:
        # 1. Распознавание речи (Whisper)
        voice_file_info = await bot.get_file(message.voice.file_id)
        voice_downloaded = io.BytesIO()
        await bot.download_file(voice_file_info.file_path, voice_downloaded)
        voice_downloaded.seek(0)
        
        transcript = await openai_client.audio.transcriptions.create(
            model="whisper-1", 
            file=("voice.ogg", voice_downloaded.read(), "audio/ogg"),
        )
        user_text = transcript.text
        logging.info(f"Распознанный текст: {user_text}")

        # 2. Генерация текстового ответа (ChatGPT)
        update_history(user_id, "user", user_text)
        
        response = await openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=get_history(user_id),
            temperature=0.8 # Повышаем температуру для живого стиля
        )
        reply_text = response.choices[0].message.content
        update_history(user_id, "assistant", reply_text)
        
        # 3. Синтез речи (ElevenLabs) - ПРЯМОЙ AIOHTTP ЗАПРОС
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE_ID}"
        headers = {
            "Accept": "audio/mpeg",
            "xi-api-key": ELEVENLABS_API_KEY,
            "Content-Type": "application/json"
        }
        data = {
            "text": reply_text,
            "model_id": "eleven_multilingual_v2",
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.75}
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=data) as response:
                if response.status != 200:
                    error_message = await response.text()
                    raise Exception(f"ElevenLabs API Error (Code {response.status}): {error_message}")
                
                # 4. Получение аудио 
                audio_data_bytes = await response.read()
        
        # Сохраняем аудиобайты во временный файл
        audio_file_path = f"response_{message.chat.id}_{message.message_id}.mp3"
        with open(audio_file_path, "wb") as f:
            f.write(audio_data_bytes)
                
        # 4.2 Отправка голосового сообщения
        telegram_file = FSInputFile(audio_file_path)
        await message.answer_voice(telegram_file)
            
        logging.info("Голосовое сообщение (ответ) отправлено.")

    except Exception as e:
        logging.error(f"Ошибка в голосовой логике: {e}")
        await message.reply(f"Извини, я не смог обработать голосовое сообщение. Кажется, Дониёр отвлёкся. 😥")
        
    finally:
        # 5. Очистка
        if audio_file_path and os.path.exists(audio_file_path):
            asyncio.create_task(delete_temp_file(audio_file_path))


# --- 6. ЗАПУСК БОТА ---
if __name__ == '__main__':
    if not TELEGRAM_BOT_TOKEN:
        logging.error("TELEGRAM_BOT_TOKEN не найден. Проверьте файл .env.")
    else:
        logging.info("Запуск бота...")
        dp.run_polling(bot, skip_updates=True)
    else:
        logging.info("Запуск бота...")

        dp.run_polling(bot, skip_updates=True)
