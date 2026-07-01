# WebDev Scout — инструкция для сервера

Чеклист всего, что нужно сделать **один раз** при первом подключении к серверу и перед запуском бота.

---

## 1. Требования к серверу

| Компонент | Минимум |
|---|---|
| Python | **3.11+** (`python3 --version`) |
| ОС | Linux (Ubuntu/Debian рекомендуется) или Windows Server |
| RAM | 1 GB+ (Playwright/XHS — лучше 2 GB) |
| Сеть | Исходящий HTTPS (Gemini, Telegram, Reddit, VK, Habr RSS, Google/DDG) |

---

## 2. Загрузка проекта на сервер

```bash
# Пример: через git
git clone <url-репозитория> webdev-scout
cd webdev-scout

# Или скопируйте папку проекта через scp / SFTP
```

---

## 3. Виртуальное окружение + pip (обязательно)

```bash
cd /path/to/webdev-scout

# Создать venv
python3 -m venv .venv

# Активировать (Linux/macOS)
source .venv/bin/activate

# Активировать (Windows PowerShell)
# .venv\Scripts\Activate.ps1

# Обновить pip
pip install --upgrade pip

# Установить все зависимости проекта
pip install -r requirements.txt
```

### Что ставит `requirements.txt`

| Пакет | Назначение |
|---|---|
| `telethon` | Telegram-парсер |
| `praw` | Reddit |
| `google-genai` | AI-классификатор (Gemini 1.5 Flash) |
| `pydantic`, `pydantic-settings` | конфиг и модели |
| `aiosqlite` | SQLite БД |
| `aiohttp` | VK API |
| `httpx`, `beautifulsoup4` | Google Radar — загрузка страниц |
| `googlesearch-python` | поиск через Google |
| `duckduckgo-search` | fallback при 429/CAPTCHA от Google |
| `playwright` | Boards, Naver, Xiaohongshu (Playwright) |
| `playwright-stealth` | Маскировка headless Chromium под реальный браузер |
| `python-dotenv` | чтение `.env` |

---

## 4. Playwright (только если нужен Xiaohongshu)

По умолчанию XHS **выключен** (`XHS_ENABLED=false`). Если не планируете парсить 小红书 — этот шаг можно пропустить.

```bash
# После pip install -r requirements.txt
playwright install chromium
playwright install-deps chromium   # Linux

# Stealth-патчи ставятся через pip (playwright-stealth), отдельной установки не нужно
```

---

## 5. Файл `.env` — ключи и настройки

```bash
cp .env.example .env
nano .env   # или vim / любой редактор
```

### Обязательно для работы AI

Получить ключ: https://aistudio.google.com/apikey

```env
GEMINI_API_KEY=your_gemini_api_key
GEMINI_MODEL=gemini-1.5-flash
```

### Telegram (хотя бы один источник TG)

Получить на https://my.telegram.org → API development tools

```env
TG_API_ID=12345678
TG_API_HASH=your_api_hash_here
TELEGRAM_SESSION=lead_parser_session
```

> Поддерживаются алиасы `TELEGRAM_API_ID` / `TELEGRAM_API_HASH` — но в `.env.example` используются `TG_API_ID` / `TG_API_HASH`.

### Reddit (опционально)

https://www.reddit.com/prefs/apps → создать app типа «script»

```env
REDDIT_CLIENT_ID=...
REDDIT_CLIENT_SECRET=...
REDDIT_USER_AGENT=WebDevScoutBot/1.0 by /u/ваш_ник
```

### VK (опционально)

https://vk.com/dev → access token с доступом к `groups`, `wall`

```env
VK_API_TOKEN=...
```

### Google Radar (включён по умолчанию)

```env
GOOGLE_RADAR_ENABLED=true
GOOGLE_SEARCH_DELAY=10
GOOGLE_RESULTS_PER_QUERY=8
GOOGLE_RECENCY_HOURS=48
GOOGLE_FETCH_TIMEOUT=15
```

### Xiaohongshu (опционально)

```env
XHS_ENABLED=false
XHS_PAGE_DELAY=3
XHS_POLL_DELAY=5
```

