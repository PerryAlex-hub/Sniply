"""Single source of truth for Sniply's version number.

Bump this with every release you publish on GitHub (match the release
tag, e.g. "0.2.0" for tag "v0.2.0") -- the update checker
(update_checker.py) compares this against GitHub's latest release tag
to decide whether to tell the user a newer version exists.
"""

__version__ = "0.2.0"
