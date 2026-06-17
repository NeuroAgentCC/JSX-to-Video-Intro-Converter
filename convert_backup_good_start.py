#!/usr/bin/env python3
"""
JSX → MP4 конвертер
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
    print("  Привет! Для работы скрипта нужно установить:")
    print()
    for pkg in missing:
        print(f"    • {pkg}  (Python-пакет)")
    if chromium_missing:
        print(f"    • Chromium  (браузер для записи анимации)")
    print()
    print("  Это займёт 1–2 минуты и делается один раз.")
    print("=" * 52)

    answer = input("  Установить всё необходимое? [y/n]: ").strip().lower()
    if answer not in ("y", "yes", "д", "да"):
        print("Отменено. Запусти скрипт снова когда будешь готов.")
        sys.exit(0)

    print()
    if missing:
        print("  Устанавливаю Python-пакеты…")
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--quiet"] + missing
        )
    if chromium_missing:
        print("  Устанавливаю Chromium…")
        subprocess.check_call(
            [sys.executable, "-m", "playwright", "install", "chromium"]
        )
    print()
    print("  Output! Продолжаю работу…")
    print()


# ── Настройки ────────────────────────────────────────────────────────────────
WORK_DIR     = Path(__file__).parent / "Input"
OUTPUT_DIR   = Path(__file__).parent / "Output"
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


def start_server(directory: str) -> HTTPServer:
    # Используем directory= вместо os.chdir() — не трогаем глобальный CWD
    handler = functools.partial(_SilentHandler, directory=directory)
    try:
        server = HTTPServer(("127.0.0.1", PORT), handler)
        server.allow_reuse_address = True
    except OSError as e:
        raise RuntimeError(
            f"Не удалось запустить HTTP-сервер на порту {PORT}: {e}\n"
            f"Возможно, порт занят другим процессом. Попробуй перезапустить скрипт."
        ) from e
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
    return [{"id": i, "title": t} for i, t in entries] if len(entries) > 1 else None


def parse_ls_key(html_text: str) -> Optional[str]:
    """Extracts the localStorage key used to persist active variant selection."""
    m = re.search(r"localStorage\.getItem\(['\"]([^'\"]+)['\"]\)", html_text)
    return m.group(1) if m else None


def extract_duration_from_html(html_text: str) -> Optional[float]:
    """Extracts duration={N} from inline Stage component usage in HTML."""
    m = re.search(r'\bduration=\{([\d.]+)\}', html_text)
    return float(m.group(1)) if m else None


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
            raise RuntimeError("Playwright не создал WebM-файл")
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
        ffmpeg_exe, "-ss", str(search_start), "-i", str(webm),
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
    """Если архив один — берём сразу. Иначе спрашиваем какие обрабатывать."""
    if len(zip_files) == 1:
        print(f"Найден архив: {zip_files[0].name}")
        return zip_files

    print(f"\nНайдено архивов: {len(zip_files)}")
    for i, z in enumerate(zip_files, 1):
        print(f"  {i}. {z.name}")
    print()
    print("  Введите номера архивов для обработки:")
    print("  • Enter или 'все'  — обработать все")
    print("  • 1,3              — только первый и третий")
    print("  • 2-4              — со второго по четвёртый")
    print("  • 1,3-5            — первый и с третьего по пятый")

    raw = input("  Выбор: ").strip().lower()

    # Enter или "все/all" → все файлы
    if not raw or raw in ("все", "all", "в", "a"):
        return zip_files

    # Парсим номера и диапазоны
    indices = set()
    for part in raw.replace(" ", "").split(","):
        if "-" in part:
            bounds = part.split("-", 1)
            try:
                a, b = int(bounds[0]), int(bounds[1])
                indices.update(range(a, b + 1))
            except ValueError:
                print(f"  Пропускаю непонятный диапазон: '{part}'")
        else:
            try:
                indices.add(int(part))
            except ValueError:
                print(f"  Пропускаю непонятное значение: '{part}'")

    valid = sorted(i for i in indices if 1 <= i <= len(zip_files))
    if not valid:
        print("  Не выбрано ни одного корректного архива.")
        return []

    selected = [zip_files[i - 1] for i in valid]
    print(f"  Выбрано: {', '.join(z.name for z in selected)}")
    return selected


def ask_quality() -> Tuple[str, int]:
    """Спрашивает уровень качества. Ориентация (16:9/9:16) определяется автоматически."""
    print()
    print("  Качество выходного видео:")
    print("  (ориентация 16:9 / 9:16 определяется автоматически по каждому файлу)")
    print()
    for i, (label, w, h) in enumerate(QUALITY_LEVELS, 1):
        print(f"  {i}. {label:<13} {w}×{h}  или  {h}×{w}")

    while True:
        raw = input(f"  Выбор (1–{len(QUALITY_LEVELS)}): ").strip()
        try:
            idx = int(raw) - 1
            if 0 <= idx < len(QUALITY_LEVELS):
                return QUALITY_LEVELS[idx][0], idx
        except ValueError:
            pass
        print(f"  Введи число от 1 до {len(QUALITY_LEVELS)}.")


def ask_duration() -> Optional[float]:
    """Спрашивает пользователя про длительность. None = брать из файла."""
    print()
    print("  Длительность клипа:")
    print("  1. Определить автоматически из файла анимации")
    print("  2. Указать вручную (в секундах)")

    while True:
        raw = input("  Выбор (1–2): ").strip()
        if raw == "1":
            print("  Длительность: из файла анимации")
            return None
        if raw == "2":
            break
        print("  Введи 1 или 2.")

    while True:
        raw = input("  Секунд (например 8 или 12.5): ").strip().replace(",", ".")
        try:
            val = float(raw)
            if val > 0:
                print(f"  Длительность: {val}с")
                return val
        except ValueError:
            pass
        print("  Введи число больше нуля.")


async def process_zip(zip_path: Path, duration_override: Optional[float],
                      quality_idx: int) -> None:
    zip_stem = zip_path.stem
    print(f"\n{'─'*52}")
    print(f"  Архив:  {zip_path.name}")

    # Распаковка в системный /tmp — в «Input» ничего лишнего не появляется
    with tempfile.TemporaryDirectory() as extract_tmp:
        extract_dir = Path(extract_tmp)
        try:
            with zipfile.ZipFile(zip_path) as zf:
                zf.extractall(extract_dir)
        except zipfile.BadZipFile:
            print("  ❌  Повреждённый или неверный ZIP-архив, пропускаю")
            return

        html_files = find_html_files(extract_dir)
        if not html_files:
            print("  ❌  HTML-файлы не найдены, пропускаю")
            return

        # Предварительное сканирование: читаем HTML, детектируем вкладки и размеры
        html_meta = []
        for html in html_files:
            html_text  = html.read_text(encoding="utf-8")
            variants   = parse_variants(html_text)
            stage_dims = parse_stage_dimensions(html_text)
            html_meta.append((html, html_text, variants, stage_dims))

        total = sum(len(v) if v else 1 for _, _, v, _ in html_meta)
        print(f"  Версий: {total}")

        out_folder = OUTPUT_DIR / zip_stem
        out_folder.mkdir(parents=True, exist_ok=True)

        server = start_server(str(extract_dir))
        errors = []

        try:
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

                    print(f"\n  [{html.name}]  — {len(variants)} вариантов  {video_w}×{video_h}")
                    print(f"    Длительность: {duration}с")

                    # Если HTML-файлов несколько — добавляем имя файла как префикс,
                    # чтобы варианты с одинаковыми названиями не перезаписывали друг друга
                    html_prefix = (safe_filename(html.stem) + " — ") if len(html_meta) > 1 else ""

                    for v in variants:
                        name     = html_prefix + safe_filename(v["title"])
                        tmp_webm = out_folder / (name + ".webm")
                        out_mp4  = out_folder / (name + ".mp4")

                        print(f"\n    [{v['title']}]")
                        print(f"    Записываю…")
                        try:
                            await record_one(url, duration, tmp_webm, video_w, video_h,
                                             variant_id=v["id"], variant_title=v["title"],
                                             ls_key=ls_key)
                            print(f"    Конвертирую в MP4…")
                            to_mp4(tmp_webm, out_mp4, duration, video_w, video_h)
                            tmp_webm.unlink(missing_ok=True)
                            print(f"    ✓  {out_mp4.name}")
                        except Exception as e:
                            tmp_webm.unlink(missing_ok=True)
                            print(f"    ❌  Ошибка ({v['title']}): {e}")
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
                    print(f"    Длительность: {duration}с")
                    print(f"    Записываю…")
                    try:
                        await record_one(url, duration, tmp_webm, video_w, video_h)
                        print(f"    Конвертирую в MP4…")
                        to_mp4(tmp_webm, out_mp4, duration, video_w, video_h)
                        tmp_webm.unlink(missing_ok=True)
                        print(f"    ✓  {out_mp4.name}")
                    except Exception as e:
                        tmp_webm.unlink(missing_ok=True)
                        print(f"    ❌  Ошибка: {e}")
                        errors.append((html.name, str(e)))
        finally:
            server.shutdown()
            server.server_close()
        # extract_tmp очищается автоматически при выходе из with

    if errors:
        print(f"\n  Не удалось конвертировать {len(errors)} файл(а):")
        for name, err in errors:
            print(f"    • {name}: {err}")

    shutil.move(str(zip_path), str(out_folder / zip_path.name))
    print(f"\n  Архив перемещён в: Output/{zip_stem}/")


def parse_args():
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--format",   type=int,   default=None)  # 1-4
    p.add_argument("--duration", type=float, default=None)  # секунды, 0 = из файла
    p.add_argument("--all",      action="store_true")        # обработать все архивы
    return p.parse_known_args()[0]


async def main() -> None:
    args = parse_args()
    check_dependencies()

    if not WORK_DIR.exists():
        WORK_DIR.mkdir(parents=True)
        print(f"❌  Папка 'Input' не найдена — создал заново. Положи ZIP и запусти снова.")
        sys.exit(1)

    zip_files = sorted(WORK_DIR.glob("*.zip"))
    if not zip_files:
        print("❌  ZIP-архивы не найдены в папке 'Input'")
        sys.exit(1)

    # Выбор архивов: через аргумент --all или интерактивно
    if args.all or args.format is not None:
        selected = zip_files
        if len(zip_files) == 1:
            print(f"Найден архив: {zip_files[0].name}")
        else:
            print(f"Найдено архивов: {len(zip_files)} — обрабатываю все")
    else:
        selected = select_zips(zip_files)
    if not selected:
        print("Ничего не выбрано, выходим.")
        sys.exit(0)

    # Качество
    if args.format is not None:
        idx = args.format - 1
        if not (0 <= idx < len(QUALITY_LEVELS)):
            print(f"❌  Неверный --format: {args.format}. Допустимо 1–{len(QUALITY_LEVELS)}.")
            sys.exit(1)
        quality_label = QUALITY_LEVELS[idx][0]
        quality_idx   = idx
    else:
        print(f"\n{'═'*52}")
        print(f"  НАСТРОЙКИ")
        print(f"{'═'*52}")
        quality_label, quality_idx = ask_quality()

    # Длительность
    if args.duration is not None:
        duration_override = args.duration if args.duration > 0 else None
    else:
        duration_override = ask_duration()

    print(f"\n{'─'*52}")
    dur_str = f"{duration_override}с" if duration_override else "из файла анимации"
    print(f"  Качество:     {quality_label}  (ориентация — по каждому файлу)")
    print(f"  Длительность: {dur_str}")
    print(f"{'─'*52}")
    print(f"  Начинаю обработку…")

    failed_archives = []
    for z in selected:
        try:
            await process_zip(z, duration_override, quality_idx)
        except Exception as e:
            print(f"\n  ❌  Критическая ошибка при обработке {z.name}:")
            print(f"      {e}")
            failed_archives.append(z.name)

    print(f"\n{'='*52}")
    if failed_archives:
        print(f"Завершено с ошибками. Не удалось обработать:")
        for name in failed_archives:
            print(f"  • {name}")
    else:
        print(f"Output! Все файлы в папке 'Output'")


if __name__ == "__main__":
    asyncio.run(main())
