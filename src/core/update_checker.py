import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass

from src.core.constants import APP_VERSION

log = logging.getLogger(__name__)

GITHUB_RELEASES_URL = "https://api.github.com/repos/MoschenHTS/ZebraFET/releases/latest"
NETWORK_TIMEOUT = 5


@dataclass(frozen=True)
class UpdateInfo:
    version: str
    release_url: str
    release_notes: str


def _parse_version(v: str) -> tuple[int, ...] | None:
    try:
        return tuple(int(x) for x in v.lstrip("v").split("."))
    except ValueError:
        return None


def check_for_update() -> UpdateInfo | None:
    req = urllib.request.Request(
        GITHUB_RELEASES_URL,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"ZebraFET/{APP_VERSION}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=NETWORK_TIMEOUT) as resp:
            data = json.loads(resp.read())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as e:
        log.info(f"Update check skipped: {e}")
        return None

    remote = _parse_version(data.get("tag_name", ""))
    local = _parse_version(APP_VERSION)
    if remote is None or local is None or remote <= local:
        return None

    return UpdateInfo(
        version=data["tag_name"].lstrip("v"),
        release_url=data.get("html_url", ""),
        release_notes=data.get("body", "") or "",
    )
