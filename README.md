# Sniply

A free, local-first Windows desktop app that automatically captures unique presentation slides during Zoom calls, PowerPoint presentations, or any screen-shared lecture — so students don't have to manually screenshot every slide change.

Point it at a region of your screen, hit **Start Capture**, and it saves a new screenshot only when the content actually changes — skipping duplicates, cursor jitter, and slide transitions — then exports the result as a PDF.

## Features

- **Region-based capture** — select any rectangle of your screen (Zoom window, PowerPoint, a browser tab) via a Snipping-Tool-style overlay.
- **Captures only what you selected** — the selection is treated as *that content*, not as fixed coordinates. When the target window rearranges itself — Zoom's chat or participants panel opening, a view switch, the presenter resizing their share — the slide area moves inside an identically-sized window, and Sniply re-locates it instead of continuing to crop the old rectangle (which is how chat panels used to end up saved as slides). If it genuinely can't find the area, it stops saving and says so rather than banking whatever moved in underneath.
- **Smart deduplication** — a multi-stage pipeline (frame-diff stability check → perceptual hashing → SSIM) saves a slide once, recognizes when the lecturer returns to an earlier slide, and tells genuinely different slides apart even when they share a similar layout.
- **Keeps capturing while you multitask** — when the selected area is inside a real window (Zoom, PowerPoint, a browser), Sniply captures that window's content directly rather than reading screen pixels, so it keeps saving slides even if you switch away to code, browse, or open another app on top of it. Falls back to a pause-when-covered screen-region mode if the selection isn't tied to a specific window.
- **Floating control bar** — once capture starts, a small draggable bar (Pause/Resume/Stop/Manual Capture) replaces the full window, so you don't need the app on screen while watching a lecture. It's excluded from the capture itself, so it never ends up in a screenshot even if you drag it over the capture region.
- **Session management** — screenshots and metadata autosave continuously (crash-safe), with a gallery to review, reorder (drag and drop), batch-delete, and select which screenshots to export.
- **Export** — combine selected screenshots into a single PDF, or export them as individual images.
- **Clear state at a glance** — one status pill, in the main window and on the floating bar, says whether capture is locked onto your selected area, paused, or has lost it.
- **Works at any window size** — below ~660px wide the two panes stack into a single scrolling column instead of squeezing, so Sniply is usable docked beside a lecture on a small laptop screen.
- **No cloud, no accounts** — everything runs and stays on your machine.

## Requirements

- Windows 10 (2004+) or Windows 11
- Python 3.12+ (only needed to run from source — not needed for the packaged `.exe`)

## Running from source

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

## Building the Windows executable

```powershell
pip install pyinstaller
pyinstaller --name Sniply --windowed --onefile --icon assets/icon.ico --add-data "assets;assets" main.py
```

`--icon` sets the `.exe`'s own icon (File Explorer, taskbar); `--add-data` bundles `assets/icon.png` so the app can also set its in-app window icon at runtime (see `main.py`'s `_resource_path` helper, which resolves assets correctly both when run from source and from the frozen executable).

The standalone executable is written to `dist/Sniply.exe` — it bundles Python and all dependencies, so it runs on a machine with no Python installed. That's what gets attached to a GitHub Release for distribution.

## Usage

1. **New Session** — screenshots and session data are stored automatically under `%LOCALAPPDATA%\Sniply\Sessions\<name>`; you only pick a name.
2. **Select Capture Area** — drag a rectangle around the slide content (e.g. your Zoom window), while it's actually visible on screen.
3. **Start Capture** — the window hides and a small floating bar takes over. Switch to your lecture and go about watching it.
4. **Pause / Resume / Manual Capture / Stop** from the floating bar, or click its expand icon to bring the full window back (gallery, export, settings) without stopping capture.
5. **Review** in the gallery — double-click to view full size, drag to reorder, check the ones you want, right-click or select multiple + Delete to remove unwanted shots.
6. **Export as PDF or Images** once you're happy with the selection.

## Configuration

Detection sensitivity is tunable from **Settings** in the app:

| Setting | Default | What it controls |
|---|---|---|
| Capture interval | 1000 ms | How often the screen is sampled |
| Stability count | 2 | Consecutive "unchanged" ticks required before a frame is considered settled |
| Fast-diff threshold | 4.0 | Pixel-difference sensitivity for detecting any change at all |
| Phash duplicate / new-content distance | 5 / 12 | Perceptual-hash thresholds for confidently same vs. confidently different |
| SSIM duplicate threshold | 0.90 | Structural-similarity cutoff used only for the ambiguous middle band |

Defaults live in [`sniply/config/defaults.py`](sniply/config/defaults.py).

## Project structure

```
sniply/
  capture/      screen/window grabbing, region selection, capture-area tracking, occlusion detection
  detection/    stability tracking, perceptual hashing, SSIM, the dedup pipeline
  storage/      session persistence (SQLite) and screenshot autosave
  export/       PDF and image export
  config/       default settings
  ui/           PySide6 interface (theme tokens, shared widgets, main window, overlay, gallery, floating bar)
tests/          pytest suite for the detection/storage/export logic
```

The detection/capture logic has no UI dependency, so it's tested with prerecorded synthetic frames instead of a live screen — see `tests/`. That includes the capture-area tracker: `tests/test_anchor_tracker.py` builds synthetic windows whose panels move and resize the way real chrome does, and asserts both that the tracker follows them and that it refuses to guess when it can't.

## Testing

```powershell
pip install pytest
pytest
```

## Reporting a problem

Sniply writes a debug log to `%LOCALAPPDATA%\Sniply\sniply_debug.log`, recording every
capture tick's stability diff, dedup decision, capture-area tracking decision and
save/error outcome. It rotates at 2 MB (keeping two older files) and each run starts with
a `--- Sniply <version> starting ---` banner, so the relevant run stays identifiable.
Attach it to a bug report — it's usually the difference between a reproducible issue and
a guess.

## Known limitations

- Windows only (uses Win32 APIs for window capture, occlusion detection, and capture exclusion).
- Assumes uniform DPI scaling across monitors; mixed-DPI multi-monitor setups may see minor region drift.
- Re-locating the capture area needs a recognisable landmark immediately around your selection. If you select almost the whole window, or the surroundings are a flat colour field with no detail, there's nothing to track by: Sniply falls back to the fixed offset and says so in the preview caption. Likewise, if the surroundings are highly repetitive, it refuses to guess between identical-looking candidates and reports the area as lost rather than picking one.
- Capturing a true DirectX-exclusive fullscreen window can occasionally fail to update — if that happens, use windowed presentation mode instead of a full-screen slideshow.
- Window-capture mode needs the target window to still exist and not be fully minimized; if it's closed mid-session, capture pauses with a clear message rather than silently failing.

## Publishing a new release

1. Bump anything version-relevant, commit, and push to `main`.
2. Build the executable (see above) and smoke-test it on a clean machine if possible.
3. Create a GitHub Release and attach `dist/Sniply.exe` as a release asset — no installer or code signing required for personal/small-scale distribution.
