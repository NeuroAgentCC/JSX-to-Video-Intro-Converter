# Changelog

Available in: **English** | [Русский](UPDATES_RU.md)

## Version 2 — June 2026

**Key improvements:**

- **Auto-orientation** — no need to pick a separate mobile format. Choose a quality level (Full HD, 2K, 4K, 8K) and the script automatically detects whether the animation is landscape or portrait (9:16) based on its dimensions
- **Clean start frame** — the script now finds the correct starting frame via edge analysis, so the animation begins cleanly without extra pauses or first-cycle artifacts
- **Multiple variants from a single HTML** — if an HTML file contains several animation versions (via `VARIANTS[]`), each one is converted to a separate MP4 automatically
- **Universal archive support** — if the archive contains a `for_conversion/` folder (see the prompt in README), only its contents are converted. If there is no such folder, the script converts everything it finds in the archive

---

## Version 1 — May 2026

First working version of the script.

**Features:**
- Takes ZIP archives from the `Input/` folder
- Finds HTML files inside (each one = a separate animation version)
- Detects duration from the JSX file (`duration` field)
- Records the animation via Playwright (headless Chromium) to WebM
- Converts WebM → MP4 via FFmpeg
- Supports 5 formats: Full HD, 2K, 4K, 8K, Mobile
- Hides TweaksPanel and PlaybackBar from the recording
- Moves the ZIP to `Output/<archive name>/` after conversion
- When run without arguments, interactively asks for format and duration
- When multiple ZIPs are present, asks which ones to process
- Auto-installs dependencies (playwright, imageio-ffmpeg, Chromium) on first run
