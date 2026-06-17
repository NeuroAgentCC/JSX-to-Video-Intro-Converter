# JSX to Video Intro Converter

## Что это
Автоматизированный пайплайн для конвертации React/JSX анимаций (сгенерированных дизайн-инструментами) в MP4-видео.

## Структура проекта
```
JSX в MP4/
├── Input/       ← сюда кладутся входящие ZIP-архивы
├── Output/         ← сюда попадают результаты
│   └── <имя архива>/
│       ├── <Версия 1>.mp4
│       ├── <Версия 2>.mp4
│       └── <имя архива>.zip   ← исходник перемещается сюда
├── convert.py      ← основной скрипт
├── CLAUDE.md
└── README.md
```

## Как запустить
```bash
python3 convert.py
```

## Что делает convert.py
1. Ищет все `.zip` в папке `Input`
2. Для каждого архива:
   - Распаковывает во временную папку
   - Находит все HTML-файлы (каждый HTML = отдельная версия анимации)
   - Для каждого HTML определяет длительность через парсинг подключённого app-JSX файла (поле `"duration"`)
   - Поднимает локальный HTTP-сервер (порт 8787)
   - Записывает анимацию через Playwright (headless Chromium) в WebM
   - Конвертирует WebM → MP4 (H.264, выбранное разрешение, 25fps) через imageio-ffmpeg
   - Называет MP4 по имени HTML-файла без расширения
   - Создаёт папку `Output/<имя архива>/`, кладёт туда MP4 и перемещает ZIP
3. Удаляет временные файлы

## Формат входящих архивов
ZIP должен содержать:
- Один или несколько `.html` файлов (версии анимации)
- JSX-файлы: `animations.jsx`, `scene.jsx`, `tweaks-panel.jsx`, `app.jsx` и/или `compact-app.jsx`
- Папку `uploads/` с ассетами (логотип и т.п.)

HTML загружает React 18 и Babel с unpkg CDN — нужен интернет при первом запуске.

## Зависимости (уже установлены)
- `playwright` + Chromium — запись браузера
- `imageio-ffmpeg` — конвертация WebM → MP4

## Ключевые параметры в convert.py
| Параметр | Значение | Описание |
|---|---|---|
| `LOAD_WAIT` | 4.0с | Ожидание загрузки шрифтов и CDN |
| `RECORD_EXTRA` | 0.5с | Буфер после окончания анимации |
| `PORT` | 8787 | Порт локального HTTP-сервера |

## REQUIRED BEHAVIOR BEFORE RUNNING CONVERSION

**NEVER run convert.py without explicit answers to both questions.**

**LANGUAGE RULE — STRICT:** Detect the language from the user's current message.
- User message in English → ALL questions and responses in English
- User message in Russian → ALL questions and responses in Russian
- Do NOT default to Russian just because this file is in Russian. Always mirror the user's message language exactly.

Before every run ALWAYS:
1. Ask the format question via `AskUserQuestion` — wait for the answer
2. Ask the duration question via `AskUserQuestion` — wait for the answer
3. Only then run: `python3 convert.py --format N --duration S --lang [ru|en]`
   - Use `--lang en` if the user's message was in English
   - Use `--lang ru` if the user's message was in Russian

Format question — 4 options (orientation is auto-detected per HTML file).
Show the user ONLY the name and resolutions — NOT the `--format N` flag:

| Show | label | description | Command |
|---|---|---|---|
| Full HD | `Full HD` | `1920×1080 / 1080×1920` | `--format 1` |
| 2K QHD | `2K QHD` | `2560×1440 / 1440×2560` | `--format 2` |
| 4K UHD | `4K UHD` | `3840×2160 / 2160×3840` | `--format 3` |
| 8K UHD | `8K UHD` | `7680×4320 / 4320×7680` | `--format 4` |

**Format question text:**
- English: "What resolution should the output video be?"
- Russian: "Какое разрешение выходного видео?"

Duration question — ask the user how long the video should be.
Always show exactly 4 options (plus the automatic "Other" for a custom value):

| Option (English) | Option (Russian) | Command |
|---|---|---|
| `From file` | `Из файла` | `--duration 0` |
| `5 sec` | `5 сек` | `--duration 5` |
| `10 sec` | `10 сек` | `--duration 10` |
| `15 sec` | `15 сек` | `--duration 15` |

If the user picks "Other" and types a number — use that as `--duration N`.

**Duration question text:**
- English: "How long should the video be?"
- Russian: "Какова длительность видео?"

This rule applies at all times, including bypass permissions mode.

## Документация
- **README.md** — пользовательская инструкция: как запустить, что нужно. Написана для человека, не трогай без необходимости.
- **CLAUDE.md** — этот файл, контекст для Claude.

## Если что-то пошло не так
- **WebM не создался** — Playwright не смог запустить Chromium. Переустанови: `python3 -m playwright install chromium`
- **Чёрное видео** — увеличь `LOAD_WAIT` (анимация не успела загрузиться)
- **Видео обрывается** — увеличь `RECORD_EXTRA`
- **Панель настроек попала в запись** — скрипт скрывает её через JS, но если структура HTML другая, проверь селектор в `record_one()`
