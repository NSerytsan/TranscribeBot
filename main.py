import os
import boto3
import requests
import subprocess
import asyncio
from uuid import uuid4
from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
AWS_ACCESS_KEY = os.getenv("AWS_ACCESS_KEY")
AWS_SECRET_KEY = os.getenv("AWS_SECRET_KEY")
AWS_REGION = os.getenv("AWS_REGION")
S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME")

s3_client = boto3.client("s3", aws_access_key_id=AWS_ACCESS_KEY, aws_secret_access_key=AWS_SECRET_KEY,
                         region_name=AWS_REGION)
transcribe_client = boto3.client("transcribe", aws_access_key_id=AWS_ACCESS_KEY, aws_secret_access_key=AWS_SECRET_KEY,
                                 region_name=AWS_REGION)

TEMP_DIR = "temp"
os.makedirs(TEMP_DIR, exist_ok=True)

user_languages = {}

LANGUAGES = {
    "🇬🇧 English": "en-US",
    "🇺🇦 Українська": "uk-UA",
    "🇫🇷 Français": "fr-FR",
    "🇩🇪 Deutsch": "de-DE"
}


async def set_language(update: Update, context: CallbackContext):
    """Відправляє клавіатуру з вибором мови"""
    keyboard = [[lang] for lang in LANGUAGES.keys()]
    reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
    await update.message.reply_text("🌍 Виберіть мову аудіо:", reply_markup=reply_markup)


async def save_language(update: Update, context: CallbackContext):
    """Зберігає вибір мови користувача"""
    user_id = update.message.from_user.id
    chosen_lang = update.message.text

    if chosen_lang in LANGUAGES:
        user_languages[user_id] = LANGUAGES[chosen_lang]
        await update.message.reply_text(f"✅ Вибрано мову: {chosen_lang}")
    else:
        await update.message.reply_text("⚠ Будь ласка, виберіть мову з клавіатури!")


def upload_to_s3(file_path, s3_key):
    """Завантажує аудіо у S3"""
    s3_client.upload_file(file_path, S3_BUCKET_NAME, s3_key)
    return f"s3://{S3_BUCKET_NAME}/{s3_key}"


def convert_to_mp3(input_path, output_path):
    """Конвертує аудіофайл у MP3"""
    command = ["ffmpeg", "-i", input_path, "-acodec", "libmp3lame", "-ar", "16000", output_path]
    subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)


def transcribe_audio(s3_uri, job_name, language_code):
    """Запускає транскрипцію з вибраною мовою"""
    transcribe_client.start_transcription_job(
        TranscriptionJobName=job_name,
        Media={"MediaFileUri": s3_uri},
        MediaFormat="mp3",
        LanguageCode=language_code
    )


async def get_transcription_text(job_name):
    """Отримує результат транскрипції"""
    while True:
        job = transcribe_client.get_transcription_job(TranscriptionJobName=job_name)
        status = job["TranscriptionJob"]["TranscriptionJobStatus"]
        if status in ["COMPLETED", "FAILED"]:
            break
        await asyncio.sleep(5)

    if status == "COMPLETED":
        transcript_url = job["TranscriptionJob"]["Transcript"]["TranscriptFileUri"]
        response = requests.get(transcript_url)
        return response.json()["results"]["transcripts"][0]["transcript"]

    return "❌ Помилка транскрипції."


async def handle_audio(update: Update, context):
    """Обробляє отримане аудіо"""
    user_id = update.message.from_user.id

    if user_id not in user_languages:
        await update.message.reply_text("⚠ Спочатку виберіть мову командою /setlang")
        return

    file = update.message.voice or update.message.audio or update.message.video_note
    if not file:
        await update.message.reply_text("⚠ Надішліть голосове або аудіо.")
        return

    file_id = file.file_id
    new_file = await context.bot.get_file(file_id)
    file_path = os.path.join(TEMP_DIR, f"{uuid4()}.mp4")
    mp3_path = file_path.replace(".mp4", ".mp3")

    await new_file.download_to_drive(file_path)

    try:
        # Конвертація у MP3
        convert_to_mp3(file_path, mp3_path)

        # Завантаження в S3
        s3_key = os.path.basename(mp3_path)
        s3_uri = upload_to_s3(mp3_path, s3_key)

        # Запуск транскрипції з вибраною мовою
        job_name = f"transcribe_{uuid4()}"
        transcribe_audio(s3_uri, job_name, user_languages[user_id])

        # Отримання транскрипції
        transcript_text = await get_transcription_text(job_name)

        await update.message.reply_text(f"📝 Транскрипція:\n\n{transcript_text}")

    except Exception as e:
        await update.message.reply_text(f"❌ Сталася помилка: {str(e)}")

    finally:
        # Видалення тимчасових файлів
        if os.path.exists(file_path):
            os.remove(file_path)
        if os.path.exists(mp3_path):
            os.remove(mp3_path)


async def start(update: Update, context):
    """Команда /start"""
    await update.message.reply_text("👋 Привіт! Надішліть аудіо, і я перетворю його у текст.\n"
                                    "📌 Спочатку виберіть мову командою /setlang")


def main():
    """Запуск бота"""
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("setlang", set_language))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, save_language))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO | filters.VIDEO_NOTE, handle_audio))

    print("✅ Бот запущено...")
    app.run_polling()


if __name__ == "__main__":
    main()
