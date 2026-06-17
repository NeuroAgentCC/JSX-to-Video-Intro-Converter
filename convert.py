#!/usr/bin/env python3
"""
JSX to Video Intro Converter
Кидай ZIP с анимацией в папку "Input", запусти скрипт.
Все версии (HTML-файлы) конвертируются автоматически.
"""

import argparse
import asyncio
import functools
import importlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.parse
import zipfile
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from typing import Optional, Tuple


# ── Проверка зависимостей ─────────────────────────────────────────────────────

def check_dependencies() -> None:
    missing = []
    if importlib.util.find_spec("playwright") is None:
        missing.append("playwright")
    if importlib.util.find_spec("imageio_ffmpeg") is None:
        missing.append("imageio-ffmpeg")

    try:
        home = Path.home()
        chromium_dirs = (
            list(home.glob(".cache/ms-playwright/chromium*")) +
            list(home.glob("Library/Caches/ms-playwright/chromium*"))
        )
        chromium_missing = len(chromium_dirs) == 0
    except Exception:
        chromium_missing = True

    if not missing and not chromium_missing:
        return

    print("=" * 52)
    print(t("  Привет! Для работы скрипта нужно установить:",
            "  Hi! The script needs the following to run:"))
    print()
    for pkg in missing:
        print(f"    • {pkg}  ({t('Python-пакет', 'Python package')})")
    if chromium_missing:
        print(f"    • Chromium  ({t('браузер для записи анимации', 'browser for recording')})")
    print()
    print(t("  Это займёт 1–2 минуты и делается один раз.",
            "  This takes 1–2 minutes and only needs to be done once."))
    print("=" * 52)

    answer = input(t("  Установить всё необходимое? [y/n]: ",
                     "  Install everything required? [y/n]: ")).strip().lower()
    if answer not in ("y", "yes", "д", "да"):
        print(t("Отменено. Запусти скрипт снова когда будешь готов.",
                "Cancelled. Run the script again when you're ready."))
        sys.exit(0)

    print()
    if missing:
        print(t("  Устанавливаю Python-пакеты…", "  Installing Python packages…"))
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--quiet"] + missing
        )
    if chromium_missing:
        print(t("  Устанавливаю Chromium…", "  Installing Chromium…"))
        subprocess.check_call(
            [sys.executable, "-m", "playwright", "install", "chromium"]
        )
    print()
    print(t("  Готово! Продолжаю работу…", "  Done! Continuing…"))
    print()


# ── Язык интерфейса ──────────────────────────────────────────────────────────
LANG: str = "ru"  # переопределяется флагом --lang в main()

def t(ru: str, en: str) -> str:
    return en if LANG == "en" else ru


def ask_language() -> str:
    print()
    print("  Select language / Выберите язык:")
    print()
    print("  1. English")
    print("  2. Русский")
    while True:
        raw = input("  Choice / Выбор (1–2): ").strip()
        if raw == "1":
            return "en"
        if raw == "2":
            return "ru"
        print("  Enter 1 or 2  /  Введите 1 или 2")


# ── Настройки ────────────────────────────────────────────────────────────────
WORK_DIR     = Path(__file__).parent / "Input"
OUTPUT_DIR   = Path(__file__).parent / "Output"
STATS_FILE   = Path(__file__).parent / ".stats.json"
# Stage жёстко вычитает 44px на PlaybackBar при расчёте масштаба (barH = 44).
# Viewport делается выше на BAR_H → Stage считает scale нужного разрешения.
# FFmpeg потом обрезает нижние 44px — плеер уходит, анимация остаётся чистой.
BAR_H        = 44
LOAD_WAIT    = 4.0   # секунд на загрузку (шрифты + React CDN)
RECORD_EXTRA = 0.5   # буфер после окончания анимации
PORT         = 8787

# Уровни качества (название, ширина 16:9, высота 16:9).
# Ориентация (портрет/ландшафт) определяется автоматически по размерам анимации.
QUALITY_LEVELS: list = [
    ("Full HD",  1920, 1080),
    ("2K (QHD)", 2560, 1440),
    ("4K (UHD)", 3840, 2160),
    ("8K (UHD)", 7680, 4320),
]


# ── HTTP-сервер ───────────────────────────────────────────────────────────────

class _SilentHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args): pass


class _ReuseServer(HTTPServer):
    allow_reuse_address = True


