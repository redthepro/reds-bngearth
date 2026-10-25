"""Update check (the user, 2026-10-15: "add an update system where the mod will check GitHub for any updates").

The project's latest published release is read through netfetch's GitHub rate limits and compared with this
copy's VERSION; when GitHub's is newer the server tells the
mod ('update' message), which says so once in game and on its settings page, with the link to the download.

    newer("1.4.1-preview", "1.4.0-preview") -> True
    check(local_version, net) -> {"current", "latest", "url"} or None (up to date, offline, no VERSION there)
"""
import json
import os
import re
import sys

import netfetch

# the folder with VERSION: above server/, or the packaged app's bundle (launcher.py frozen by PyInstaller, where
# every module sits at the bundle's top)
ROOT = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def repository(root=ROOT, environ=None):
    """Explicit update channel; absent, disabled or malformed configuration fails closed."""
    env = os.environ if environ is None else environ
    value = env.get("BNGE_UPDATE_REPO")
    if value is None:
        try:
            with open(os.path.join(root, "UPDATE_REPO"), encoding="utf-8") as f:
                value = f.read().strip()
        except OSError:
            return ""
    value = value.strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9][A-Za-z0-9_.-]*", value):
        return ""
    return value


REPO = repository()
RELEASE_URL = f"https://api.github.com/repos/{REPO}/releases/latest" if REPO else ""
PAGE_URL = f"https://github.com/{REPO}/releases" if REPO else ""


def local_version(root=ROOT):
    """This copy's VERSION (the folder above server/), or None."""
    try:
        with open(os.path.join(root, "VERSION"), encoding="utf-8") as f:
            v = f.read().strip()
        return v or None
    except OSError:
        return None


def parse(v):
    """(major, minor, patch, release, build) ordering; commit builds follow their base version.

    A preview remains below the same release without a suffix. CI build numbers compare numerically,
    ignoring their identifying commit hash; None for an invalid version.
    """
    text = str(v or "").strip()
    build = re.search(r"-build\.(\d+)\.g[0-9a-f]+$", text)
    if build:
        text = text[:build.start()]
    m = re.fullmatch(r"v?(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:-([0-9A-Za-z.-]+))?", text)
    if not m:
        return None
    nums = tuple(int(g or 0) for g in m.groups()[:3])
    return nums + ((0, m.group(4)) if m.group(4) else (1, ""), int(build.group(1)) if build else 0)


def newer(latest, current):
    """Is version latest newer than current? (False when either is not a version.)"""
    a, b = parse(latest), parse(current)
    return a is not None and b is not None and a > b


def check(current, net=None):
    """The update GitHub has for version current, or None. Blocking (a worker thread)."""
    if not current or not REPO:
        return None
    net = net if net is not None else netfetch.default_fetcher()
    try:
        release = json.loads(net.get(RELEASE_URL, kind="background").body)
    except (netfetch.FetchError, OSError, ValueError, TypeError):
        return None
    if not isinstance(release, dict) or release.get("draft") or release.get("prerelease"):
        return None
    latest = str(release.get("tag_name") or "").removeprefix("v")
    if not newer(latest, current):
        return None
    return {"current": current, "latest": latest, "url": PAGE_URL}


# The mod's update notice asks the server to open the releases page: the game's openWebBrowser does nothing in the
# native Linux build (the user, 2026-10-18: "i clicked open releases page but it never opened my browser"). Only this
# project's GitHub pages are opened (a message from the mod cannot open anything else).
OPEN_PREFIX = f"https://github.com/{REPO}/" if REPO else ""


def open_page(url, popen=None):
    """Open url (this project's GitHub pages only) in the player's browser, without waiting. Returns True if a
    browser was asked. On Linux xdg-open runs with the packaged app's library and Python paths taken out of its
    environment (the AppImage's own libraries broke the browser it starts)."""
    import subprocess
    if not OPEN_PREFIX or not isinstance(url, str) or not url.startswith(OPEN_PREFIX) or any(c in url for c in ' "\'<>`\\\n\r'):
        return False
    popen = popen or subprocess.Popen
    try:
        if sys.platform == "win32":
            os.startfile(url)       # noqa: S606 (the default browser)
            return True
        env = {k: v for k, v in os.environ.items()
               if k not in ("LD_LIBRARY_PATH", "LD_PRELOAD", "PYTHONHOME", "PYTHONPATH", "APPDIR", "APPIMAGE", "ARGV0")}
        cmd = ["open", url] if sys.platform == "darwin" else ["xdg-open", url]
        popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        return True
    except (OSError, ValueError):
        return False
