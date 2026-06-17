# JSX to Video Intro Converter

Available in: **English** | [Русский](README_RU.md)

Converts React/JSX logo animations (exported by design tools) into MP4 video files — Full HD, 2K, 4K or 8K, portrait or landscape. Just drop a ZIP archive and run.

## Demo

Converts JSX components into video — two ways to run: via Claude Code dialog or terminal.

[video]

## Option 1 — via Claude Code (recommended)

If you have [Claude Code](https://claude.ai/code) installed — open the project folder in it and simply type **"convert"**. Claude will ask for the format and duration right in the chat, then run the conversion.

Claude responds in the language you write in — English and Russian are supported.

Supported formats: Full HD (1920×1080), 2K, 4K, 8K. Portrait/landscape orientation is detected automatically per file.

## Option 2 — via terminal

1. Put the ZIP archive with your animation into the **`Input`** folder
2. Run in the terminal from the project folder:
   ```bash
   python3 convert.py
   ```
3. The script will ask about format and duration — answer the prompts
4. Finished MP4 files will appear in the **`Output/<archive name>/`** folder

If the archive contains multiple animation versions — each one is converted separately. The original ZIP is automatically moved to the output folder. Portrait/landscape orientation is detected automatically per file.

## Preparing your archive (Claude Design users)

The script works with any ZIP archive — it will find and convert all HTML/JSX files it can locate. However, if you generate animations in Claude Design, sending the prompt below **before downloading the ZIP** ensures the archive is structured correctly: only the final variants go into conversion, preview pages and combined files are kept separate.

<details>
<summary>Show prompt</summary>

```
Now, for each intro version, create a separate JSX file so we can correctly convert each version into a video using our script.

Important: do not place all intros on a single shared HTML page or in a single shared JSX file. Each individual intro variant must have its own separate JSX file.

Since we download the result as a single ZIP archive, make sure to separate the files into folders inside the archive.

Place the final JSX files for conversion only in the folder:

for_conversion/

The for_conversion/ folder should contain only clean, final JSX files where each file contains exactly one intro variant.

Place old combined files, HTML pages with menus, preview pages, files containing multiple intros, and any auxiliary materials separately in the folder:

not_for_conversion/

The not_for_conversion/ folder is only for archiving or previewing. Our script should not use files from this folder for conversion.

File names in the for_conversion/ folder must not be abstract like Intro_01.jsx, Intro_02.jsx, etc. Each JSX file name must correspond to the project name and video format.

For example, if the project is called ProjectName, the structure should be:

* for_conversion/ProjectName_16x9_Variant_01.jsx
* for_conversion/ProjectName_16x9_Variant_02.jsx
* for_conversion/ProjectName_9x16_Variant_01.jsx
* for_conversion/ProjectName_9x16_Variant_02.jsx

If the video format is 16:9, it must be indicated in the file name as 16x9. If the format is 9:16, it must be indicated as 9x16.

Each JSX file in for_conversion/ must contain only one specific intro version. Do not combine multiple variants into one file.

If you want to create a shared HTML page with a menu, preview, or variant switcher for convenience — that is fine, but only in the not_for_conversion/ folder.

Menus, buttons, navigation, variant lists, and any UI elements must not appear inside the JSX files from for_conversion/.

The JSX files in for_conversion/ should contain only the clean intro variant — no menus, no buttons, no extra wrappers, and no elements that would need to be manually removed later.

Keep the intro variants themselves the same as previously generated, but split them into separate JSX files and place them only in the for_conversion/ folder.

Important: the for_conversion/ folder must not contain old combined JSX files, HTML pages, preview pages, or files where multiple intros are together. It should only contain files ready for video conversion.

Technical requirements for each JSX file in for_conversion/:

Each JSX file must be completely self-contained and must not depend on any other files in the archive. Specifically:

1. No imports and no references to external JSX, JS, or CSS files. Everything needed must be inside the file itself.

2. Only window.React and window.ReactDOM as external dependencies (React 18). No other global variables or libraries.

3. No JSX syntax requiring Babel. Only React.createElement(). The file must work as plain JS without transpilation.

4. Three constants must be declared at the top of the file:
   - FRAME_W — frame width in pixels (e.g., const FRAME_W = 1920)
   - FRAME_H — frame height in pixels (e.g., const FRAME_H = 1080)
   - DURATION — animation duration in seconds (e.g., const DURATION = 5)

5. When the page loads, the file must automatically mount into <div id="root"> and immediately start playing the animation without any user interaction.

6. All assets (images, logos, icons) must be embedded directly in the JSX file as base64. No references to files in uploads/, assets/, etc.
```

</details>

## Requirements

- Python 3.9+
- Internet connection on first run (dependency install + loading React from CDN during render)

On first run the script will automatically check for everything it needs and offer to install anything missing.

## Notes

This script was built for a specific internal task — converting JSX logo animations into video. We decided to share it in case it's useful to others.

Terminal mode has no external dependencies and always works.

## Support the project

If this script saves you time or helps with your work, you can support the author here:

https://neuroagent.cc/donate

Any support is appreciated and helps keep the project alive.

You can also follow updates and new tools here:

https://t.me/NeuroAgentCC