def start_server(directory: str) -> HTTPServer:
    # Используем directory= вместо os.chdir() — не трогаем глобальный CWD
    handler = functools.partial(_SilentHandler, directory=directory)
    try:
        server = _ReuseServer(("127.0.0.1", PORT), handler)
    except OSError as e:
        raise RuntimeError(t(
            f"Не удалось запустить HTTP-сервер на порту {PORT}: {e}\n"
            f"Возможно, порт занят другим процессом. Попробуй перезапустить скрипт.",
            f"Failed to start HTTP server on port {PORT}: {e}\n"
            f"The port may be in use. Try restarting the script."
        )) from e
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


# ── Парсинг JSX/HTML ──────────────────────────────────────────────────────────

def find_app_jsx_for_html(html_path: Path) -> Optional[Path]:
    text = html_path.read_text(encoding="utf-8")
    srcs = re.findall(r'src="([^"]+\.jsx)"', text)
    if not srcs:
        return None
    candidate = html_path.parent / srcs[-1]   # последний JSX — точка входа
    return candidate if candidate.exists() else None


def extract_duration(jsx_path: Path) -> float:
    text = jsx_path.read_text(encoding="utf-8")
    m = re.search(r'"duration"\s*:\s*([\d.]+)', text)
    if m:
        return float(m.group(1))
    m = re.search(r'\bDURATION\s*=\s*([\d.]+)', text)
    return float(m.group(1)) if m else 5.0


def find_html_files(directory: Path) -> list:
    # rglob находит HTML и в подпапках, если архив распакован с вложенной структурой
    return sorted(directory.rglob("*.html"))


def parse_variants(html_text: str) -> Optional[list]:
    """Returns [{id, title}, ...] if VARIANTS[] structure found with 2+ entries.
    Supports both 'title' and 'label' as the display name field."""
    m = re.search(r'const\s+VARIANTS\s*=\s*\[([^\]]+)\]', html_text, re.DOTALL)
    if not m:
        return None
    block = m.group(1)
    entries = re.findall(
        r"id\s*:\s*['\"]([^'\"]+)['\"]\s*,\s*(?:title|label)\s*:\s*['\"]([^'\"]+)['\"]",
        block
    )
    return [{"id": vid, "title": vtitle} for vid, vtitle in entries] if len(entries) > 1 else None


def parse_ls_key(html_text: str) -> Optional[str]:
    """Extracts the localStorage key used to persist active variant selection."""
    m = re.search(r"localStorage\.getItem\(['\"]([^'\"]+)['\"]\)", html_text)
    return m.group(1) if m else None


def extract_duration_from_html(html_text: str) -> Optional[float]:
    """Extracts duration={N} from inline Stage component usage in HTML."""
    m = re.search(r'\bduration=\{([\d.]+)\}', html_text)
    return float(m.group(1)) if m else None


def parse_intro_canvas(text: str) -> Optional[Tuple[int, int]]:
    """Parses INTRO_CANVAS = { width: N, height: N } from self-contained JSX files."""
    m = re.search(r'INTRO_CANVAS\s*=\s*\{[^}]*\bwidth\s*:\s*(\d+)[^}]*\bheight\s*:\s*(\d+)', text)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.search(r'INTRO_CANVAS\s*=\s*\{[^}]*\bheight\s*:\s*(\d+)[^}]*\bwidth\s*:\s*(\d+)', text)
    if m:
        return int(m.group(2)), int(m.group(1))
    return None


def parse_intro_length(text: str) -> Optional[float]:
    """Parses INTRO_LENGTH = N from self-contained JSX files."""
    m = re.search(r'\bINTRO_LENGTH\s*=\s*([\d.]+)', text)
    return float(m.group(1)) if m else None


def parse_frame_dimensions(text: str) -> Optional[Tuple[int, int]]:
    """Parses FRAME_W = N and FRAME_H = N from self-contained JSX files."""
    mw = re.search(r'\bFRAME_W\s*=\s*(\d+)', text)
    mh = re.search(r'\bFRAME_H\s*=\s*(\d+)', text)
    if mw and mh:
        return int(mw.group(1)), int(mh.group(1))
    return None


def parse_stage_dimensions(html_text: str) -> Optional[Tuple[int, int]]:
    """Returns (width, height) from Stage component, resolving JS variables if needed."""
    # Try direct numeric props: width={1920} height={1080}
    m = re.search(r'<Stage\b[^>]*\bwidth=\{(\d+)\}[^>]*\bheight=\{(\d+)\}', html_text, re.DOTALL)
    if m:
        return int(m.group(1)), int(m.group(2))
    # height before width
    m = re.search(r'<Stage\b[^>]*\bheight=\{(\d+)\}[^>]*\bwidth=\{(\d+)\}', html_text, re.DOTALL)
    if m:
        return int(m.group(2)), int(m.group(1))
    # Variable refs: width={VW} height={VH}
    mw = re.search(r'<Stage\b[^>]*\bwidth=\{([A-Za-z_]\w*)\}', html_text)
    mh = re.search(r'<Stage\b[^>]*\bheight=\{([A-Za-z_]\w*)\}', html_text)
    if mw and mh:
        wn, hn = mw.group(1), mh.group(1)
        vw = re.search(rf'\b{re.escape(wn)}\s*=\s*(\d+)', html_text)
        vh = re.search(rf'\b{re.escape(hn)}\s*=\s*(\d+)', html_text)
        if vw and vh:
            return int(vw.group(1)), int(vh.group(1))
    return None


