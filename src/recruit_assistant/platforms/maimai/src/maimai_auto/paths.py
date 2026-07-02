import sys
from pathlib import Path


DEFAULT_DEBUG_PORT = 9224
DEFAULT_MAIMAI_URL = "https://maimai.cn/ent/v41/recruit/talents?pid=&tab=1"


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def bundle_root() -> Path:
    if hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return project_root()


def runtime_root() -> Path:
    if getattr(sys, "frozen", False):
        root = Path(sys.executable).resolve().parent / "runtime" / "maimai"
    else:
        root = project_root().parents[3] / "runtime" / "maimai"
    root.mkdir(parents=True, exist_ok=True)
    return root
