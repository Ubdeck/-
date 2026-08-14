from __future__ import annotations

import sys
from pathlib import Path


MAIMAI_TALENTS_URL = "https://maimai.cn/ent/v41/recruit/talents?pid=&tab=1"
DEFAULT_BROWSER_PORT = 9225
DEFAULT_MAIMAI_GREETING = "您好，我这边有个岗位和您的经历比较匹配，想和您进一步沟通一下。"
DEFAULT_MAIMAI_MATCH_REQUIREMENTS = """通用要求：
1. 候选人的过往经历必须和本次搜索关键词、目标岗位方向相关，优先看最近 5 年工作经历。
2. 销售岗位优先要求有企业端销售、行业客户、大客户、渠道、区域销售或团队管理经验。
3. 城市、薪资、行业方向、岗位方向明显不匹配时不要通过。
4. 关键信息缺失时保持谨慎，不要为了通过而脑补简历中没有的信息。
5. 只有明确值得发起沟通时才返回 match=true。"""


def get_app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[4]


APP_DIR = get_app_dir()
RUNTIME_DIR = APP_DIR / "runtime"
