#!/usr/bin/env python3
import os, re, io, logging, httpx
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes, ConversationHandler
import pdfplumber

BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
OSRM_URL = "http://router.project-osrm.org/route/v1/driving/{lon1},{lat1};{lon2},{lat2}?overview=false"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)
WAIT_RATE = 1

def extract_text_from_pdf(pdf_bytes):
    text = ""
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            t = page.extract_text()
            if t:
                text += t + "\n"
    return text

def extract_addresses(text):
    lines = [l.strip() for l in text.splitlines()]
    us_state = r'\b[A-Z]{2}\s+\d{5}(?:-\d{4})?\b'

    def find_block(start_kw, stop_kw):
        in_block = False
        block = []
        for line in lines:
            if re.search(r'\b' + start_kw + r'\b', line, re.IGNORECASE):
                in_block = True
                continue
            if in_block:
                if stop_kw and re.search(r'\b' + stop_kw + r'\b', line, re.IGNORECASE):
                    break
                block.append(line)
        clean = []
        for l in block:
            if re.match(r'\d{1,2}/\d{1,2}/\d{2,4}', l): continue
            if re.match(r'^[\d\-\(\)\s\.]+$', l): continue
            if re.match(r'^(PICKUP|DELIVERY|DELIVER|ADDRESS|CONTACT|BETWEEN|PICKUP BETWEEN|DELIVER BETWEEN|AGREED)$', l, re.IGNORECASE): continue
            if not l: continue
            clean.append(l)
        addr_lines = []
        for l in clean:
            addr_lines.append(l)
            if re.search(us_state, l):
                break
        return ' '.join(addr_lines) if addr_lines else None

    pickup = find_block('PICKUP', 'DELIVERY')
    delivery = find_block('DELIVERY', 'AGREED')

    if not pickup or not delivery:
        candidates = []
        for i, line in enumerate(lines):
            if re.search(us_state, line):
                start = max(0, i - 2)
                addr = ' '.join(lines[start:i+1])
                candidates.append(addr)
        if len(candidates) >= 1 and not pickup:
            pickup = candidates[0]
        if len(candidates) >= 2 and not delivery:
            delivery = candidates[1]

    return pickup, delivery

def extract_amount(text):
    found = []
    for pat in [r'TOTAL[^\d$]{0,30}\$?\s*([\d,]+\.?\d*)', r'BASE[^\d$]{0,30}\$?\s*([\d,]+\.?\d*)', r'\$\s*([\d,]+\.\d{2})']:
        for m in re.finditer(pat, text, re.IGNORECASE):
            try:
                v = float(m.group(1).replace(',', ''))
                if v > 50: found.append(v)
            except: pass
    return max(found) if found else None

async def geocode(address):
    params = {"q": address, "format": "json", "limit": 1, "countrycodes": "us"}
    headers = {"User-Agent": "TelegramFreightBot/1.0"}
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            r = await client.get(NOMINATIM_URL, params=params, headers=headers)
            data = r.json()
            if data: return float(data[0]["lat"]), float(data[0]["lon"])
        except Exception as e:
            logger.warning(f"Geocode error: {e}")
    return None

async def get_distance_miles(lat1, lon1, lat2, lon2):
    url = OSRM_URL.format(lat1=lat1, lon1=lon1, lat2=lat2, lon2=lon2)
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            r = await client.get(url)
            data = r.json()
            if data.get("code") == "Ok":
                return round(data["routes"][0]["distance"] / 1609.344, 1)
        except Exception as e:
            logger.warning(f"OSRM error: {e}")
    return None

def fmt(pickup, delivery, dist, pdf_amt, manual):
    lines = ["📦 *Результат анализа PDF*", "", f"🟢 *Pick Up:* `{pickup}`", f"🔴 *Delivery:* `{delivery}`", f"📏 *Расстояние:* `{dist} миль`"]
    if pdf_amt:
        lines += ["", f"💵 *Сумма из PDF:* `${pdf_amt:,.2f}`", f"⚡ *Rate/mile (PDF):* `${round(pdf_amt/dist,3)}/mi`"]
    if manual:
        lines += ["", f"✏️ *Ваша ставка:* `${manual:,.2f}`", f"⚡ *Rate/mile (ваша):* `${round(manual/dist,3)}/mi`"]
    return "\n".join(lines)

async def start(update, context):
    await update.message.reply_text("👋 Отправьте PDF с Rate Confirmation!")

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if not doc or doc.mime_type != "application/pdf":
        await update.message.reply_text("⚠️ Отправьте PDF файл.")
        return ConversationHandler.END

    await update.message.reply_text("⏳ Читаю PDF...")
    file = await context.bot.get_file(doc.file_id)
    pdf_bytes = await file.download_as_bytearray()

    try:
        text = extract_text_from_pdf(bytes(pdf_bytes))
    except:
        await update.message.reply_text("❌ Не удалось прочитать PDF.")
        return ConversationHandler.END

    pickup, delivery = extract_addresses(text)

    if not pickup or not delivery:
        await update.message.reply_text("⚠️ Не удалось найти адреса Pick Up / Delivery.")
        return ConversationHandler.END

    await update.message.reply_text(f"📍 Найдено:\n🟢 `{pickup}`\n🔴 `{delivery}`\n\n⏳ Считаю расстояние...", parse_mode="Markdown")

    c1 = await geocode(pickup)
    c2 = await geocode(delivery)

    if not c1:
        await update.message.reply_text(f"❌ Не могу найти на карте:\n`{pickup}`", parse_mode="Markdown")
        return ConversationHandler.END
    if not c2:
        await update.message.reply_text(f"❌ Не могу найти на карте:\n`{delivery}`", parse_mode="Markdown")
        return ConversationHandler.END

    dist = await get_distance_miles(c1[0], c1[1], c2[0], c2[1])
    if not dist:
        await update.message.reply_text("❌ Не удалось получить расстояние.")
        return ConversationHandler.END

    pdf_amt = extract_amount(text)
    context.user_data.update({"pickup": pickup, "delivery": delivery, "distance": dist, "pdf_amount": pdf_amt})

    await update.message.reply_text(fmt(pickup, delivery, dist, pdf_amt, None), parse_mode="Markdown")
    await update.message.reply_text("✏️ Введите *вашу ставку* ($) или /skip", parse_mode="Markdown")
    return WAIT_RATE

async def handle_rate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        rate = float(update.message.text.strip().replace("$","").replace(",",""))
    except:
        await update.message.reply_text("⚠️ Введите число, например: `1500`", parse_mode="Markdown")
        return WAIT_RATE
    d = context.user_data
    await update.message.reply_text(fmt(d["pickup"], d["delivery"], d["distance"], d["pdf_amount"], rate), parse_mode="Markdown")
    return ConversationHandler.END

async def skip_rate(update, context):
    await update.message.reply_text("✅ Готово!")
    return ConversationHandler.END

async def cancel(update, context):
    await update.message.reply_text("❌ Отменено.")
    return ConversationHandler.END

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Document.PDF, handle_document)],
        states={WAIT_RATE: [CommandHandler("skip", skip_rate), MessageHandler(filters.TEXT & ~filters.COMMAND, handle_rate)]},
        fallbacks=[CommandHandler("cancel", cancel)],
        per_chat=False, per_user=True,
    )
    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv)
    logger.info("🤖 Бот запущен!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
