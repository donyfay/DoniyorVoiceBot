import os
import logging
import asyncio
import io
import random
from dotenv import load_dotenv # Убедитесь, что эта библиотека используется, если вы запускаете локально.

import aiohttp 

# Aiogram v3 импорты
from aiogram import Bot, Dispatcher, types, F
from aiogram.fsm.storage.memory import MemoryStorage 
from aiogram.types import FSInputFile, Message
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
logger = logging.getLogger(__name__)

# Инициализация API
dp = Dispatcher(storage=MemoryStorage())
bot = Bot(token=TELEGRAM_BOT_TOKEN)
openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)


# --- 2. КОНСТАНТЫ И ПРОМПТЫ ДЛЯ ПЕРСОНАЛИЗАЦИИ (ОБНОВЛЕННЫЙ БЛОК) ---
# ID девушки для особого стиля общения (должен быть integer)
GIRLFRIEND_ID = 7988234589 

# 2.1. СИСТЕМНЫЙ ПРОМПТ (ДРУГ / ДРУГИЕ ПОЛЬЗОВАТЕЛИ) - (Ваш базовый промпт)
BASE_SYSTEM_PROMPT = """
Ты — это Дониёр. Ты переписываешься в Telegram со своим другом, которого зовут {name}. 
Ты очень современный человек. Твой стиль общения — простой, понятный, дружелюбный, на грубость можешь отвечать грубостью, с использованием современных сленговых слов, вместо улыбки используй скобку, когда смешно пиши ахахаха. 
Твоя цель — быстро и точно отвечать, поддерживая дружеский тон. Твои ответы должны быть краткими, не более 2-3 предложений.
!!!КРИТИЧЕСКАЯ ИНСТРУКЦИЯ!!!: Не задавай вопрос в конце каждого сообщения. Задавай вопрос только один раз каждые два-три твоих ответа (не в каждом сообщении).
"""
# 2.2. СИСТЕМНЫЙ ПРОМПТ (ДЕВУШКА - Узбекская кириллица, Ласковый стиль)
GIRLFRIEND_SYSTEM_PROMPT = """
Ассалому алайкум, менинг гулим. Сен Дониёрсан. Сен ҳозирда Telegram'да суюклинг Зилола билан ёзишяпсан. У дорихонада ишлайди ва сен у билан якинлашмокчисан, севиб колишини хохлайсан. Сен унга ўзингнинг ҳаётинг ва ишинг ҳақида гапириб берасан, аммо энг муҳими — бу у. Сенинг жавобларинг жуда меҳрибон, эҳтиёткор ва шахсий бўлиши керак. Уни "гулим", "асалим" каби сўзлар билан аташ мумкин. Жавобларинг қисқа, 1-2 жумладан ортиқ бўлмасин. Саволларни ҳар 2-3 хабарда бир марта берасан.
"""
# --- КОНЕЦ СИСТЕМНЫХ ПРОМПТОВ ---


# --- 3. ЛОГИКА ПАМЯТИ (ИСТОРИЯ ЧАТА) ---
user_histories = {}
MAX_CONTEXT_MESSAGES = 30

def get_history(user_id):
    """Возвращает историю диалога пользователя (без системного промпта)."""
    if user_id not in user_histories:
        user_histories[user_id] = []
    return user_histories[user_id]

def update_history(user_id, role, content):
    """Обновляет историю и обрезает ее до MAX_CONTEXT_MESSAGES."""
    history = get_history(user_id)
    history.append({"role": role, "content": content})

    if len(history) > MAX_CONTEXT_MESSAGES:
        # Обрезаем, сохраняя только MAX_CONTEXT_MESSAGES последних сообщений
        user_histories[user_id] = history[-(MAX_CONTEXT_MESSAGES):]

def build_openai_messages(user_id, first_name):
    """
    Конструирует финальный список сообщений для OpenAI,
    динамически вставляя системный промпт с именем и стилем.
    """
    
    # 1. Выбор промпта на основе ID (ID пользователя может быть int или str)
    if int(user_id) == GIRLFRIEND_ID:
        system_prompt_template = GIRLFRIEND_SYSTEM_PROMPT
        name = "Зилола" # Принудительное имя для промпта
        logger.info(f"Выбран промпт для Зилолы (ID: {user_id}).")
    else:
        system_prompt_template = BASE_SYSTEM_PROMPT
        name = first_name
        logger.info(f"Выбран базовый промпт для пользователя {user_id}.")

    # 2. Форматирование промпта
    system_prompt = system_prompt_template.format(name=name)
    
    # 3. Построение истории
    dialog_history = get_history(user_id)
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
@dp.business_message(Command("start")) 
async def handle_start_business(message: types.Message):
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
@dp.message(Command("start"), F.chat.type == 'private', F.business_connection_id.not_) 
async def handle_start_private(message: types.Message):
    user_id = message.from_user.id
    if user_id in user_histories:
        del user_histories[user_id]
    
    response_text = "Память сброшена. Начинаем с чистого листа! Готов общаться в стиле Дониёра. 👋"
    
    await message.reply(response_text)