def get_video_size(stage_w: int, stage_h: int, quality_idx: int) -> Tuple[int, int]:
    """Returns output (w, h) at the requested quality, auto-detecting portrait/landscape."""
    base_w, base_h = QUALITY_LEVELS[quality_idx][1], QUALITY_LEVELS[quality_idx][2]
    if stage_h > stage_w:
        return base_h, base_w  # портрет: меняем местами
    return base_w, base_h


def safe_filename(name: str) -> str:
    """Sanitizes a string for use as a filename."""
    return re.sub(r'[/:*?"<>|\\]', '-', name).strip()


# Скрываем TweaksPanel (position:fixed, класс .twk-panel) через CSS до загрузки React.
# PlaybackBar убирается через viewport-трюк + FFmpeg-кроп: viewport на BAR_H выше →
# Stage сам считает scale нужного разрешения → бар уходит ниже кадра.
_HIDE_UI_SCRIPT = """
(function () {
  var st = document.createElement('style');
  st.textContent = '.twk-panel { display: none !important; }';
  (document.head || document.documentElement).appendChild(st);
})();
"""

# CSS для скрытия всех известных превью-оверлеев после рендера React.
# Инжектируется через page.evaluate() после LOAD_WAIT — надёжнее init_script.
_HIDE_OVERLAYS_CSS = (
    ".tabs, .ps-switch, .ps-hint, .ps-link "
    "{ display: none !important; }"
)


# ── Playwright-запись ─────────────────────────────────────────────────────────

async def record_one(url: str, duration: float, out_webm: Path,
                     video_w: int, video_h: int,
                     variant_id: Optional[str] = None,
                     variant_title: Optional[str] = None,
                     ls_key: Optional[str] = None) -> None:
    from playwright.async_api import async_playwright

    # Временная папка для WebM — в системном /tmp, не в проекте
    with tempfile.TemporaryDirectory() as video_tmp_str:
        video_tmp = Path(video_tmp_str)

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                args=["--no-sandbox", "--disable-dev-shm-usage"]
            )
            # Viewport выше на BAR_H → Stage считает нужный scale
            # PlaybackBar уходит ниже видимой области → FFmpeg обрежет снизу
            ctx = await browser.new_context(
                viewport={"width": video_w, "height": video_h + BAR_H},
                record_video_dir=str(video_tmp),
                record_video_size={"width": video_w, "height": video_h + BAR_H},
            )
            page = await ctx.new_page()

            # Инжектируем скрипт ДО загрузки страницы — React ещё не существует
            await page.add_init_script(_HIDE_UI_SCRIPT)

            if variant_id is not None and ls_key is not None:
                # Устанавливаем вариант через localStorage до инициализации React
                await page.add_init_script(f"""
(function () {{
  localStorage.setItem({json.dumps(ls_key)}, {json.dumps(variant_id)});
}})();
""")

            # "load" надёжнее "networkidle" — Google Fonts держит соединение
            try:
                await page.goto(url, wait_until="load", timeout=30000)
            except Exception:
                await page.goto(url, wait_until="commit", timeout=15000)

            await page.wait_for_timeout(int(LOAD_WAIT * 1000))

            if variant_id is not None:
                if ls_key is None:
                    # Фолбэк: кликаем по нужной вкладке через JS после рендера React
                    title_js = json.dumps(variant_title or variant_id)
                    await page.evaluate(f"""
(function () {{
  var tabs = document.querySelectorAll('.tab .t');
  for (var i = 0; i < tabs.length; i++) {{
    if (tabs[i].textContent.trim() === {title_js}) {{
      tabs[i].closest('.tab').click(); break;
    }}
  }}
}})();
""")
                    await page.wait_for_timeout(500)  # ждём ремаунт Stage

                # Скрываем превью-оверлеи через CSS после рендера React.
                # Инжектируем напрямую в <head> — гарантированно перебивает display:flex.
                await page.evaluate(f"""
() => {{
  var st = document.createElement('style');
  st.textContent = {json.dumps(_HIDE_OVERLAYS_CSS)};
  document.head.appendChild(st);
}}
""")
                # Ждём срабатывания ResizeObserver внутри Stage (пересчёт scale)
                await page.wait_for_timeout(300)

            await page.wait_for_timeout(int((3 * duration + RECORD_EXTRA) * 1000))
            await ctx.close()   # WebM пишется на диск здесь
            await browser.close()

        webm_files = sorted(video_tmp.glob("*.webm"), key=lambda f: f.stat().st_mtime)
        if not webm_files:
            raise RuntimeError(t("Playwright не создал WebM-файл",
                                  "Playwright did not create a WebM file"))
        shutil.move(str(webm_files[-1]), str(out_webm))
        # video_tmp очищается автоматически при выходе из with


