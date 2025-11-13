import os
import logging
import asyncio
import io
import random 
from dotenv import load_dotenv

import aiohttp 

# Aiogram v3 импорты
from aiogram import Bot, Dispatcher, types, F
from aiogram.fsm.storage.memory import MemoryStorage 
from aiogram.types import FSInputFile, Message 
from aiogram.types.business_connection import BusinessMessage # <<< ИСПРАВЛЕНИЕ ЗДЕСЬ
from aiogram.filters import Command 
from aiogram.enums import ParseMode 
from openai import AsyncOpenAI 

# --- 1. ЗАГРУЗКА КЛЮЧЕЙ И КОНФИГУРАЦИЯ ---
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID") 

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__) # Использование logger вместо logging.info/error напрямую

# --- 2. БАЗОВЫЙ СИСТЕМНЫЙ ПРОМПТ (ОЧЕНЬ СТРОГИЙ) ---
# {name} будет заменено на имя собеседника или слово "друг".
BASE_SYSTEM_PROMPT = """
Ты — это Дониёр. Ты переписываешься в Telegram со своим другом, которого зовут {name}.
Твои ответы должны быть дружелюбными, неформальными, с использованием современного сленга.
Твой собеседник — это ты, и вот информация о твоей жизни, которую ты можешь использовать в разговоре, чтобы сделать его более личным:
1. Работа: Менеджер по продажам металлопроката.
2. Увлечения: Психология, плавание, изучение IT.
3. Проекты: В свободное время читаешь книги и занимаешься разработкой ИИ-ассистентов.
4. График/Семья: Работаешь 5/2, на выходных почти всегда занят, часто ездишь к родителям.

Твои ответы должны быть ОЧЕНЬ короткими и лаконичными, не более 1-2 предложений.
!!!КРИТИЧЕСКАЯ ИНСТРУКЦИЯ!!!: Не задавай вопрос в конце каждого сообщения. Задавай вопрос только один раз каждые два-три твоих ответа (случайным образом, не в каждом сообщении).
!!!КРИТИЧЕСКАЯ ИНСТРУКЦИЯ ПО СТИЛЮ!!!: Используй эмодзи ОЧЕНЬ редко и умеренно (не более 1-2 на сообщение).
"""
# --- КОНЕЦ БАЗОВОГО СИСТЕМНОГО ПРОМПТА ---

# Инициализация API
dp = Dispatcher(storage=MemoryStorage())
bot = Bot(token=TELEGRAM_BOT_TOKEN)
openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)


# --- 3. ЛОГИКА ПАМЯТИ (ИСТОРИЯ ЧАТА) ---
user_histories = {}
MAX_CONTEXT_MESSAGES = 40 # Память установлена на 40 сообщений

def get_history(user_id):
    """Инициализирует или возвращает историю диалога (без системного промпта)."""
    if user_id not in user_histories:
        user_histories[user_id] = []
    return user_histories[user_id]

def update_history(user_id, role, content):
    """Обновляет историю и обрезает ее до MAX_CONTEXT_MESSAGES."""
    history = get_history(user_id)
    history.append({"role": role, "content": content})

    # Обрезаем историю, сохраняя только последние сообщения
    if len(history) > MAX_CONTEXT_MESSAGES: 
        user_histories[user_id] = history[-(MAX_CONTEXT_MESSAGES):]

def build_openai_messages(user_id, first_name):
    """
    Конструирует финальный список сообщений для OpenAI,
    динамически вставляя системный промпт с именем.
    """
    # 1. Формируем персонализированный системный промпт
    system_prompt = BASE_SYSTEM_PROMPT.format(name=first_name)
    
    # 2. Получаем историю диалога (без системного промпта)
    dialog_history = get_history(user_id)
    
    # 3. Объединяем: [Системный промпт] + [История]
    messages = [{"role": "system", "content": system_prompt}] + dialog_history
    return messages


# --- 4. ФУНКЦИИ УТИЛИТЫ ---
async def delete_temp_file(file_path):
    """Асинхронно удаляет временный аудиофайл."""
    await asyncio.sleep(1)
    if os.path.exists(file_path):
        os.remove(file_path)
        logger.info(f"Временный файл удален: {file_path}")


