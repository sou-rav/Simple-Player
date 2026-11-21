# Simple Player

A simple, offline video player for Windows. It runs on VLC's playback engine (libVLC), so it plays the formats VLC plays, and its layout imitates the Windows 11 Media Player: dark theme, orange accent, a "Continue watching" home grid, a play queue, saved playlists and a settings page.

It has no network features. No streaming, no online lookups, no accounts.

> Status: version 1.0.0. It was built and tested by one person on one Windows 11 PC (100%, 125% and 150% display scaling). It has not been tested on a PC that never had VLC installed. The look imitates the Windows 11 Media Player in PySide6/Qt. It is not native WinUI 3. This project is not affiliated with Microsoft or VideoLAN.

## Features
- Home grid of unfinished videos with progress bars and time left. Finished videos leave it by themselves. Resume is matched by file path and size.
- Clean display names (for example `Show S01E02`). Files and paths are never renamed. You can rename a card (display name only).
- TV episodes are grouped by show and season. Folders can be added to Home and are shown as stacks.
- Play queue page, saved playlists page, mini window (always on top).
- Subtitles and audio tracks with full language names, subtitle delay and size, audio sync, playback speed, equalizer, aspect ratio and zoom, screenshots.
- Volume up to 200%. Gestures and keyboard shortcuts. Drag and drop. Single instance.
- Windows 11 style title bar, page transitions, a loading page while a file starts.

## Run from source
1. Install Python 3.10 or newer and the **64-bit** VLC from videolan.org.
2. `python -m pip install -r requirements.txt`
3. `python player.py` (or `python player.py "C:\path\video.mkv"`). On Windows you can also double-click `start.bat`.

## Build the program and the installer
Needs the 64-bit VLC installed (only to copy its files) and, for the installer, Inno Setup 6.
1. Close Simple Player. Double-click `build_exe.bat`. It builds in `C:\build\SimplePlayer` (outside OneDrive) and copies the VLC runtime next to the exe. The log is `C:\build\build_log.txt`.
2. Double-click `build_installer.bat`. The result is `C:\build\Output\SimplePlayer_Setup.exe` (log: `C:\build\installer_log.txt`). It installs for the current user only (no admin), adds a Start Menu shortcut and an optional desktop shortcut, and lists Simple Player in "Open with" and Default apps for MP4, MKV, AVI, MOV, WMV, WebM and M4V. It never makes itself the default player. Uninstall keeps your settings and history.

Keep the version in `SimplePlayer.iss` equal to `APP_VERSION` in `player.py`. The installer is not code-signed, so Windows SmartScreen may warn on first run.

## Keys and gestures
- Space play/pause. Left / Right rewind / fast forward (10 s / 30 s by default). Up / Down volume. M mute. N next. P previous. [ and ] speed. F or Enter full screen. Esc leaves it.
- S screenshot. J / K audio sync. G / H subtitle delay. Ctrl+O open file. Ctrl+I properties. Ctrl+Q play queue. Ctrl+Shift+E equalizer. F2 rename the selected card.
- Mouse wheel over the timeline seeks. Wheel elsewhere changes the volume. Two-finger swipe sideways seeks 5 s per step.

## Your data and diagnostics
Settings, history and thumbnails are in `%APPDATA%\SimplePlayer` (`state.json`, `thumbs\`, `player.log`). Empty switch files in that folder: `debug.txt` (writes a detailed `vlc.log`), `native_titlebar.txt` (standard Windows title bar), `opaque_controls.txt` (opaque control bar), `native_bar.txt` (old see-through bar), `no_transition.txt` (no fades).

## Third-party software
Simple Player uses VLC / libVLC (VideoLAN), python-vlc, and PySide6 (Qt for Python). They have their own licenses, mostly LGPL, and some VLC plugins are GPL. See videolan.org/legal.html and qt.io/licensing. The installer in the releases bundles the VLC runtime.

## License
The Simple Player source code is under the MIT License (see `LICENSE`). Third-party components keep their own licenses.
