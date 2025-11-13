import os
import logging
import asyncio
import io
import json # Добавлено для отладки, если нужно
from dotenv import load_dotenv

# ДОБАВЛЕНО: Библиотека для прямого асинхронного HTTP-запроса
import aiohttp 

# Aiogram v3 импорты
from aiogram import Bot, Dispatcher, types, F
from aiogram.fsm.storage.memory import MemoryStorage 
from aiogram.types import FSInputFile 
from aiogram.filters import Command # Добавлено для команды /start
from aiogram.enums import ParseMode # Добавлено для форматирования

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
- "ага сейчас гляну )"
- "да норм всё, чего ты )"
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


# --- 5. ОБРАБОТЧИКИ СООБЩЕНИЙ (ИСПРАВЛЕНЫ ДЛЯ BUSINESS-АККАУНТА) ---

# 5.1. Сброс контекста
# Команда /start может прийти как обычное сообщение, так и как business_message.
# Проще всего обрабатывать ее через dp.message и добавить проверку business_connection_id
@dp.message(Command("start"), F.chat.type == 'private')
async def handle_start(message: types.Message):
    user_id = message.from_user.id
    if user_id in user_histories:
        del user_histories[user_id]
    
    response_text = "Память сброшена. Начинаем с чистого листа! Готов общаться в стиле Дониёра. 👋"
    
    # КРИТИЧЕСКОЕ ИЗМЕНЕНИЕ: Ответ должен учитывать Business-подключение
    if message.business_connection_id:
        await bot.send_message(
            business_connection_id=message.business_connection_id,
            chat_id=message.chat.id,
            text=response_text
        )
    else:
        await message.reply(response_text)


# 5.2. ТЕКСТ -> ТЕКСТ (С памятью)
@dp.business_message(F.text) # ИСПРАВЛЕНО: Теперь ловит Business-сообщения
async def handle_text_to_text(message: types.Message):
    
    # КРИТИЧЕСКАЯ ПРОВЕРКА
    business_id = message.business_connection_id
    if not business_id:
        logging.error("Business connection ID is missing for text message.")
        return # Игнорируем, если это не Business-сообщение (хотя фильтр уже должен отсеять)
    
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
        
        # КРИТИЧЕСКОЕ ИЗМЕНЕНИЕ: Используем business_connection_id для ответа
        await bot.send_message(
            business_connection_id=business_id,
            chat_id=message.chat.id,
            text=reply_text
        )
        
        logging.info(f"Текстовый ответ отправлен через Business ID: {business_id}")
        
    except Exception as e:
        logging.error(f"Ошибка при обработке текста в Business-чате: {e}")
        # Ответ тоже должен идти через Business-ID
        await bot.send_message(
            business_connection_id=business_id,
            chat_id=message.chat.id,
            text="Извини, Дониёр сейчас занят и не смог ответить текстом. 😥"
        )


# 5.3. ГОЛОС -> ГОЛОС (С памятью и синтезом)
@dp.business_message(F.voice) # ИСПРАВЛЕНО: Теперь ловит Business-сообщения с голосом
async def handle_voice_to_voice(message: types.Message):
    
    # КРИТИЧЕСКАЯ ПРОВЕРКА
    business_id = message.business_connection_id
    if not business_id:
        logging.error("Business connection ID is missing for voice message.")
        return
    
    await bot.send_chat_action(chat_id=message.chat.id, action="record_voice") 
    user_id = message.from_user.