# ── FFmpeg-конвертация ────────────────────────────────────────────────────────

def _find_clean_start(webm: Path, ffmpeg_exe: str, load_wait: float, duration: float) -> float:
    """Ищет фазу 0 (пустой фон, нет объектов) в цикле 2 — гарантированно чистая зона.
    Цикл 1 [load_wait … load_wait+duration] может содержать loading-кадры.
    Цикл 2 [load_wait+duration … load_wait+2*duration] — точно чистая анимация.
    Минимальная дисперсия в чистой зоне = самый однородный кадр = phase 0."""
    fps, fw, fh = 25, 80, 45
    frame_size = fw * fh
    search_start = load_wait + duration  # цикл 2: loading точно закончился

    cmd = [
        ffmpeg_exe, "-i", str(webm), "-ss", str(search_start),
        "-t", str(duration),
        # edgedetect применяется на полном разрешении ДО масштабирования:
        # мелкие символы терминала и тонкие сетевые линии детектируются корректно.
        # crop убирает PlaybackBar, scale уменьшает для скорости обработки в Python.
        "-vf", (f"fps={fps},crop=iw:ih-{BAR_H}:0:0,format=gray,"
                f"edgedetect=low=0.05:high=0.2,"
                f"scale={fw}:{fh}:force_original_aspect_ratio=disable,format=gray"),
        "-f", "rawvideo", "-an", "pipe:1",
    ]
    result = subprocess.run(cmd, capture_output=True)
    data = result.stdout
    frames = [data[i:i + frame_size] for i in range(0, len(data) - frame_size + 1, frame_size)]
    if not frames:
        return load_wait + duration

    # Кадры уже содержат только рёбра (после edgedetect).
    # Сумма пикселей = суммарная энергия рёбер на полном разрешении.
    # Минимум = пустой фон без объектов, независимо от цвета фона.
    best_idx = min(range(len(frames)), key=lambda i: sum(frames[i]))
    return search_start + best_idx / fps