# --- 5. ОБРАБОТЧИКИ СООБЩЕНИЙ ---

# 5.1.А. Сброс контекста в Business-чате
@dp.business_message(Command("start"), F.is_outgoing.ne(True))
async def handle_start_business(message: BusinessMessage):
    user_id = message.from_user.id
    if user_id in user_histories:
        del user_histories[user_id]
    
    response_text = "Память сброшена. Начинаем с чистого листа! Готов общаться в стиле Дониёра. 👋"
    
    await bot.send_message(
        business_connection_id=message.business_connection_id,
        chat_id=message.chat.id,
        text=response_text
    )

# 5.1.Б. Сброс контекста в ПРЯМОМ ЛС с ботом
@dp.message(Command("start"), F.chat.type == 'private') 
async def handle_start_private(message: types.Message):
    user_id = message.from_user.id
    if user_id in user_histories:
        del user_histories[user_id]
    
    response_text = "Память сброшена. Начинаем с чистого листа! Готов общаться в стиле Дониёра. 👋"
    
    await message.reply(response_text)


# 5.2. ТЕКСТ -> ТЕКСТ (С памятью, Business Chat)
@dp.business_message(F.text, F.is_outgoing.ne(True))
async def handle_text_to_text(message: BusinessMessage):
    
    business_id = message.business_connection_id
    if not business_id or not message.chat.id:
        logger.warning("Пропуск сообщения: невалидные ID (вероятно, служебное).")
        return 
    
    logger.info(f"Получено Business-сообщение от Chat ID: {message.chat.id}. Текст: {message.text[:30]}")
    
    # --- ИЗОЛЯЦИЯ send_chat_action ДЛЯ ИЗБЕЖАНИЯ PEER_ID_INVALID ---
    try:
        await bot.send_chat_action(chat_id=message.chat.id, action="typing")
        logger.info("Отправлено 'typing'...")
    except Exception as e:
        logger.warning(f"Ошибка при отправке chat_action: {e}. Продолжаем выполнение.")
    # --- КОНЕЦ ИЗОЛЯЦИИ ---
    
    user_id = message.from_user.id
    # Получаем имя собеседника. Если имени нет, используем "друг"
    first_name = message.from_user.first_name or "друг"
    
    try:
        # 1. Записываем сообщение пользователя в историю
        update_history(user_id, "user", message.text)
        
        # 2. Формируем финальный список сообщений с именем для OpenAI
        messages_for_openai = build_openai_messages(user_id, first_name)
        
        # ОСНОВНАЯ ЛОГИКА (OpenAI)
        response = await openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages_for_openai,
            temperature=0.8
        )
        
        reply_text = response.choices[0].message.content
        update_history(user_id, "assistant", reply_text)
        
        # --- ЛОГИКА СЛУЧАЙНОЙ ЗАДЕРЖКИ (5-20 секунд) ---
        delay_s = random.randint(5, 20) 
        logger.info(f"Задержка перед отправкой ответа: {delay_s} секунд.")
        await asyncio.sleep(delay_s)
        # --- КОНЕЦ ЛОГИКИ ЗАДЕРЖКИ ---

        # ОТПРАВКА ОТВЕТА
        await bot.send_message(
            business_connection_id=business_id,
            chat_id=message.chat.id,
            text=reply_text
        )
        
        logger.info(f"Текстовый ответ отправлен через Business ID: {business_id}")
        
    except Exception as e:
        # Этот блок сработает, только если упадет OpenAI или send_message
        logger.error(f"Критическая ошибка при работе с OpenAI/отправке сообщения: {e}")
        await bot.send_message(
            business_connection_id=business_id,
            chat_id=message.chat.id,
            text="Извини, Дониёр сейчас занят и не смог ответить текстом. 😥"
        )


