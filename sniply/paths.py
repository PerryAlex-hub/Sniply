"""Where Sniply keeps its data on disk.

One module, because these locations have to agree between the entry
point (which opens the log before any UI exists) and the UI (which
stores sessions), and because "next to the script" is wrong for a
packaged app in a way that is easy to not notice: in a PyInstaller
one-file build, the script lives in a temporary directory that is
created at launch and deleted on exit. Anything written relative to
__file__ therefore disappears the moment the app closes -- which is
exactly what happened to the debug log, so no one running the .exe
could ever produce one for a bug report.

%LOCALAPPDATA%\\Sniply is the standard per-user location on Windows
and survives both the app closing and being upgraded.
"""

import os
from pathlib import Path

#: Root for everything Sniply writes. Falls back to the home
#: directory on the (unexpected) chance LOCALAPPDATA is unset, rather
#: than failing to start over a missing environment variable.
APP_DATA_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Sniply"

#: Session folders: screenshots plus each session's SQLite database.
SESSIONS_DIR = APP_DATA_DIR / "Sessions"

#: The debug log. Every tick's stability diff, dedup decision,
#: capture-area tracking decision and save/error outcome lands here;
#: when something behaves differently against real screen content
#: than in testing, this file is what says what actually happened
#: without needing to reproduce it live.
LOG_PATH = APP_DATA_DIR / "sniply_debug.log"