# 5.2. ТЕКСТ -> ТЕКСТ (С памятью, Business Chat)
@dp.business_message(F.text) 
async def handle_text_to_text(message: types.Message):
    
    business_id = message.business_connection_id
    if not business_id or not message.chat.id:
        logger.warning("Пропуск сообщения: невалидные ID (вероятно, служебное).")
        return 
    
    logger.info(f"Получено Business-сообщение от Chat ID: {message.chat.id}. Текст: {message.text[:30]}")
    
    # --- ИЗОЛЯЦИЯ send_chat_action ДЛЯ ИЗБЕЖАНИЯ PEER_ID_INVALID ---
    try:
        await bot.send_chat_action(
            chat_id=message.chat.id, 
            action="typing", 
            business_connection_id=business_id
        )
        logger.info("Отправлено 'typing'...")
    except Exception as e:
        logger.warning(f"Ошибка при отправке chat_action: {e}. Продолжаем выполнение.")
    # --- КОНЕЦ ИЗОЛЯЦИИ ---
    
    user_id = message.from_user.id  
    first_name = message.from_user.first_name or "друг"
    
    try:
        # ОСНОВНАЯ ЛОГИКА (OpenAI)
        update_history(user_id, "user", message.text)
        
        messages_for_openai = build_openai_messages(user_id, first_name) # <-- НОВАЯ ЛОГИКА ПЕРСОНАЛИЗАЦИИ
        
        response = await openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages_for_openai,
            temperature=0.8
        )
        
        reply_text = response.choices[0].message.content
        update_history(user_id, "assistant", reply_text)
        
        # --- ЛОГИКА ЗАДЕРЖКИ (5-60 секунд) ---
        delay_s = random.randint(5, 7)
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
        logger.error(f"Критическая ошибка при работе с OpenAI/отправке сообщения: {e}")
        await bot.send_message(
            business_connection_id=business_id,
            chat_id=message.chat.id,
            text="Извини, Дониёр сейчас занят и не смог ответить текстом. 😥"
        )


# 5.3. ГОЛОС -> ГОЛОС (С памятью и синтезом, Business Chat)
@dp.business_message(F.voice)
async def handle_voice_to_voice(message: types.Message):
    
    business_id = message.business_connection_id
    if not business_id or not message.chat.id:
        logger.warning("Пропуск голосового сообщения: невалидные ID (вероятно, служебное).")
        return 
    
    # --- ИЗОЛЯЦИЯ send_chat_action ДЛЯ ИЗБЕЖАНИЯ PEER_ID_INVALID ---
    try:
        await bot.send_chat_action(
            chat_id=message.chat.id, 
            action="record_voice",
            business_connection_id=business_id
        ) 
        logger.info("Отправлено 'record_voice'...")
    except Exception as e:
        logger.warning(f"Ошибка при отправке chat_action: {e}. Продолжаем выполнение.")
    # --- КОНЕЦ ИЗОЛЯЦИИ ---
    
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
        
        messages_for_openai = build_openai_messages(user_id, first_name) # <-- НОВАЯ ЛОГИКА ПЕРСОНАЛИЗАЦИИ
        
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
        
        # --- ЛОГИКА ЗАДЕРЖКИ (5-60 секунд) ---
        delay_s = random.randint(5, 60)
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
@dp.business_message()
async def handle_unhandled_business_messages(message: types.Message):
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
    
