import re
from datetime import datetime

from DrissionPage import ChromiumPage
from recruit_assistant.platforms.liepin import connect_chromium_page as liepin_connect_chromium_page

from .paths import DEFAULT_DEBUG_PORT, runtime_root


def _append_log(message: str):
    try:
        log_dir = runtime_root()
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "launcher.log"
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with log_file.open("a", encoding="utf-8") as file:
            file.write(f"[{timestamp}] {message}\n")
    except Exception:
        return


def same_site_url(current_url: str, target_url: str) -> bool:
    current = str(current_url or "").lower()
    target = str(target_url or "").lower()
    try:
        current_host = re.sub(r"^www\\.", "", current.split("//", 1)[-1].split("/", 1)[0])
        target_host = re.sub(r"^www\\.", "", target.split("//", 1)[-1].split("/", 1)[0])
        return bool(current_host and target_host and current_host == target_host)
    except Exception:
        return current.startswith(target)


def connect_chromium_page(
    search_url: str | None = None,
    port: int = DEFAULT_DEBUG_PORT,
    connect_timeout: float = 20.0,
    retries: int = 2,
) -> ChromiumPage:
    _append_log(
        f"connect_chromium_page delegate_to_liepin port={port} search_url={search_url or ''} retries={retries}"
    )
    page = liepin_connect_chromium_page(
        search_url=search_url,
        port=port,
        connect_timeout=connect_timeout,
        retries=retries,
    )
    _append_log(f"connect_chromium_page ok url={getattr(page, 'url', '')}")
    return page
