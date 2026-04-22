#!/usr/bin/env python3
"""
Telegram Bot — извлекает адреса Pick Up / Delivery из PDF,
считает расстояние через OSRM и вычисляет ставку за милю.
"""

import os
import re
import io
import logging
import asyncio
import httpx

from telegram import Update
from telegram.ext import (
    Application,
    MessageHandler,
    CommandHandler,
    filters,
    ContextTypes,
    ConversationHandler,
)

import pdfplumber

# ──────────────────────────────────────────────
# Конфигурация
# ──────────────────────────────────────────────
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")

OSRM_URL = "http://router.project-osrm.org/route/v1/driving/{lon1},{lat1};{lon2},{lat2}?overview=false"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Состояния ConversationHandler
WAIT_RATE = 1


# ──────────────────────────────────────────────
# Вспомогательные функции
# ──────────────────────────────────────────────

def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    """Извлекает весь текст из PDF-файла."""
    text = ""
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:
                text += page_text + "\n"
    return text


def find_address_after_label(text: str, labels: list[str]) -> str | None:
    """
    Ищет адрес, стоящий после одного из ключевых слов-меток.
    Возвращает первую найденную строку.
    """
    lines = text.splitlines()
    for i, line in enumerate(lines):
        line_up = line.upper()
        for label in labels:
            if label.upper() in line_up:
                # Адрес может быть на той же строке или на следующих
                # Собираем до 3 строк после метки
                candidate_lines = []
                # Берём часть после метки на той же строке
                after = re.split(label, line, flags=re.IGNORECASE, maxsplit=1)
                if len(after) > 1 and after[1].strip():
                    candidate_lines.append(after[1].strip())
                # Добавляем следующие строки
                for j in range(i + 1, min(i + 4, len(lines))):
                    next_line = lines[j].strip()
                    if next_line:
                        candidate_lines.append(next_line)
                    if len(candidate_lines) >= 3:
                        break

                if candidate_lines:
                    # Объединяем и очищаем
                    addr = " ".join(candidate_lines[:2])
                    # Убираем лишние служебные слова в начале
                    addr = re.sub(r'^[:\-\s]+', '', addr).strip()
                    if len(addr) > 5:
                        return addr
    return None


def extract_amount(text: str) -> float | None:
    """
    Ищет денежную сумму в PDF.
    Приоритет: Total / Amount / Rate / Pay.
    """
    # Паттерны вида: Total: $1,234.56 или TOTAL AMOUNT 1234.56
    patterns = [
        r'(?:total[^$\d]{0,20})\$?\s*([\d,]+\.?\d*)',
        r'(?:amount[^$\d]{0,20})\$?\s*([\d,]+\.?\d*)',
        r'(?:rate[^$\d]{0,20})\$?\s*([\d,]+\.?\d*)',
        r'(?:pay[^$\d]{0,20})\$?\s*([\d,]+\.?\d*)',
        r'\$\s*([\d,]+\.?\d{2})',   # любое $X,XXX.XX
    ]
    for pat in patterns:
        match = re.search(pat, text, re.IGNORECASE)
        if match:
            try:
                value = float(match.group(1).replace(',', ''))
                if value > 0:
                    return value
            except ValueError:
                continue
    return None