# 5.5. ТЕКСТ В ПРЯМОМ ЛС С БОТОМ (Включен AI, Строгая фильтрация)
# ЭТОТ БЛОК ЗАМЕНЯЕТ ВАШЕ ПЕРЕНАПРАВЛЕНИЕ и позволяет боту отвечать в ЛС
@dp.message(F.text, F.chat.type == 'private', F.business_connection_id.not_) 
async def handle_private_text_ai(message: types.Message):

    logger.info(f"Получено Private-сообщение от Chat ID: {message.chat.id}. Текст: {message.text[:30]}")

    try:
        await bot.send_chat_action(chat_id=message.chat.id, action="typing")
    except Exception as e:
        logger.warning(f"Ошибка при отправке chat_action: {e}. Продолжаем выполнение.")

    user_id = message.from_user.id
    first_name = message.from_user.first_name or "друг"

    try:
        update_history(user_id, "user", message.text)
        messages_for_openai = build_openai_messages(user_id, first_name) # <-- ИСПОЛЬЗУЕТ ЛОГИКУ ЗИЛОЛЫ

        response = await openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages_for_openai,
            temperature=0.8
        )

        reply_text = response.choices[0].message.content
        update_history(user_id, "assistant", reply_text)

        # --- ЛОГИКА ЗАДЕРЖКИ (5-60 секунд) ---
        delay_s = random.randint(5, 60)
        logger.info(f"Задержка перед отправкой ответа в ЛС: {delay_s} секунд.")
        await asyncio.sleep(delay_s)
        # --- КОНЕЦ ЛОГИКИ ЗАДЕРЖКИ ---

        await message.reply(reply_text)

        logger.info(f"Текстовый ответ отправлен в ЛС Chat ID: {message.chat.id}")

    except Exception as e:
        logger.error(f"Критическая ошибка при работе с OpenAI/отправке сообщения в ЛС: {e}")
        await message.reply("Извини, Дониёр сейчас занят и не смог ответить текстом в ЛС. 😥")


# 5.6. ГОЛОС -> ГОЛОС (С памятью, Прямой ЛС, Строгая фильтрация)
# ЭТОТ БЛОК ТАКЖЕ ПОЗВОЛЯЕТ БОТУ ОТВЕЧАТЬ ГОЛОСОМ В ЛС
@dp.message(F.voice, F.chat.type == 'private', F.business_connection_id.not_)
async def handle_private_voice_to_voice(message: types.Message):

    logger.info(f"Получено Private-голосовое сообщение от Chat ID: {message.chat.id}")

    try:
        await bot.send_chat_action(chat_id=message.chat.id, action="record_voice")
    except Exception as e:
        logger.warning(f"Ошибка при отправке chat_action: {e}. Продолжаем выполнение.")

    user_id = message.from_user.id
    first_name = message.from_user.first_name or "друг"
    audio_file_path = None

    try:
        # 1. Распознавание речи
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

        # 2. Генерация текстового ответа
        update_history(user_id, "user", user_text)
        messages_for_openai = build_openai_messages(user_id, first_name) # <-- ИСПОЛЬЗУЕТ ЛОГИКУ ЗИЛОЛЫ

        response = await openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages_for_openai,
            temperature=0.8
        )
        reply_text = response.choices[0].message.content
        update_history(user_id, "assistant", reply_text)

        # 3. Синтез речи
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

        audio_file_path = f"response_{message.chat.id}_{message.message_id}.mp3"
        with open(audio_file_path, "wb") as f:
            f.write(audio_data_bytes)

        telegram_file = FSInputFile(audio_file_path)

        # --- ЛОГИКА ЗАДЕРЖКИ (5-60 секунд) ---
        delay_s = random.randint(5, 60)
        logger.info(f"Задержка перед отправкой голосового ответа в ЛС: {delay_s} секунд.")
        await asyncio.sleep(delay_s)
        # --- КОНЕЦ ЛОГИКИ ЗАДЕРЖКИ ---

        await message.reply_voice(voice=telegram_file)

        logger.info("Голосовое сообщение (ответ) отправлено в ЛС.")

    except Exception as e:
        logger.error(f"Критическая ошибка в голосовой логике в ЛС: {e}")
        await message.reply("Извини, я не смог обработать голосовое сообщение в ЛС. Кажется, Дониёр отвлёкся. 😥")

    finally:
        if audio_file_path and os.path.exists(audio_file_path):
            asyncio.create_task(delete_temp_file(audio_file_path))


# 5.7. НЕОБРАБОТАННЫЕ СООБЩЕНИЯ В ПРИВАТНОМ ЧАТЕ (Строгая фильтрация)
@dp.message(F.chat.type == 'private', F.business_connection_id.not_)
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
        try:
            dp.run_polling(bot, skip_updates=True)
        except Exception as e:
            logger.error(f"Критическая ошибка при запуске Polling: {e}")
