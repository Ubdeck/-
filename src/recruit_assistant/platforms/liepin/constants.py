from __future__ import annotations

import sys
from pathlib import Path


SEARCH_URL = "https://lpt.liepin.com/search"
JOB_MANAGER_URL = "https://lpt.liepin.com/job/manager"
CHAT_URL = "https://lpt.liepin.com/chat/im"
DEFAULT_MATCH_REQUIREMENTS = """通用要求：
1. 候选人的过往经历必须和开聊职位方向相关，优先看最近 5 年工作经历。
2. 销售岗位优先要求有企业端销售、行业客户、大客户、渠道或团队管理经验。
3. 城市、薪资、行业方向明显不匹配时不要通过。
4. 关键信息缺失时保持谨慎，不要为了通过而脑补简历中没有的信息。
5. 只有明确值得发起沟通时才返回 match=true。"""
DEFAULT_BROWSER_PORT = 9225


def get_app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[4]


APP_DIR = get_app_dir()
RUNTIME_DIR = APP_DIR / "runtime"

KEYWORDS_PLACEHOLDER = "\u641c\u804c\u4f4d/\u516c\u53f8/\u884c\u4e1a\u7b49\uff08\u4e2d\u6587\u7528\u7a7a\u683c\u9694\u5f00\uff0c\u82f1\u6587\u7528\u9017\u53f7\u9694\u5f00\uff09"
JOB_PLACEHOLDER = "\u641c\u7d22\u804c\u4f4d"
COMPANY_PLACEHOLDER = "\u641c\u7d22\u516c\u53f8"
CITY_SEARCH_PLACEHOLDER = "\u641c\u7d22\u57ce\u5e02"

CONFIRM_TEXT = "\u786e\u5b9a"
CITY_CONFIRM_TEXT = "\u786e\u8ba4"
OTHER_TEXT = "\u5176\u4ed6"
CURRENT_CITY_TITLE = "\u76ee\u524d\u57ce\u5e02"
EXPECTED_CITY_TITLE = "\u671f\u671b\u57ce\u5e02"
EXPERIENCE_TITLE = "\u7ecf\u9a8c"
EDUCATION_TITLE = "\u6559\u80b2\u7ecf\u5386"
RECRUITMENT_TYPE_TITLE = "\u7edf\u62db\u8981\u6c42"
SCHOOL_TYPE_TITLE = "\u9662\u6821\u8981\u6c42"
ACTIVE_STATUS_TITLE = "\u6d3b\u8dc3\u72b6\u6001"
JOB_STATUS_TITLE = "\u6c42\u804c\u72b6\u6001"
JOB_HOP_FREQUENCY_TITLE = "\u8df3\u69fd\u9891\u7387"
AGE_REQUIREMENT_TITLE = "\u5e74\u9f84\u8981\u6c42"
GENDER_REQUIREMENT_TITLE = "\u6027\u522b\u8981\u6c42"
LANGUAGE_REQUIREMENT_TITLE = "\u8bed\u8a00\u8981\u6c42"
GRADUATION_YEAR_TITLE = "\u6bd5\u4e1a\u5e74\u4efd"
CURRENT_INDUSTRY_TITLE = "\u5f53\u524d\u884c\u4e1a"
EXPECTED_INDUSTRY_TITLE = "\u671f\u671b\u884c\u4e1a"
INDUSTRY_MODAL_TITLE = "\u8bf7\u9009\u62e9\u884c\u4e1a"
INDUSTRY_SEARCH_PLACEHOLDER = "\u8bf7\u8f93\u5165\u884c\u4e1a\u5173\u952e\u8bcd"
CURRENT_FUNCTION_TITLE = "\u5f53\u524d\u804c\u80fd"
EXPECTED_FUNCTION_TITLE = "\u671f\u671b\u804c\u80fd"
FUNCTION_MODAL_TITLE = "\u8bf7\u9009\u62e9\u804c\u4f4d\u7c7b\u522b"
FUNCTION_SEARCH_PLACEHOLDER = "\u8bf7\u8f93\u5165\u804c\u4f4d\u540d\u79f0\u641c\u7d22"
MORE_CONDITIONS_TEXT = "\u66f4\u591a\u6761\u4ef6"
AI_FILL_TEXTS = ("\u586b\u5165", "\u63d2\u5165", "\u586b\u5165\u590d\u5408\u5173\u952e\u8bcd")