# 5.3. ГОЛОС -> ГОЛОС (С памятью, Business Chat)
@dp.business_message(F.voice, F.is_outgoing.ne(True))
async def handle_voice_to_voice(message: BusinessMessage):
    
    business_id = message.business_connection_id
    if not business_id or not message.chat.id:
        logger.warning("Пропуск голосового сообщения: невалидные ID (вероятно, служебное).")
        return 
    
    # --- ИЗОЛЯЦИЯ send_chat_action ДЛЯ ИЗБЕЖАНИЯ PEER_ID_INVALID ---
    try:
        await bot.send_chat_action(chat_id=message.chat.id, action="record_voice") 
        logger.info("Отправлено 'record_voice'...")
    except Exception as e:
        logger.warning(f"Ошибка при отправке chat_action: {e}. Продолжаем выполнение.")
    # --- КОНЕЦ ИЗОЛЯЦИИ ---
    
    user_id = message.from_user.id
    # Получаем имя собеседника. Если имени нет, используем "друг"
    first_name = message.from_user.first_name or "друг"
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
        logger.info(f"Распознанный текст: {user_text}")

        # 2. Генерация текстового ответа (ChatGPT)
        update_history(user_id, "user", user_text)
        
        # Формируем финальный список сообщений с именем для OpenAI
        messages_for_openai = build_openai_messages(user_id, first_name)
        
        response = await openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages_for_openai,
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
        
        # --- ЛОГИКА СЛУЧАЙНОЙ ЗАДЕРЖКИ (5-20 секунд) ---
        delay_s = random.randint(5, 20) 
        logger.info(f"Задержка перед отправкой голосового ответа: {delay_s} секунд.")
        await asyncio.sleep(delay_s)
        # --- КОНЕЦ ЛОГИКИ ЗАДЕРЖКИ ---

        await bot.send_voice(
            business_connection_id=business_id,
            chat_id=message.chat.id,
            voice=telegram_file
        )
            
        logger.info("Голосовое сообщение (ответ) отправлено.")

    except Exception as e:
        logger.error(f"Критическая ошибка в голосовой логике в Business-чате: {e}")
        await bot.send_message(
            business_connection_id=business_id,
            chat_id=message.chat.id,
            text="Извини, я не смог обработать голосовое сообщение. Кажется, Дониёр отвлёкся. 😥"
        )
        
    finally:
        # 5. Очистка
        if audio_file_path and os.path.exists(audio_file_path):
            asyncio.create_task(delete_temp_file(audio_file_path))


# 5.4. НЕОБРАБОТАННЫЕ СООБЩЕНИЯ В BUSINESS CHAT (стикеры, фото)
@dp.business_message(F.is_outgoing.ne(True))
async def handle_unhandled_business_messages(message: BusinessMessage):
    """Ответ на стикеры, фото и другие необработанные типы сообщений."""
    business_id = message.business_connection_id
    
    if message.content_type not in ['text', 'voice']:
        logger.info(f"Получено нераспознанное Business-сообщение (тип: {message.content_type}). Отправка нейтрального ответа.")
        
        try:
            await bot.send_message(
                business_connection_id=business_id,
                chat_id=message.chat.id,
                text="Не понял, это что? Лучше напиши или отправь голосовое. 😉"
            )
        except Exception as e:
            logger.error(f"Ошибка при отправке нейтрального ответа в Business-чате: {e}")
    
    return

# 5.5. ТЕКСТ В ПРЯМОМ ЛС С БОТОМ (Включен AI)
@dp.message(F.text, F.chat.type == 'private') 
async def handle_private_text_ai(message: types.Message):
    
    logger.info(f"Получено Private-сообщение от Chat ID: {message.chat.id}. Текст: {message.text[:30]}")
    
    # Отправляем "typing"
    try:
        await bot.send_chat_action(chat_id=message.chat.id, action="typing")
        logger.info("Отправлено 'typing'...")
    except Exception as e:
        logger.warning(f"Ошибка при отправке chat_action: {e}. Продолжаем выполнение.")
        
    user_id = message.from_user.id  
    first_name = message.from_user.first_name or "друг"
    
    try:
        # 1. Записываем сообщение пользователя в историю
        update_history(user_id, "user", message.text)
        
        # 2. Формируем финальный список сообщений
        messages_for_openai = build_openai_messages(user_id, first_name)
        
        # ОСНОВНАЯ ЛОГИКА (OpenAI)
        response = await openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages_for_openai,
            temperature=0.8
        )
        
        reply_text = response.choices[0].message.content
        update_history(user_id, "assistant", reply_text)
        
        # --- ЛОГИКА СЛУЧАЙНОЙ ЗАДЕРЖКИ (5-20 секунд) ---
        delay_s = random.randint(5, 20) 
        logger.info(f"Задержка перед отправкой ответа в ЛС: {delay_s} секунд.")
        await asyncio.sleep(delay_s)
        # --- КОНЕЦ ЛОГИКИ ЗАДЕРЖКИ ---

        # ОТПРАВКА ОТВЕТА
        await message.reply(reply_text)
        
        logger.info(f"Текстовый ответ отправлен в ЛС Chat ID: {message.chat.id}")
        
    except Exception as e:
        logger.error(f"Критическая ошибка при работе с OpenAI/отправке сообщения в ЛС: {e}")
        await message.reply("Извини, Дониёр сейчас занят и не смог ответить текстом в ЛС. 😥")


