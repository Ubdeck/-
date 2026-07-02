import importlib.util
from functools import lru_cache

from .paths import bundle_root


def _load_module(module_name: str, file_name: str):
    file_path = bundle_root() / file_name
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载脚本：{file_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@lru_cache(maxsize=1)
def load_search_module():
    return _load_module("legacy_search", "legacy/candidate_search.py")


@lru_cache(maxsize=1)
def load_resume_extract_batch_module():
    return _load_module("legacy_resume_extract_batch", "legacy/resume_extract_batch.py")


@lru_cache(maxsize=1)
def load_chat_flow_module():
    return _load_module("legacy_chat_flow_test", "legacy/chat_flow_test.py")