async def geocode(address: str) -> tuple[float, float] | None:
    """Геокодирует адрес через Nominatim. Возвращает (lat, lon) или None."""
    params = {
        "q": address,
        "format": "json",
        "limit": 1,
        "addressdetails": 0,
    }
    headers = {"User-Agent": "TelegramFreightBot/1.0"}
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.get(NOMINATIM_URL, params=params, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            if data:
                return float(data[0]["lat"]), float(data[0]["lon"])
        except Exception as e:
            logger.warning(f"Geocode error for '{address}': {e}")
    return None


async def get_road_distance_miles(lat1, lon1, lat2, lon2) -> float | None:
    """Запрашивает дорожное расстояние у OSRM. Возвращает мили или None."""
    url = OSRM_URL.format(lat1=lat1, lon1=lon1, lat2=lat2, lon2=lon2)
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") == "Ok":
                meters = data["routes"][0]["distance"]
                miles = meters / 1609.344
                return round(miles, 1)
        except Exception as e:
            logger.warning(f"OSRM error: {e}")
    return None


def format_result(
    pickup: str,
    delivery: str,
    distance_mi: float,
    pdf_amount: float | None,
    manual_rate: float | None,
) -> str:
    """Формирует итоговое сообщение."""
    lines = [
        "📦 *Результат анализа PDF*",
        "",
        f"🟢 *Pick Up:* `{pickup}`",
        f"🔴 *Delivery:* `{delivery}`",
        f"📏 *Расстояние:* `{distance_mi} миль`",
    ]

    if pdf_amount is not None:
        rpm_pdf = round(pdf_amount / distance_mi, 3) if distance_mi else 0
        lines += [
            "",
            f"💵 *Сумма из PDF:* `${pdf_amount:,.2f}`",
            f"⚡ *Ставка (PDF / миль):* `${rpm_pdf}/mi`",
        ]

    if manual_rate is not None:
        rpm_manual = round(manual_rate / distance_mi, 3) if distance_mi else 0
        lines += [
            "",
            f"✏️ *Ваша ставка:* `${manual_rate:,.2f}`",
            f"⚡ *Ставка (ваша / миль):* `${rpm_manual}/mi`",
        ]

    return "\n".join(lines)


# ──────────────────────────────────────────────
# Обработчики Telegram
# ──────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Отправьте PDF с грузом (Rate Confirmation / Load Sheet).\n"
        "Я извлеку адреса Pick Up и Delivery, посчитаю расстояние и ставку за милю."
    )


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получает PDF, парсит адреса и расстояние."""
    doc = update.message.document
    if not doc or doc.mime_type != "application/pdf":
        await update.message.reply_text("⚠️ Пожалуйста, отправьте файл в формате PDF.")
        return ConversationHandler.END

    await update.message.reply_text("⏳ Обрабатываю PDF...")

    # Скачиваем файл
    file = await context.bot.get_file(doc.file_id)
    pdf_bytes = await file.download_as_bytearray()

    # Извлекаем текст
    try:
        text = extract_text_from_pdf(bytes(pdf_bytes))
    except Exception as e:
        logger.error(f"PDF read error: {e}")
        await update.message.reply_text("❌ Не удалось прочитать PDF. Попробуйте другой файл.")
        return ConversationHandler.END

    if not text.strip():
        await update.message.reply_text("❌ PDF не содержит текста (возможно, это скан). Попробуйте текстовый PDF.")
        return ConversationHandler.END

    # Ищем адреса
    pickup_labels  = ["Pick Up", "Pickup", "Origin", "Ship From", "PU", "Loading"]
    delivery_labels = ["Delivery", "Deliver To", "Destination", "Ship To", "DEL", "Consignee"]

    pickup_addr   = find_address_after_label(text, pickup_labels)
    delivery_addr = find_address_after_label(text, delivery_labels)

    if not pickup_addr or not delivery_addr:
        await update.message.reply_text(
            "⚠️ Не удалось найти адреса Pick Up / Delivery.\n"
            "Убедитесь, что PDF содержит метки: *Pick Up*, *Pickup*, *Origin* и *Delivery*, *Ship To* и т.д.",
            parse_mode="Markdown",
        )
        return ConversationHandler.END

    await update.message.reply_text(
        f"📍 Найдено:\n🟢 Pick Up: `{pickup_addr}`\n🔴 Delivery: `{delivery_addr}`\n\n⏳ Геокодирую адреса...",
        parse_mode="Markdown",
    )

    # Геокодирование
    coords_pu  = await geocode(pickup_addr)
    coords_del = await geocode(delivery_addr)

    if not coords_pu:
        await update.message.reply_text(f"❌ Не удалось геокодировать Pick Up: `{pickup_addr}`", parse_mode="Markdown")
        return ConversationHandler.END
    if not coords_del:
        await update.message.reply_text(f"❌ Не удалось геокодировать Delivery: `{delivery_addr}`", parse_mode="Markdown")
        return ConversationHandler.END

    # Расстояние
    distance = await get_road_distance_miles(
        coords_pu[0], coords_pu[1],
        coords_del[0], coords_del[1],
    )

    if not distance:
        await update.message.reply_text("❌ Не удалось получить расстояние от OSRM. Попробуйте позже.")
        return ConversationHandler.END

    # Сумма из PDF
    pdf_amount = extract_amount(text)

    # Сохраняем данные в context для следующего шага
    context.user_data["pickup"]    = pickup_addr
    context.user_data["delivery"]  = delivery_addr
    context.user_data["distance"]  = distance
    context.user_data["pdf_amount"] = pdf_amount

    # Частичный результат
    partial = format_result(pickup_addr, delivery_addr, distance, pdf_amount, None)
    await update.message.reply_text(partial, parse_mode="Markdown")

    # Спрашиваем ручную ставку
    if pdf_amount:
        await update.message.reply_text(
            "✏️ Введите *вашу ставку* ($) для расчёта rate per mile\n"
            "или напишите /skip чтобы пропустить.",
            parse_mode="Markdown",
        )
    else:
        await update.message.reply_text(
            "💵 Сумма из PDF не найдена автоматически.\n"
            "Введите сумму ($) вручную или /skip чтобы пропустить.",
            parse_mode="Markdown",
        )

    return WAIT_RATE


async def handle_rate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Принимает ручную ставку и выводит финальный результат."""
    text = update.message.text.strip().replace("$", "").replace(",", "")
    try:
        manual_rate = float(text)
    except ValueError:
        await update.message.reply_text("⚠️ Введите число, например: `1500` или `1500.00`", parse_mode="Markdown")
        return WAIT_RATE

    pickup    = context.user_data.get("pickup")
    delivery  = context.user_data.get("delivery")
    distance  = context.user_data.get("distance")
    pdf_amount = context.user_data.get("pdf_amount")

    result = format_result(pickup, delivery, distance, pdf_amount, manual_rate)
    await update.message.reply_text(result, parse_mode="Markdown")
    return ConversationHandler.END


async def skip_rate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Пропускает ввод ставки."""
    await update.message.reply_text("✅ Готово! Отправьте следующий PDF.")
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Отменено.")
    return ConversationHandler.END


# ──────────────────────────────────────────────
# Запуск
# ──────────────────────────────────────────────

def main():
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("❌ Установите переменную окружения BOT_TOKEN!")
        return

    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Document.PDF, handle_document)],
        states={
            WAIT_RATE: [
                CommandHandler("skip", skip_rate),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_rate),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_chat=False,   # работает и в группах
        per_user=True,
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv)

    logger.info("🤖 Бот запущен. Ожидаю PDF...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