# 5.6. ГОЛОС -> ГОЛОС (С памятью, Прямой ЛС)
@dp.message(F.voice, F.chat.type == 'private')
async def handle_private_voice_to_voice(message: types.Message):
    
    logger.info(f"Получено Private-голосовое сообщение от Chat ID: {message.chat.id}")
    
    # Отправляем 'record_voice'
    try:
        await bot.send_chat_action(chat_id=message.chat.id, action="record_voice") 
        logger.info("Отправлено 'record_voice'...")
    except Exception as e:
        logger.warning(f"Ошибка при отправке chat_action: {e}. Продолжаем выполнение.")
    
    user_id = message.from_user.id
    first_name = message.from_user.first_name or "друг"
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
        logger.info(f"Распознанный текст: {user_text}")

        # 2. Генерация текстового ответа (ChatGPT)
        update_history(user_id, "user", user_text)
        messages_for_openai = build_openai_messages(user_id, first_name)
        
        response = await openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages_for_openai,
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
                
                audio_data_bytes = await response.read()
        
        # Сохраняем аудиобайты во временный файл
        audio_file_path = f"response_{message.chat.id}_{message.message_id}.mp3"
        with open(audio_file_path, "wb") as f:
            f.write(audio_data_bytes)
                
        # 4. Отправка голосового сообщения
        telegram_file = FSInputFile(audio_file_path)
        
        # --- ЛОГИКА СЛУЧАЙНОЙ ЗАДЕРЖКИ (5-20 секунд) ---
        delay_s = random.randint(5, 20) 
        logger.info(f"Задержка перед отправкой голосового ответа в ЛС: {delay_s} секунд.")
        await asyncio.sleep(delay_s)
        # --- КОНЕЦ ЛОГИКИ ЗАДЕРЖКИ ---

        await message.reply_voice(voice=telegram_file)
        
        logger.info("Голосовое сообщение (ответ) отправлено в ЛС.")

    except Exception as e:
        logger.error(f"Критическая ошибка в голосовой логике в ЛС: {e}")
        await message.reply("Извини, я не смог обработать голосовое сообщение в ЛС. Кажется, Дониёр отвлёкся. 😥")
        
    finally:
        # 5. Очистка
        if audio_file_path and os.path.exists(audio_file_path):
            asyncio.create_task(delete_temp_file(audio_file_path))


# 5.7. НЕОБРАБОТАННЫЕ СООБЩЕНИЯ В ПРИВАТНОМ ЧАТЕ 
@dp.message(F.chat.type == 'private')
async def handle_unhandled_private_messages(message: types.Message):
    """Ответ на стикеры, фото и другие необработанные типы сообщений в ЛС."""
    if message.content_type not in ['text', 'voice']:
        logger.info(f"Получено нераспознанное Private-сообщение (тип: {message.content_type}). Отправка нейтрального ответа.")
        
        try:
            await message.reply("Не понял, это что? Лучше напиши или отправь голосовое. 😉")
        except Exception as e:
            logger.error(f"Ошибка при отправке нейтрального ответа в ЛС: {e}")
    
    return

# --- 6. ЗАПУСК БОТА ---
if __name__ == '__main__':
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN не найден. Проверьте файл .env.")
    else:
        logger.info("Запуск бота...")
        dp.run_polling(bot, skip_updates=True)