def to_mp4(webm: Path, mp4: Path, duration: float,
           video_w: int, video_h: int) -> None:
    import imageio_ffmpeg
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    start = _find_clean_start(webm, ffmpeg, LOAD_WAIT, duration)
    cmd = [
        ffmpeg, "-y",
        "-i", str(webm),
        "-ss", str(start),   # после -i = точный seeking покадрово
        "-vf", f"crop={video_w}:{video_h}:0:0",
        "-t", str(duration),
        "-c:v", "libx264", "-preset", "fast",
        "-crf", "18", "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        str(mp4),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        mp4.unlink(missing_ok=True)
        raise RuntimeError(f"FFmpeg error:\n{r.stderr[-500:]}")


# ── Основной пайплайн ─────────────────────────────────────────────────────────

def select_zips(zip_files: list) -> list:
    if len(zip_files) == 1:
        print(t(f"Найден архив: {zip_files[0].name}",
                f"Found archive: {zip_files[0].name}"))
        return zip_files

    print(t(f"\nНайдено архивов: {len(zip_files)}",
            f"\nFound {len(zip_files)} archives:"))
    for i, z in enumerate(zip_files, 1):
        print(f"  {i}. {z.name}")
    print()
    print(t("  Введите номера архивов для обработки:",
            "  Enter archive numbers to process:"))
    print(t("  • Enter или 'все'  — обработать все",
            "  • Enter or 'all'   — process all"))
    print(t("  • 1,3              — только первый и третий",
            "  • 1,3              — first and third only"))
    print(t("  • 2-4              — со второго по четвёртый",
            "  • 2-4              — second through fourth"))
    print(t("  • 1,3-5            — первый и с третьего по пятый",
            "  • 1,3-5            — first and third through fifth"))

    raw = input(t("  Выбор: ", "  Choice: ")).strip().lower()

    if not raw or raw in ("все", "all", "в", "a"):
        return zip_files

    indices = set()
    for part in raw.replace(" ", "").split(","):
        if "-" in part:
            bounds = part.split("-", 1)
            try:
                a, b = int(bounds[0]), int(bounds[1])
                indices.update(range(a, b + 1))
            except ValueError:
                print(t(f"  Пропускаю непонятный диапазон: '{part}'",
                        f"  Skipping invalid range: '{part}'"))
        else:
            try:
                indices.add(int(part))
            except ValueError:
                print(t(f"  Пропускаю непонятное значение: '{part}'",
                        f"  Skipping invalid value: '{part}'"))

    valid = sorted(i for i in indices if 1 <= i <= len(zip_files))
    if not valid:
        print(t("  Не выбрано ни одного корректного архива.",
                "  No valid archives selected."))
        return []

    selected = [zip_files[i - 1] for i in valid]
    print(t(f"  Выбрано: {', '.join(z.name for z in selected)}",
            f"  Selected: {', '.join(z.name for z in selected)}"))
    return selected


def ask_quality() -> Tuple[str, int]:
    print()
    print(t("  Качество выходного видео:", "  Output video quality:"))
    print(t("  (ориентация 16:9 / 9:16 определяется автоматически по каждому файлу)",
            "  (orientation 16:9 / 9:16 is detected automatically per file)"))
    print()
    for i, (label, w, h) in enumerate(QUALITY_LEVELS, 1):
        print(f"  {i}. {label:<13} {w}×{h}  {t('или', 'or')}  {h}×{w}")

    while True:
        raw = input(t(f"  Выбор (1–{len(QUALITY_LEVELS)}): ",
                      f"  Choice (1–{len(QUALITY_LEVELS)}): ")).strip()
        try:
            idx = int(raw) - 1
            if 0 <= idx < len(QUALITY_LEVELS):
                return QUALITY_LEVELS[idx][0], idx
        except ValueError:
            pass
        print(t(f"  Введи число от 1 до {len(QUALITY_LEVELS)}.",
                f"  Enter a number from 1 to {len(QUALITY_LEVELS)}."))


def ask_duration() -> Optional[float]:
    print()
    print(t("  Длительность клипа:", "  Clip duration:"))
    print(t("  1. Из файла (автоматически)", "  1. From file (auto-detect)"))
    print(t("  2. 5 сек",                   "  2. 5 sec"))
    print(t("  3. 10 сек",                  "  3. 10 sec"))
    print(t("  4. 15 сек",                  "  4. 15 sec"))
    print(t("  5. Вручную (своя длительность)", "  5. Custom (enter manually)"))

    presets = {"1": None, "2": 5.0, "3": 10.0, "4": 15.0}
    while True:
        raw = input(t("  Выбор (1–5): ", "  Choice (1–5): ")).strip()
        if raw in presets:
            val = presets[raw]
            if val is None:
                print(t("  Длительность: из файла анимации", "  Duration: from animation file"))
            else:
                print(t(f"  Длительность: {int(val)}с", f"  Duration: {int(val)}s"))
            return val
        if raw == "5":
            break
        print(t("  Введи число от 1 до 5.", "  Enter a number from 1 to 5."))

    while True:
        raw = input(t("  Секунд (например 8 или 12.5): ",
                      "  Seconds (e.g. 8 or 12.5): ")).strip().replace(",", ".")
        try:
            val = float(raw)
            if val > 0:
                print(t(f"  Длительность: {val}с", f"  Duration: {val}s"))
                return val
        except ValueError:
            pass
        print(t("  Введи число больше нуля.", "  Enter a number greater than zero."))


async def process_zip(zip_path: Path, duration_override: Optional[float],
                      quality_idx: int) -> None:
    zip_stem = zip_path.stem
    print(f"\n{'─'*52}")
    print(t(f"  Архив:  {zip_path.name}", f"  Archive: {zip_path.name}"))

    # Распаковка в системный /tmp — в «Input» ничего лишнего не появляется
    with tempfile.TemporaryDirectory() as extract_tmp:
        extract_dir = Path(extract_tmp)
        try:
            with zipfile.ZipFile(zip_path) as zf:
                zf.extractall(extract_dir)
        except zipfile.BadZipFile:
            print(t("  ❌  Повреждённый или неверный ZIP-архив, пропускаю",
                    "  ❌  Corrupted or invalid ZIP archive, skipping"))
            return

        conversion_dir = extract_dir / "for_conversion"
        if not conversion_dir.exists():
            print(t("  ℹ️  Папка 'for_conversion' не найдена — конвертирую всё содержимое архива",
                    "  ℹ️  'for_conversion' folder not found — converting all files in archive"))
            conversion_dir = extract_dir

        html_files = find_html_files(conversion_dir)

        # Если в for_conversion/ нет HTML — ищем JSX там и preview-HTML в not_for_conversion/,
        # парим по содержимому (HTML ссылается на JSX через src=).
        jsx_pairs = []   # list of (preview_html_path, jsx_path)
        if not html_files:
            jsx_files = sorted(conversion_dir.glob("*.jsx"))
            not_conversion_dir = extract_dir / "not_for_conversion"
            if jsx_files and not_conversion_dir.exists():
                for preview_html in sorted(not_conversion_dir.rglob("*.html")):
                    try:
                        html_text = preview_html.read_text(encoding="utf-8", errors="ignore")
                    except OSError:
                        continue
                    for jsx in jsx_files:
                        if jsx.name in html_text:
                            jsx_pairs.append((preview_html, jsx))
                            break

            # Новый формат: self-contained JSX без HTML-обёртки.
            # Генерируем минимальный HTML для каждого JSX — React из CDN, автозапуск через #root.
            if not jsx_pairs and jsx_files:
                for jsx in jsx_files:
                    wrapper = conversion_dir / (jsx.stem + ".html")
                    wrapper.write_text(
                        '<!DOCTYPE html><html><head><meta charset="utf-8">'
                        '<script crossorigin src="https://unpkg.com/react@18/umd/react.production.min.js"></script>'
                        '<script crossorigin src="https://unpkg.com/react-dom@18/umd/react-dom.production.min.js"></script>'
                        f'</head><body><div id="root"></div>'
                        f'<script src="{jsx.name}"></script>'
                        '</body></html>',
                        encoding="utf-8"
                    )
                html_files = find_html_files(conversion_dir)

        if not html_files and not jsx_pairs:
            print(t("  ❌  HTML и JSX-файлы не найдены в архиве, пропускаю",
                    "  ❌  No HTML or JSX files found in archive, skipping"))
            return

        # Предварительное сканирование: читаем HTML, детектируем вкладки и размеры
        html_meta = []
        for html in html_files:
            html_text  = html.read_text(encoding="utf-8")
            variants   = parse_variants(html_text)
            stage_dims = parse_stage_dimensions(html_text)
            if stage_dims is None:
                # Self-contained JSX: размеры в FRAME_W/FRAME_H самого JSX-файла
                app_jsx = find_app_jsx_for_html(html)
                if app_jsx:
                    stage_dims = parse_frame_dimensions(app_jsx.read_text(encoding="utf-8"))
            html_meta.append((html, html_text, variants, stage_dims))

        total = sum(len(v) if v else 1 for _, _, v, _ in html_meta) + len(jsx_pairs)
        print(t(f"  Версий: {total}", f"  Versions: {total}"))

        out_folder = OUTPUT_DIR / zip_stem
        out_folder.mkdir(parents=True, exist_ok=True)

        server = start_server(str(extract_dir))
        errors = []

        try:
            for preview_html, jsx_path in jsx_pairs:
                rel = preview_html.relative_to(extract_dir)
                url = f"http://127.0.0.1:{PORT}/{urllib.parse.quote(str(rel))}"

                jsx_text   = jsx_path.read_text(encoding="utf-8")
                stage_dims = parse_intro_canvas(jsx_text)
                if stage_dims:
                    video_w, video_h = get_video_size(stage_dims[0], stage_dims[1], quality_idx)
                else:
                    video_w, video_h = QUALITY_LEVELS[quality_idx][1], QUALITY_LEVELS[quality_idx][2]

                if duration_override is not None:
                    duration = duration_override
                else:
                    d = parse_intro_length(jsx_text)
                    duration = d if d is not None else extract_duration(jsx_path)

                name     = jsx_path.stem
                tmp_webm = out_folder / (name + ".webm")
                out_mp4  = out_folder / (name + ".mp4")

                print(f"\n  [{jsx_path.name}]  {video_w}×{video_h}")
                print(t(f"    Длительность: {duration}с", f"    Duration: {duration}s"))
                print(t("    Записываю…", "    Recording…"))
                try:
                    await record_one(url, duration, tmp_webm, video_w, video_h)
                    print(t("    Конвертирую в MP4…", "    Converting to MP4…"))
                    to_mp4(tmp_webm, out_mp4, duration, video_w, video_h)
                    tmp_webm.unlink(missing_ok=True)
                    print(f"    ✓  {out_mp4.name}")
                except Exception as e:
                    tmp_webm.unlink(missing_ok=True)
                    print(t(f"    ❌  Ошибка: {e}", f"    ❌  Error: {e}"))
                    errors.append((jsx_path.name, str(e)))

            for html, html_text, variants, stage_dims in html_meta:
                rel = html.relative_to(extract_dir)
                url = f"http://127.0.0.1:{PORT}/{urllib.parse.quote(str(rel))}"

                # Определяем выходное разрешение для этого HTML
                if stage_dims:
                    video_w, video_h = get_video_size(stage_dims[0], stage_dims[1], quality_idx)
                else:
                    video_w, video_h = QUALITY_LEVELS[quality_idx][1], QUALITY_LEVELS[quality_idx][2]

                if variants:
                    # HTML с вкладками — записываем каждый вариант отдельно
                    ls_key = parse_ls_key(html_text)
                    if duration_override is not None:
                        duration = duration_override
                    else:
                        d = extract_duration_from_html(html_text)
                        if d is None:
                            app_jsx = find_app_jsx_for_html(html)
                            d = extract_duration(app_jsx) if app_jsx else 5.0
                        duration = d

                    print(t(f"\n  [{html.name}]  — {len(variants)} вариантов  {video_w}×{video_h}",
                            f"\n  [{html.name}]  — {len(variants)} variants  {video_w}×{video_h}"))
                    print(t(f"    Длительность: {duration}с", f"    Duration: {duration}s"))

                    # Если HTML-файлов несколько — добавляем имя файла как префикс,
                    # чтобы варианты с одинаковыми названиями не перезаписывали друг друга
                    html_prefix = (safe_filename(html.stem) + " — ") if len(html_meta) > 1 else ""

                    for v in variants:
                        name     = html_prefix + safe_filename(v["title"])
                        tmp_webm = out_folder / (name + ".webm")
                        out_mp4  = out_folder / (name + ".mp4")

                        print(f"\n    [{v['title']}]")
                        print(t("    Записываю…", "    Recording…"))
                        try:
                            await record_one(url, duration, tmp_webm, video_w, video_h,
                                             variant_id=v["id"], variant_title=v["title"],
                                             ls_key=ls_key)
                            print(t("    Конвертирую в MP4…", "    Converting to MP4…"))
                            to_mp4(tmp_webm, out_mp4, duration, video_w, video_h)
                            tmp_webm.unlink(missing_ok=True)
                            print(f"    ✓  {out_mp4.name}")
                        except Exception as e:
                            tmp_webm.unlink(missing_ok=True)
                            print(t(f"    ❌  Ошибка ({v['title']}): {e}",
                                    f"    ❌  Error ({v['title']}): {e}"))
                            errors.append((v["title"], str(e)))
                else:
                    # Обычный HTML — прежнее поведение
                    app_jsx  = find_app_jsx_for_html(html)
                    duration = duration_override if duration_override is not None else (
                        extract_duration(app_jsx) if app_jsx else 5.0
                    )
                    tmp_webm = out_folder / (html.stem + ".webm")
                    out_mp4  = out_folder / (html.stem + ".mp4")

                    print(f"\n  [{html.name}]  {video_w}×{video_h}")
                    print(t(f"    Длительность: {duration}с", f"    Duration: {duration}s"))
                    print(t("    Записываю…", "    Recording…"))
                    try:
                        await record_one(url, duration, tmp_webm, video_w, video_h)
                        print(t("    Конвертирую в MP4…", "    Converting to MP4…"))
                        to_mp4(tmp_webm, out_mp4, duration, video_w, video_h)
                        tmp_webm.unlink(missing_ok=True)
                        print(f"    ✓  {out_mp4.name}")
                    except Exception as e:
                        tmp_webm.unlink(missing_ok=True)
                        print(t(f"    ❌  Ошибка: {e}", f"    ❌  Error: {e}"))
                        errors.append((html.name, str(e)))
        finally:
            server.shutdown()
            server.server_close()
        # extract_tmp очищается автоматически при выходе из with

    if errors:
        print(t(f"\n  Не удалось конвертировать {len(errors)} файл(а):",
                f"\n  Failed to convert {len(errors)} file(s):"))
        for name, err in errors:
            print(f"    • {name}: {err}")

    shutil.move(str(zip_path), str(out_folder / zip_path.name))
    print(t(f"\n  Архив перемещён в: Output/{zip_stem}/",
            f"\n  Archive moved to: Output/{zip_stem}/"))


def _load_stats() -> dict:
    try:
        return json.loads(STATS_FILE.read_text())
    except Exception:
        return {"runs": 0}


def _save_stats(stats: dict) -> None:
    try:
        STATS_FILE.write_text(json.dumps(stats))
    except Exception:
        pass


def _maybe_show_support(runs: int) -> None:
    if runs != 1 and runs % 10 != 0:
        return
    print(f"\n{'─'*52}")
    print(t(
        "  Понравился инструмент? Поддержи проект: https://neuroagent.cc/donate",
        "  Enjoyed the tool? Support the project:  https://neuroagent.cc/donate",
    ))
    print(t(
        "  Нашёл баг или есть идея:               https://github.com/NeuroAgentCC",
        "  Found a bug or have an idea:            https://github.com/NeuroAgentCC",
    ))
    print(f"{'─'*52}")


def parse_args():
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--format",   type=int,   default=None)   # 1-4
    p.add_argument("--duration", type=float, default=None)   # секунды, 0 = из файла
    p.add_argument("--all",      action="store_true")         # обработать все архивы
    p.add_argument("--lang",     type=str,   default=None)   # ru / en, None = auto
    return p.parse_known_args()[0]


async def main() -> None:
    global LANG
    args = parse_args()
    LANG = args.lang if args.lang in ("ru", "en") else ask_language()
    check_dependencies()

    if not WORK_DIR.exists():
        WORK_DIR.mkdir(parents=True)
        print(t("❌  Папка 'Input' не найдена — создал заново. Положи ZIP и запусти снова.",
                "❌  'Input' folder not found — created it. Add your ZIP and run again."))
        sys.exit(1)

    zip_files = sorted(WORK_DIR.glob("*.zip"))
    if not zip_files:
        print(t("❌  ZIP-архивы не найдены в папке 'Input'",
                "❌  No ZIP archives found in 'Input' folder"))
        sys.exit(1)

    if args.all or args.format is not None:
        selected = zip_files
        if len(zip_files) == 1:
            print(t(f"Найден архив: {zip_files[0].name}",
                    f"Found archive: {zip_files[0].name}"))
        else:
            print(t(f"Найдено архивов: {len(zip_files)} — обрабатываю все",
                    f"Found {len(zip_files)} archives — processing all"))
    else:
        selected = select_zips(zip_files)
    if not selected:
        print(t("Ничего не выбрано, выходим.", "Nothing selected, exiting."))
        sys.exit(0)

    if args.format is not None:
        idx = args.format - 1
        if not (0 <= idx < len(QUALITY_LEVELS)):
            print(t(f"❌  Неверный --format: {args.format}. Допустимо 1–{len(QUALITY_LEVELS)}.",
                    f"❌  Invalid --format: {args.format}. Valid range: 1–{len(QUALITY_LEVELS)}."))
            sys.exit(1)
        quality_label = QUALITY_LEVELS[idx][0]
        quality_idx   = idx
    else:
        print(f"\n{'═'*52}")
        print(t("  НАСТРОЙКИ", "  SETTINGS"))
        print(f"{'═'*52}")
        quality_label, quality_idx = ask_quality()

    if args.duration is not None:
        duration_override = args.duration if args.duration > 0 else None
    else:
        duration_override = ask_duration()

    print(f"\n{'─'*52}")
    dur_str = t(f"{duration_override}с", f"{duration_override}s") if duration_override else t("из файла анимации", "from animation file")
    print(t(f"  Качество:     {quality_label}  (ориентация — по каждому файлу)",
            f"  Quality:      {quality_label}  (orientation detected per file)"))
    print(t(f"  Длительность: {dur_str}", f"  Duration:     {dur_str}"))
    print(f"{'─'*52}")
    print(t("  Начинаю обработку…", "  Starting…"))

    failed_archives = []
    for z in selected:
        try:
            await process_zip(z, duration_override, quality_idx)
        except Exception as e:
            print(t(f"\n  ❌  Критическая ошибка при обработке {z.name}:",
                    f"\n  ❌  Critical error processing {z.name}:"))
            print(f"      {e}")
            failed_archives.append(z.name)

    print(f"\n{'='*52}")
    if failed_archives:
        print(t("Завершено с ошибками. Не удалось обработать:",
                "Completed with errors. Failed to process:"))
        for name in failed_archives:
            print(f"  • {name}")
    else:
        print(t("Готово! Все файлы в папке 'Output'",
                "Done! All files are in the 'Output' folder"))

    stats = _load_stats()
    stats["runs"] = stats.get("runs", 0) + 1
    _save_stats(stats)
    _maybe_show_support(stats["runs"])


if __name__ == "__main__":
    asyncio.run(main())
