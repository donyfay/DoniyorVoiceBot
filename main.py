import os
import logging
import asyncio
import io
import json 
from dotenv import load_dotenv

# ДОБАВЛЕНО: Библиотека для прямого асинхронного HTTP-запроса
import aiohttp 

# Aiogram v3 импорты
from aiogram import Bot, Dispatcher, types, F
from aiogram.fsm.storage.memory import MemoryStorage 
from aiogram.types import FSInputFile 
from aiogram.filters import Command 
from aiogram.enums import ParseMode 
# Импорт AsyncOpenAI (предполагается, что он есть в requirements.txt)
from openai import AsyncOpenAI 

# --- 1. ЗАГРУЗКА КЛЮЧЕЙ И КОНФИГУРАЦИЯ ---
load_dotenv()

# Переменные окружения
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID") 

# ЛОГИКА АВТОРИЗАЦИИ УДАЛЕНА

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- 2. СИСТЕМНЫЙ ПРОМПТ (Свободная двуязычность) ---
SYSTEM_PROMPT = """
Ты — это Дониёр. Ты переписываешься в Telegram со своими друзьями, знакомыми, родственниками и девушками.
Ты не бот, ты реальный человек. Пиши естественно, с эмоциями, иногда с юмором, по-дружески.
Иногда сокращай слова, используй разговорные выражения и эмодзи. Не будь формальным.

**Язык общения:** Ты свободно владеешь **русским** и **узбекским** языками. Ты можешь отвечать на **любом** из этих языков, **не придерживаясь строго** языка входящего сообщения. Как человек, ты можешь иногда ответить на русском, даже если тебе написали на узбекском, или наоборот. Ты также можешь смешивать узбекские и русские слова в одном сообщении (код-свитчинг), если это звучит естественно и по-дружески. Главное — сохранять живой, неформальный стиль Дониёра.

Стиль: лёгкий, уверенный, спокойный. Иногда шути, но не перегибай.
Если пишет девушка — можешь отвечать чуть теплее и мягче.
Если пишет друг — свободнее, с лёгким юмором.
Если пишет родственник — уважительно, спокойно, без флирта.

Не используй слова: "искусственный интеллект", "бот", "как программа", "я не знаю".
Если не уверен в ответе — отвечай нейтрально, с лёгкой неопределённостью, как человек.
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

# --- 4.5. ФИЛЬТРЫ БЕЗОПАСНОСТИ ---
# Вся логика фильтрации удалена

# --- 5. ОБРАБОТЧИКИ СООБЩЕНИЙ ---

# 5.1. Сброс контекста
@dp.message(Command("start"), F.chat.type == 'private') # ФИЛЬТР УДАЛЕН
async def handle_start(message: types.Message):
    user_id = message.from_user.id
    if user_id in user_histories:
        del user_histories[user_id]
    
    response_text = "Память сброшена. Начинаем с чистого листа! Готов общаться в стиле Дониёра. 👋"
    
    # КОРРЕКТНЫЙ ОТВЕТ: Ответ должен учитывать Business-подключение
    if message.business_connection_id:
        await bot.send_message(
            business_connection_id=message.business_connection_id,
            chat_id=message.chat.id,
            text=response_text
        )
    else:
        await message.reply(response_text)


# 5.2. ТЕКСТ -> ТЕКСТ (С памятью)
@dp.business_message(F.text) # ФИЛЬТР УДАЛЕН
async def handle_text_to_text(message: types.Message):
    
    # КРИТИЧЕСКАЯ ПРОВЕРКА
    business_id = message.business_connection_id
    if not business_id:
        logging.error("Business connection ID is missing for text message.")
        return 
    
    await bot.send_chat_action(chat_id=message.chat.id, action="typing")
    user_id = message.from_user.id  

    update_history(user_id, "user", message.text)

    try:
        response = await openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=get_history(user_id),
            temperature=0.8
        )
        
        reply_text = response.choices[0].message.content
        
        update_history(user_id, "assistant", reply_text)
        
        # КОРРЕКТНЫЙ ОТВЕТ: Используем business_connection_id для ответа
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
@dp.business_message(F.voice) # ФИЛЬТР УДАЛЕН
async def handle_voice_to_voice(message: types.Message):
    
    # КРИТИЧЕСКАЯ ПРОВЕРКА
    business_id = message.business_connection_id
    if not business_id:
        logging.error("Business connection ID is missing for voice message.")
        return
    
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
            temperature=0.8 
        )
        reply_text = response.choices[0].message.content
        update_history(user_id, "assistant", reply_text)
        
        # 3. Синтез речи (ElevenLabs) 
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
        
        # КОРРЕКТНЫЙ ОТВЕТ: Используем business_connection_id для ответа
        await bot.send_voice(
            business_connection_id=business_id,
            chat_id=message.chat.id,
            voice=telegram_file
        )
            
        logging.info("Голосовое сообщение (ответ) отправлено.")

    except Exception as e:
        logging.error(f"Ошибка в голосовой логике в Business-чате: {e}")
        # Ответ тоже должен идти через Business-ID
        await bot.send_message(
            business_connection_id=business_id,
            chat_id=message.chat.id,
            text="Извини, я не смог обработать голосовое сообщение. Кажется, Дониёр отвлёкся. 😥"
        )
        
    finally:
        # 5. Очистка
        if audio_file_path and os.path.exists(audio_file_path):
            asyncio.create_task(delete_temp_file(audio_file_path))


# 5.4. ОБРАБОТЧИК ДЛЯ НЕРАСПОЗНАННЫХ СООБЩЕНИЙ (добавлен для обработки стикеров и других типов контента)
@dp.business_message()
async def handle_unhandled_business_messages(message: types.Message):
    """Ответ на стикеры, фото и другие необработанные типы сообщений."""
    business_id = message.business_connection_id
    user_id = message.from_user.id
    
    if message.content_type not in ['text', 'voice']:
        logging.info(f"Получено нераспознанное Business-сообщение от ID: {user_id} (тип: {message.content_type}). Отправка нейтрального ответа.")
        
        # Отправляем нейтральный ответ
        try:
            await bot.send_message(
                business_connection_id=business_id,
                chat_id=message.chat.id,
                text="Не понял, это что? Лучше напиши или отправь голосовое. 😉"
            )
        except Exception as e:
             logging.error(f"Ошибка при отправке нейтрального ответа в Business-чате: {e}")
    
    return


# --- 6. ЗАПУСК БОТА ---
if __name__ == '__main__':
    if not TELEGRAM_BOT_TOKEN:
        logging.error("TELEGRAM_BOT_TOKEN не найден. Проверьте файл .env.")
    else:
        logging.info("Запуск бота...")
        dp.run_polling(bot, skip_updates=True)