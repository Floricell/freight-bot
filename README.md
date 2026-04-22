# 🚛 Telegram Freight Bot

Бот извлекает адреса **Pick Up** и **Delivery** из PDF (Rate Confirmation),
считает дорожное расстояние через OpenStreetMap/OSRM и рассчитывает ставку за милю.

---

## ⚙️ Установка

### 1. Требования
- Python 3.11+
- pip

### 2. Установить зависимости
```bash
pip install -r requirements.txt
```

### 3. Создать бота в Telegram
1. Напишите [@BotFather](https://t.me/BotFather) в Telegram
2. Отправьте `/newbot` и следуйте инструкциям
3. Скопируйте токен (выглядит как `123456789:AAF...`)

### 4. Добавить бота в групповой чат
1. Откройте чат → добавить участника → найдите вашего бота
2. Дайте боту права **читать сообщения** (обычных прав достаточно)

### 5. Запустить бота
```bash
# Linux / macOS
BOT_TOKEN="ВАШ_ТОКЕН" python bot.py

# Windows (CMD)
set BOT_TOKEN=ВАШ_ТОКЕН
python bot.py

# Windows (PowerShell)
$env:BOT_TOKEN="ВАШ_ТОКЕН"
python bot.py
```

---

## 🤖 Как пользоваться

1. Отправьте PDF в групповой чат (или личку боту)
2. Бот автоматически найдёт адреса и посчитает расстояние
3. Бот спросит вашу ставку — введите число (например `1500`)
4. Получите результат:

```
📦 Результат анализа PDF

🟢 Pick Up:  123 Main St, Chicago, IL 60601
🔴 Delivery: 456 Oak Ave, Dallas, TX 75201
📏 Расстояние: 921.4 миль

💵 Сумма из PDF: $2,300.00
⚡ Ставка (PDF / миль): $2.497/mi

✏️ Ваша ставка: $2,100.00
⚡ Ставка (ваша / миль): $2.28/mi
```

---

## 📋 Поддерживаемые метки в PDF

| Pick Up | Delivery |
|---------|----------|
| Pick Up | Delivery |
| Pickup  | Deliver To |
| Origin  | Destination |
| Ship From | Ship To |
| PU | DEL |
| Loading | Consignee |

---

## 🛠️ Устранение проблем

| Проблема | Решение |
|----------|---------|
| "Не удалось найти адреса" | PDF должен содержать текст (не скан). Проверьте метки. |
| "Не удалось геокодировать" | Адрес слишком сокращён — бот берёт первые строки после метки |
| Бот не отвечает в группе | Убедитесь что у бота отключён **Privacy Mode** (через BotFather → Bot Settings → Group Privacy → Turn off) |

### Отключить Privacy Mode (обязательно для групп!)
1. `/mybots` в BotFather
2. Выберите бота → Bot Settings → Group Privacy
3. Нажмите **Turn off**

---

## 🌐 API

- **Геокодирование:** [Nominatim (OpenStreetMap)](https://nominatim.org/) — бесплатно, без ключа
- **Расстояние:** [OSRM](http://project-osrm.org/) — бесплатно, без ключа, реальные дороги