### Общие настройки

```env
DB_PATH=leads.db
POLL_INTERVAL_SECONDS=300
LOG_LEVEL=INFO
```

---

## 6. Первый запуск Telegram (авторизация сессии)

При **первом** запуске Telethon запросит номер телефона и код из Telegram. Сессия сохранится в файл `{TELEGRAM_SESSION}.session` — его нужно хранить и не удалять.

```bash
source .venv/bin/activate
python main.py
```

Введите:
1. Номер телефона (международный формат, например `+374...`)
2. Код из Telegram
3. Пароль 2FA (если включён)

После успешной авторизации можно остановить (`Ctrl+C`) и настроить автозапуск (шаг 8).

---

## 7. Что происходит при первом запуске

1. **SQLite** — создаётся `leads.db` с таблицами `leads` и `discovered_chats`
2. **Seed-каналы** — если `discovered_chats` пустая, автоматически добавляются каналы из `STARTING_TELEGRAM_CHANNELS` (config.py) — парсинг TG начинается сразу, без ожидания auto-discovery
3. **Парсеры** — запускаются только те, для которых заполнены ключи в `.env`
4. **Квалифицированные лиды** — выводятся в консоль в реальном времени

---

## 8. Запуск в фоне (systemd, Linux)

Создайте `/etc/systemd/system/webdev-scout.service`:

```ini
[Unit]
Description=WebDev Scout Lead Parser
After=network.target

[Service]
Type=simple
User=your_user
WorkingDirectory=/path/to/webdev-scout
Environment=PATH=/path/to/webdev-scout/.venv/bin
ExecStart=/path/to/webdev-scout/.venv/bin/python main.py
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable webdev-scout
sudo systemctl start webdev-scout
sudo systemctl status webdev-scout

# Логи
journalctl -u webdev-scout -f
```

---

## 9. Быстрая шпаргалка (копировать целиком)

```bash
# === Первичная настройка на сервере ===
cd /path/to/webdev-scout
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# Только для Xiaohongshu:
# playwright install chromium
# playwright install-deps chromium

cp .env.example .env
nano .env          # заполнить ключи

python main.py     # первый раз — авторизация Telegram

# === Дальнейшие запуски ===
source .venv/bin/activate
python main.py
```

---

## 10. Частые проблемы

| Проблема | Решение |
|---|---|
| `No parsers active` | Заполните хотя бы один блок ключей в `.env` (TG, Reddit, VK) или включите Habr / Google Radar / Playwright-парсеры |
| Google 429 / CAPTCHA | Бот автоматически переключится на DuckDuckGo; задержка между запросами — **10 сек** |
| `FloodWaitError` (Telegram) | Бот ждёт автоматически; не уменьшайте `TG_JOIN_DELAY_*` |
| `playwright not installed` | `pip install playwright && playwright install chromium` |
| Старая схема `leads.db` | Удалите или переименуйте `leads.db` перед первым запуском новой версии |
| Нет лидов из TG сразу | Seed-каналы в БД есть, но для приватных групп нужен join (до 5/день) |

---

## 11. Файлы, которые не коммитить в git

```
.env
*.session
*.session-journal
leads.db
.venv/
```

Добавьте их в `.gitignore`, если используете git.

---

## 12. Активные модули парсера

| Модуль | Файл | Нужен ключ |
|---|---|---|
| Telegram | `tg_parser.py` | `TG_API_ID`, `TG_API_HASH` |
| Reddit | `reddit_parser.py` | `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET` |
| Google Radar | `google_radar_parser.py` | не нужен (работает без API-ключа) |
| Habr Career | `habr_parser.py` | не нужен (HTTP scrape) |
| Behance Jobs | `behance_parser.py` | Playwright |
| Boards | `boards_parser.py` | Playwright |
| Naver | `naver_parser.py` | Playwright |
| VK | `vk_parser.py` | `VK_API_TOKEN` |
| Xiaohongshu | `xiaohongshu_parser.py` | Playwright + `XHS_ENABLED=true` |

AI-классификация (`ai_classifier.py`) требует `GEMINI_API_KEY` для всех источников.
