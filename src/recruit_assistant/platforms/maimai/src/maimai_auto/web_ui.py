import ctypes
import json
import os
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import webbrowser
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

from .config import (
    EDUCATION_EXTRA_OPTIONS,
    EDUCATION_OPTIONS,
    GENDER_OPTIONS,
    GRADUATION_YEAR_OPTIONS,
    KEYWORD_MODE_OPTIONS,
    WORK_YEAR_OPTIONS,
    ScheduleEntry,
    SearchSettings,
    delete_schedule,
    load_schedules,
    load_settings,
    save_settings,
    upsert_schedule,
)
from .contacted_candidates import load_contacted_candidates
from .message_monitor import load_state as load_message_followup_state
from .matching import load_match_results
from .paths import DEFAULT_DEBUG_PORT, DEFAULT_MAIMAI_URL, runtime_root
from .workflows import is_message_followup_running, run_full_pipeline


HOST = "127.0.0.1"
PORT = 18765

STATE = {
    "running": False,
    "status": "准备就绪",
    "error": "",
    "started_at": None,
    "scheduled_for": "",
    "schedule_enabled": False,
    "schedule_count": 0,
    "message_followup_running": False,
}
STATE_LOCK = threading.Lock()
SCHEDULE_LOCK = threading.RLock()
SCHEDULE_TIMERS: dict[str, threading.Timer] = {}
SERVER: ThreadingHTTPServer | None = None

BROWSER_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]


def append_runtime_log(message: str):
    try:
        log_dir = runtime_root() / "runtime"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "launcher.log"
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with log_file.open("a", encoding="utf-8") as file:
            file.write(f"[{timestamp}] {message}\n")
    except Exception:
        return


def browser_launch_env() -> dict:
    env = os.environ.copy()
    for key in list(env.keys()):
        upper_key = key.upper()
        if upper_key.startswith("PYINSTALLER_") or upper_key in {"_MEIPASS2", "PYTHONHOME", "PYTHONPATH"}:
            env.pop(key, None)
    return env


def reset_windows_dll_dir():
    if os.name != "nt":
        return
    try:
        ctypes.windll.kernel32.SetDllDirectoryW(None)
        append_runtime_log("reset_windows_dll_dir applied")
    except Exception as exc:
        append_runtime_log(f"reset_windows_dll_dir failed: {exc}")


def _is_writable_dir(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except Exception:
        return False


def browser_runtime_dir() -> Path:
    bundled_runtime = runtime_root() / "runtime"
    local_app_data = Path.home() / "AppData" / "Local" / "maimai-auto-runtime"
    home_fallback = Path.home() / ".maimai-auto-runtime"
    temp_fallback = Path(tempfile.gettempdir()) / "maimai-auto-runtime"
    candidates = [bundled_runtime]
    if not _is_writable_dir(bundled_runtime):
        candidates.extend([local_app_data, home_fallback, temp_fallback])
    for candidate in candidates:
        if _is_writable_dir(candidate):
            return candidate
    raise RuntimeError("无法创建浏览器运行目录，请检查当前用户目录写入权限。")


def set_status(message: str):
    with STATE_LOCK:
        STATE["status"] = message


def get_state() -> dict:
    with STATE_LOCK:
        snapshot = dict(STATE)
    snapshot["message_followup_running"] = is_message_followup_running()
    return snapshot


def followup_target_key(item: dict) -> tuple[str, int, int]:
    return (
        str(item.get("name", "") or "").strip(),
        int(item.get("page_number", 0) or 0),
        int(item.get("page_list_index", item.get("list_index", 0)) or 0),
    )


def load_followup_payload() -> dict:
    contacted = load_contacted_candidates()
    state = load_message_followup_state()
    contacted_candidates = contacted.get("contacted_candidates", [])
    state_targets = state.get("targets", [])
    state_lookup = {
        followup_target_key(item): dict(item)
        for item in state_targets
        if str(item.get("name", "") or "").strip()
    }

    targets = []
    for item in contacted_candidates:
        key = followup_target_key(item)
        merged = dict(item)
        merged.update(state_lookup.get(key, {}))
        merged["name"] = str(merged.get("name", "") or "").strip()
        merged["page_number"] = int(merged.get("page_number", 0) or 0)
        merged["page_list_index"] = int(merged.get("page_list_index", merged.get("list_index", 0)) or 0)
        merged["list_index"] = int(merged.get("list_index", merged["page_list_index"]) or merged["page_list_index"])
        merged["phone_exchange_status"] = merged.get("phone_exchange_status", "") or "pending"
        targets.append(merged)

    recent_replies = [
        {
            "name": item.get("name", ""),
            "reply_detected_at": item.get("reply_detected_at", ""),
            "last_messages": list(item.get("last_messages", []) or []),
            "last_session_preview": item.get("last_session_preview", ""),
        }
        for item in targets
        if item.get("reply_detected_at")
    ]
    recent_replies.sort(key=lambda item: item.get("reply_detected_at", ""), reverse=True)
    return {
        "running": is_message_followup_running(),
        "contacted_count": len(contacted_candidates),
        "contacted_candidates": contacted_candidates,
        "targets": targets,
        "recent_replies": recent_replies[:10],
        "updated_at": state.get("updated_at", "") or contacted.get("updated_at", ""),
    }


def escape(value) -> str:
    return str(value or "").replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")


def options(items: list[str], selected: str) -> str:
    return "".join(
        f'<option value="{escape(item)}"{" selected" if item == selected else ""}>{escape(item)}</option>'
        for item in items
    )


def checked(value: bool) -> str:
    return " checked" if value else ""


def parse_settings(body: bytes) -> SearchSettings:
    existing = load_settings()
    base_data = existing.__dict__.copy()
    form_data = {
        key: values[0]
        for key, values in parse_qs(body.decode("utf-8"), keep_blank_values=True).items()
    }
    base_data.update(form_data)
    base_data["actual_send"] = "actual_send" in form_data
    base_data["schedule_enabled"] = bool(form_data.get("schedule_time"))
    return SearchSettings.from_dict(base_data)


def parse_next_run(time_text: str) -> datetime:
    try:
        hour, minute = [int(item) for item in time_text.strip().split(":", 1)]
    except Exception as exc:
        raise ValueError("定时时间格式应为 HH:MM，例如 11:00。") from exc
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("定时时间必须在 00:00 到 23:59 之间。")
    now = datetime.now()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target


def schedule_entry_next_run(entry: ScheduleEntry) -> datetime:
    return parse_next_run(entry.time_text)


def schedule_entry_summary(entry: ScheduleEntry) -> str:
    settings = SearchSettings.from_dict(entry.settings)
    parts = []
    if settings.keyword:
        parts.append(f"关键词：{settings.keyword}")
    if settings.city and settings.city != "无":
        parts.append(f"城市：{settings.city}")
    if settings.companies:
        parts.append(f"公司：{settings.companies}")
    parts.append(f"页数：{settings.page_limit}")
    parts.append("实际沟通" if settings.actual_send else "测试模式")
    return " | ".join(parts)


def build_schedule_state(entries: list[ScheduleEntry] | None = None) -> tuple[str, bool, int]:
    schedule_entries = entries if entries is not None else load_schedules()
    if not schedule_entries:
        return "", False, 0
    next_entry = min(schedule_entries, key=schedule_entry_next_run)
    next_run = schedule_entry_next_run(next_entry).strftime("%Y-%m-%d %H:%M")
    return next_run, True, len(schedule_entries)


def refresh_schedule_state(entries: list[ScheduleEntry] | None = None):
    scheduled_for, enabled, count = build_schedule_state(entries)
    with STATE_LOCK:
        STATE["scheduled_for"] = scheduled_for
        STATE["schedule_enabled"] = enabled
        STATE["schedule_count"] = count


def run_worker(settings: SearchSettings, source: str = "手动启动") -> bool:
    with STATE_LOCK:
        if STATE["running"]:
            STATE["status"] = f"已有任务运行中，跳过：{source}"
            return False
        STATE["running"] = True
        STATE["error"] = ""
        STATE["status"] = f"正在执行：{source}"
        STATE["started_at"] = time.time()
    try:
        run_full_pipeline(settings, set_status)
        with STATE_LOCK:
            STATE["status"] = (
                "本轮沟通已完成，消息跟进已启动。"
                if is_message_followup_running()
                else "已完成，本轮流程执行成功。"
            )
            STATE["message_followup_running"] = is_message_followup_running()
        return True
    except Exception as exc:
        with STATE_LOCK:
            STATE["error"] = str(exc)
            STATE["status"] = "运行失败"
            STATE["message_followup_running"] = is_message_followup_running()
        return False
    finally:
        with STATE_LOCK:
            STATE["running"] = False
            STATE["message_followup_running"] = is_message_followup_running()


def make_schedule_entry(settings: SearchSettings) -> ScheduleEntry:
    time_text = str(settings.schedule_time or "").strip()
    if not time_text:
        raise ValueError("请先选择定时时间。")
    parse_next_run(time_text)
    snapshot = settings.__dict__.copy()
    snapshot["schedule_enabled"] = False
    snapshot["schedule_time"] = time_text
    return ScheduleEntry(
        id=uuid.uuid4().hex,
        time_text=time_text,
        created_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        settings=snapshot,
    )


def materialize_schedule_settings(entry: ScheduleEntry) -> SearchSettings:
    data = dict(entry.settings or {})
    data["schedule_enabled"] = False
    data["schedule_time"] = entry.time_text
    return SearchSettings.from_dict(data)


def cancel_all_schedules():
    with SCHEDULE_LOCK:
        for timer in SCHEDULE_TIMERS.values():
            try:
                timer.cancel()
            except Exception:
                continue
        SCHEDULE_TIMERS.clear()
    refresh_schedule_state([])


def cancel_schedule_timer(schedule_id: str):
    with SCHEDULE_LOCK:
        timer = SCHEDULE_TIMERS.pop(schedule_id, None)
        if timer:
            try:
                timer.cancel()
            except Exception:
                pass


def schedule_worker(schedule_id: str):
    entries = load_schedules()
    entry = next((item for item in entries if item.id == schedule_id), None)
    if not entry:
        cancel_schedule_timer(schedule_id)
        refresh_schedule_state(entries)
        return
    settings = materialize_schedule_settings(entry)
    run_worker(settings, f"定时任务 {entry.time_text}")
    if next((item for item in load_schedules() if item.id == schedule_id), None):
        schedule_entry(entry)
    refresh_schedule_state(load_schedules())


def schedule_entry(entry: ScheduleEntry) -> datetime:
    target = schedule_entry_next_run(entry)
    delay = max(1, (target - datetime.now()).total_seconds())
    timer = threading.Timer(delay, schedule_worker, args=(entry.id,))
    timer.daemon = True
    with SCHEDULE_LOCK:
        old = SCHEDULE_TIMERS.get(entry.id)
        if old:
            old.cancel()
        SCHEDULE_TIMERS[entry.id] = timer
        timer.start()
    return target


def reload_all_schedules():
    entries = load_schedules()
    with SCHEDULE_LOCK:
        current_ids = set(SCHEDULE_TIMERS.keys())
        target_ids = {entry.id for entry in entries}
        for schedule_id in current_ids - target_ids:
            timer = SCHEDULE_TIMERS.pop(schedule_id, None)
            if timer:
                timer.cancel()
        for entry in entries:
            schedule_entry(entry)
    refresh_schedule_state(entries)
    if entries:
        next_run, _, count = build_schedule_state(entries)
        set_status(f"已加载 {count} 个定时任务，最近一次：{next_run}")


def schedule_payload() -> dict:
    entries = load_schedules()
    items = []
    for entry in entries:
        settings = SearchSettings.from_dict(entry.settings)
        items.append(
            {
                "id": entry.id,
                "time_text": entry.time_text,
                "created_at": entry.created_at,
                "next_run": schedule_entry_next_run(entry).strftime("%Y-%m-%d %H:%M"),
                "summary": schedule_entry_summary(entry),
                "keyword": settings.keyword,
                "city": settings.city,
                "actual_send": settings.actual_send,
            }
        )
    return {"items": items, "count": len(items)}


def html_page(settings: SearchSettings) -> str:
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>脉脉自动化助手</title>
  <style>
    :root {{
      --ink:#152033;
      --muted:#66758a;
      --line:#d9e2ee;
      --card:rgba(255,255,255,.82);
      --glass:rgba(248,250,255,.7);
      --blue:#0f6fff;
      --danger:#d43c52;
    }}
    * {{ box-sizing:border-box; }}
    body {{
      margin:0;
      min-height:100vh;
      color:var(--ink);
      font-family:"SF Pro Display","PingFang SC","Microsoft YaHei UI","Segoe UI",sans-serif;
      background:
        radial-gradient(circle at 10% 0%, rgba(255,255,255,.95), transparent 28%),
        radial-gradient(circle at 92% 8%, rgba(165,206,255,.85), transparent 26%),
        linear-gradient(135deg, #f6f9ff 0%, #edf4ff 55%, #f9fbff 100%);
    }}
    .wrap {{ width:min(1180px, calc(100% - 28px)); margin:24px auto 36px; }}
    .hero {{ padding:8px 6px 22px; }}
    .eyebrow {{ color:#1e63da; font-size:12px; letter-spacing:.16em; font-weight:800; }}
    h1 {{ margin:10px 0 8px; font-size:34px; letter-spacing:-.05em; }}
    .sub {{ margin:0; max-width:900px; color:var(--muted); line-height:1.85; font-size:14px; }}
    .card {{
      background:var(--card);
      border:1px solid rgba(255,255,255,.92);
      border-radius:30px;
      box-shadow:0 28px 80px rgba(26,38,64,.12);
      backdrop-filter:blur(24px);
      padding:26px;
    }}
    .grid {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:16px; }}
    label {{ display:block; margin:0 0 8px; font-size:13px; font-weight:800; color:#334155; }}
    input, select, textarea {{
      width:100%;
      border:1px solid var(--line);
      background:rgba(255,255,255,.94);
      color:var(--ink);
      border-radius:16px;
      padding:13px 14px;
      font-size:15px;
      outline:none;
    }}
    textarea {{ min-height:110px; resize:vertical; }}
    .wide {{ grid-column:1 / -1; }}
    .hint {{ margin-top:8px; color:#7b8798; font-size:12px; line-height:1.7; }}
    .stack {{ display:grid; gap:10px; }}
    .inline {{
      display:flex;
      align-items:center;
      gap:12px;
      flex-wrap:wrap;
      padding:14px 16px;
      border-radius:18px;
      border:1px solid var(--line);
      background:var(--glass);
    }}
    .switch {{ display:flex; align-items:center; gap:10px; font-size:14px; color:#21324b; font-weight:700; }}
    .switch input {{ width:18px; height:18px; margin:0; accent-color:var(--blue); }}
    .actions {{
      margin-top:20px;
      padding:18px;
      border-radius:24px;
      border:1px solid rgba(217,226,238,.92);
      background:rgba(248,250,252,.82);
      display:grid;
      gap:12px;
    }}
    .buttons {{ display:grid; grid-template-columns:1fr 1fr 120px; gap:12px; }}
    button {{
      border:0;
      border-radius:18px;
      padding:15px 18px;
      font-size:15px;
      font-weight:900;
      color:white;
      background:linear-gradient(135deg,#121a2b,#24364f);
      cursor:pointer;
    }}
    button.secondary {{ background:linear-gradient(135deg,#0f6fff,#1ea8ff); }}
    button.ghost {{ color:#21324b; background:rgba(255,255,255,.92); border:1px solid var(--line); }}
    button.danger {{ color:var(--danger); background:rgba(255,255,255,.96); border:1px solid #f2c8cf; }}
    .status {{ min-height:22px; color:var(--muted); font-size:13px; line-height:1.7; white-space:pre-wrap; }}
    .list-card {{ margin-top:20px; }}
    .list-head {{ display:flex; justify-content:space-between; gap:12px; align-items:end; margin-bottom:14px; }}
    .list-title {{ font-size:20px; font-weight:900; }}
    .list-sub {{ color:var(--muted); font-size:13px; line-height:1.7; max-width:820px; }}
    .results {{ display:grid; gap:12px; }}
    .result-item {{ border:1px solid var(--line); border-radius:18px; padding:16px; background:rgba(255,255,255,.82); }}
    .result-top {{ display:flex; justify-content:space-between; align-items:center; gap:12px; margin-bottom:8px; }}
    .result-name {{ font-size:16px; font-weight:900; }}
    .score {{ padding:6px 10px; border-radius:999px; background:#e7f0ff; color:#185fd6; font-size:12px; font-weight:900; }}
    .meta {{ color:#536277; font-size:13px; line-height:1.75; }}
    .reason {{ margin-top:8px; color:#1a2740; font-size:14px; line-height:1.75; }}
    .empty {{ padding:18px; border:1px dashed var(--line); border-radius:18px; background:rgba(255,255,255,.45); color:var(--muted); font-size:14px; }}
    .schedule-item {{
      border:1px solid var(--line);
      border-radius:18px;
      padding:16px;
      background:rgba(255,255,255,.82);
      display:grid;
      gap:8px;
    }}
    .schedule-top {{ display:flex; justify-content:space-between; align-items:center; gap:12px; }}
    .schedule-time {{ font-size:16px; font-weight:900; }}
    .schedule-badge {{ padding:6px 10px; border-radius:999px; background:#eef5ff; color:#175fd9; font-size:12px; font-weight:900; }}
    .schedule-actions {{ display:flex; gap:10px; flex-wrap:wrap; }}
    .schedule-actions button {{ padding:10px 14px; border-radius:14px; font-size:13px; }}
    @media (max-width:760px) {{
      .grid, .buttons {{ grid-template-columns:1fr; }}
      .result-top, .list-head, .schedule-top {{ flex-direction:column; align-items:flex-start; }}
    }}
  </style>
</head>
<body>
  <main class="wrap">
    <section class="hero">
      <div class="eyebrow">MAIMAI AUTO</div>
      <h1>脉脉自动化助手</h1>
      <p class="sub">你现在可以保存多套定时任务。每次点击“保存定时”都会把当前整套筛选配置、AI 要求、问候语和执行模式一起保存成一条独立任务，程序启动后会自动恢复这些任务并按时间运行。</p>
    </section>

    <section class="card">
      <form id="form">
        <div class="grid">
          <div>
            <label>关键词</label>
            <input name="keyword" value="{escape(settings.keyword)}" placeholder="例如：研发">
          </div>
          <div>
            <label>关键词模式</label>
            <select name="keyword_mode">{options(KEYWORD_MODE_OPTIONS, settings.keyword_mode)}</select>
          </div>
          <div>
            <label>城市地区</label>
            <input name="city" value="{escape(settings.city)}" placeholder="例如：杭州">
          </div>
          <div>
            <label>执行页数</label>
            <input name="page_limit" type="number" min="1" step="1" value="{escape(settings.page_limit)}">
          </div>
          <div>
            <label>工作年限</label>
            <select name="work_years">{options(WORK_YEAR_OPTIONS, settings.work_years)}</select>
          </div>
          <div>
            <label>毕业年份</label>
            <select name="graduation_year">{options(GRADUATION_YEAR_OPTIONS, settings.graduation_year)}</select>
          </div>
          <div>
            <label>学历要求</label>
            <select name="education">{options(EDUCATION_OPTIONS, settings.education)}</select>
          </div>
          <div>
            <label>学历补充</label>
            <select name="education_extra">{options(EDUCATION_EXTRA_OPTIONS, settings.education_extra)}</select>
          </div>
          <div>
            <label>就职公司</label>
            <input name="companies" value="{escape(settings.companies)}" placeholder="多个公司用逗号分隔">
          </div>
          <div>
            <label>性别</label>
            <select name="gender">{options(GENDER_OPTIONS, settings.gender)}</select>
          </div>
          <div class="wide">
            <label>AI 筛选要求</label>
            <textarea name="ai_requirement_text" placeholder="例如：必须有新华三经历；优先有竞赛获奖；软性要求是沟通清晰、稳定、愿意长期发展。">{escape(settings.ai_requirement_text)}</textarea>
            <div class="hint">这里直接写业务要求即可。留空时会使用内置的通用筛选提示词。</div>
          </div>
          <div class="wide">
            <label>问候语</label>
            <textarea name="greeting" placeholder="例如：你好，我对你的简历很感兴趣，方便沟通一下吗？">{escape(settings.greeting)}</textarea>
          </div>
          <div class="wide stack">
            <div class="inline">
              <div class="switch">
                <input type="checkbox" name="actual_send"{checked(settings.actual_send)}>
                <strong>实际沟通模式</strong>
              </div>
              <span style="color:#6b7a8f;font-size:13px;">开启后会点击“发送后留在此页”；关闭时只做测试动作，不真正发送。</span>
            </div>
            <div class="inline">
              <label style="margin:0; min-width:92px;">定时执行时间</label>
              <input type="time" name="schedule_time" value="{escape(settings.schedule_time)}" style="max-width:180px;">
              <span style="color:#6b7a8f;font-size:13px;">每点一次“保存定时”，就会把当前整套配置新增为一条独立任务。</span>
            </div>
          </div>
        </div>
        <div class="actions">
          <div class="buttons">
            <button id="startBtn" type="submit">一键启动</button>
            <button id="scheduleBtn" class="secondary" type="button">保存定时</button>
            <button id="shutdownBtn" class="ghost" type="button">退出</button>
          </div>
          <div class="status" id="status">准备就绪</div>
        </div>
      </form>
    </section>

    <section class="card list-card">
      <div class="list-head">
        <div>
          <div class="list-title">已保存定时任务</div>
          <div class="list-sub" id="scheduleSummary">暂无定时任务</div>
        </div>
      </div>
      <div class="results" id="scheduleList"><div class="empty">还没有保存定时任务。</div></div>
    </section>

    <section class="card list-card">
      <div class="list-head">
        <div>
          <div class="list-title">通过候选人</div>
          <div class="list-sub" id="matchSummary">暂无匹配结果</div>
        </div>
      </div>
      <div class="results" id="matchList"><div class="empty">本轮还没有 AI 通过名单。</div></div>
    </section>

    <section class="card list-card">
      <div class="list-head">
        <div>
          <div class="list-title">消息跟进</div>
          <div class="list-sub" id="followupSummary">消息监听尚未启动</div>
        </div>
      </div>
      <div class="results" id="followupList"><div class="empty">还没有交换手机或回复监听数据。</div></div>
    </section>
  </main>

  <script>
    const form = document.getElementById('form');
    const startBtn = document.getElementById('startBtn');
    const scheduleBtn = document.getElementById('scheduleBtn');
    const shutdownBtn = document.getElementById('shutdownBtn');
    const statusBox = document.getElementById('status');
    const matchSummary = document.getElementById('matchSummary');
    const matchList = document.getElementById('matchList');
    const scheduleSummary = document.getElementById('scheduleSummary');
    const scheduleList = document.getElementById('scheduleList');
    const followupSummary = document.getElementById('followupSummary');
    const followupList = document.getElementById('followupList');

    function renderMatches(items) {{
      if (!items.length) {{
        matchList.innerHTML = '<div class="empty">本轮还没有 AI 通过名单。</div>';
        return;
      }}
      matchList.innerHTML = items.map((item) => {{
        const companies = item.companies && item.companies.length ? item.companies.join(' / ') : '暂无';
        const tags = item.career_tags && item.career_tags.length ? item.career_tags.join(' / ') : '暂无';
        const meta = [
          item.location || '地点未知',
          item.work_years || '年限未知',
          item.degree || '学历未知',
          item.gender || '性别未知'
        ].join(' | ');
        const target = [item.target_role || '目标岗位未知', item.expected_salary || '薪资未知'].join(' | ');
        return `
          <article class="result-item">
            <div class="result-top">
              <div class="result-name">#${{item.page_number}}-${{item.page_list_index || item.list_index}} ${{item.name || '未命名候选人'}}</div>
              <div class="score">匹配度 ${{item.match_score || 0}}</div>
            </div>
            <div class="meta">${{meta}}</div>
            <div class="meta">${{target}}</div>
            <div class="meta">近期公司：${{companies}}</div>
            <div class="meta">职业标签：${{tags}}</div>
            <div class="reason">AI 评价：${{item.reason || '暂无评价'}}</div>
          </article>
        `;
      }}).join('');
    }}

    function renderSchedules(items) {{
      if (!items.length) {{
        scheduleSummary.textContent = '暂无定时任务';
        scheduleList.innerHTML = '<div class="empty">还没有保存定时任务。</div>';
        return;
      }}
      scheduleSummary.textContent = `共 ${{items.length}} 个定时任务，最近一次执行：${{items[0].next_run}}`;
      scheduleList.innerHTML = items.map((item) => `
        <article class="schedule-item">
          <div class="schedule-top">
            <div class="schedule-time">${{item.time_text}} <span class="schedule-badge">下次：${{item.next_run}}</span></div>
            <div class="schedule-actions">
              <button type="button" class="danger" onclick="deleteSchedule('${{item.id}}')">删除</button>
            </div>
          </div>
          <div class="meta">${{item.summary || '未命名任务'}}</div>
          <div class="meta">创建时间：${{item.created_at}}</div>
        </article>
      `).join('');
    }}

    function renderFollowup(data) {{
      const runningText = data.running ? '运行中' : '未运行';
      const contactedCount = data.contacted_count || 0;
      const updatedAt = data.updated_at || '暂无';
      followupSummary.textContent = `消息监听：${{runningText}} | 已记录沟通人：${{contactedCount}} | 最近更新：${{updatedAt}}`;

      const targets = data.targets || [];
      if (!targets.length) {{
        followupList.innerHTML = '<div class="empty">还没有交换手机或回复监听数据。</div>';
        return;
      }}

      followupList.innerHTML = targets.map((item) => {{
        const exchange = item.phone_exchange_status || 'pending';
        const preview = item.last_session_preview || '暂无会话预览';
        const replyAt = item.reply_detected_at || '暂无回复';
        const messages = (item.last_messages || []).slice(-2).join(' / ') || '暂无抓取消息';
        return `
          <article class="result-item">
            <div class="result-top">
              <div class="result-name">${{item.name || '未命名候选人'}}</div>
              <div class="score">换电话：${{exchange}}</div>
            </div>
            <div class="meta">最近预览：${{preview}}</div>
            <div class="meta">最近回复时间：${{replyAt}}</div>
            <div class="reason">最近消息：${{messages}}</div>
          </article>
        `;
      }}).join('');
    }}

    async function refreshStatus() {{
      try {{
        const res = await fetch('/status');
        const data = await res.json();
        startBtn.disabled = !!data.running;
        startBtn.textContent = data.running ? '运行中...' : '一键启动';
        const scheduleText = data.schedule_count ? ` | 定时任务: ${{data.schedule_count}} 个 | 最近: ${{data.scheduled_for}}` : '';
        const followupText = data.message_followup_running ? ' | 消息监听运行中' : '';
        statusBox.textContent = data.error ? ('失败: ' + data.error) : ((data.status || '准备就绪') + scheduleText + followupText);
      }} catch (error) {{
        statusBox.textContent = '状态获取失败：' + error.message;
      }}
    }}

    async function refreshMatches() {{
      try {{
        const res = await fetch('/api/matches');
        const data = await res.json();
        matchSummary.textContent = data.summary || '暂无匹配结果';
        renderMatches(data.matched_candidates || []);
      }} catch (error) {{
        matchSummary.textContent = '匹配结果读取失败';
        matchList.innerHTML = '<div class="empty">读取匹配结果失败：' + error.message + '</div>';
      }}
    }}

    async function refreshSchedules() {{
      try {{
        const res = await fetch('/api/schedules');
        const data = await res.json();
        renderSchedules(data.items || []);
      }} catch (error) {{
        scheduleSummary.textContent = '定时任务读取失败';
        scheduleList.innerHTML = '<div class="empty">读取定时任务失败：' + error.message + '</div>';
      }}
    }}

    async function refreshFollowup() {{
      try {{
        const res = await fetch('/api/followup');
        const data = await res.json();
        renderFollowup(data);
      }} catch (error) {{
        followupSummary.textContent = '消息跟进读取失败';
        followupList.innerHTML = '<div class="empty">读取消息跟进失败：' + error.message + '</div>';
      }}
    }}

    async function postForm(path) {{
      try {{
        const body = new URLSearchParams(new FormData(form));
        const res = await fetch(path, {{ method: 'POST', body }});
        const data = await res.json();
        if (!data.ok) statusBox.textContent = data.error || '操作失败';
      }} catch (error) {{
        statusBox.textContent = '请求失败：' + error.message;
      }}
      await refreshStatus();
      await refreshMatches();
      await refreshSchedules();
      await refreshFollowup();
    }}

    async function deleteSchedule(id) {{
      try {{
        const res = await fetch('/schedule/delete?id=' + encodeURIComponent(id), {{ method: 'POST' }});
        const data = await res.json();
        if (!data.ok) statusBox.textContent = data.error || '删除失败';
      }} catch (error) {{
        statusBox.textContent = '删除失败：' + error.message;
      }}
      await refreshStatus();
      await refreshSchedules();
      await refreshFollowup();
    }}
    window.deleteSchedule = deleteSchedule;

    form.addEventListener('submit', async (event) => {{
      event.preventDefault();
      await postForm('/start');
    }});

    scheduleBtn.addEventListener('click', async () => {{
      await postForm('/schedule');
    }});

    shutdownBtn.addEventListener('click', async () => {{
      try {{
        await fetch('/shutdown', {{ method: 'POST' }});
        statusBox.textContent = '正在退出 UI...';
      }} catch (error) {{
        statusBox.textContent = '退出失败：' + error.message;
      }}
    }});

    refreshStatus();
    refreshMatches();
    refreshSchedules();
    refreshFollowup();
    setInterval(refreshStatus, 1200);
    setInterval(refreshMatches, 2500);
    setInterval(refreshSchedules, 3500);
    setInterval(refreshFollowup, 2500);
  </script>
</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        return

    def send_text(self, text: str, content_type: str = "text/html; charset=utf-8"):
        body = text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/status"):
            self.send_text(json.dumps(get_state(), ensure_ascii=False), "application/json; charset=utf-8")
            return
        if self.path.startswith("/api/matches"):
            self.send_text(json.dumps(load_match_results(), ensure_ascii=False), "application/json; charset=utf-8")
            return
        if self.path.startswith("/api/schedules"):
            self.send_text(json.dumps(schedule_payload(), ensure_ascii=False), "application/json; charset=utf-8")
            return
        if self.path.startswith("/api/followup"):
            self.send_text(json.dumps(load_followup_payload(), ensure_ascii=False), "application/json; charset=utf-8")
            return
        self.send_text(html_page(load_settings()))

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        length = int(self.headers.get("Content-Length", "0"))
        settings = parse_settings(self.rfile.read(length)) if length else load_settings()

        if parsed.path == "/start":
            save_settings(settings)
            if get_state()["running"]:
                self.send_text(json.dumps({"ok": False, "error": "任务正在运行中。"}, ensure_ascii=False), "application/json; charset=utf-8")
                return
            threading.Thread(target=run_worker, args=(settings, "手动启动"), daemon=True).start()
            self.send_text(json.dumps({"ok": True}, ensure_ascii=False), "application/json; charset=utf-8")
            return

        if parsed.path == "/schedule":
            save_settings(settings)
            try:
                entry = make_schedule_entry(settings)
                entries = upsert_schedule(entry)
                reload_all_schedules()
                next_run = schedule_entry_next_run(entry).strftime("%Y-%m-%d %H:%M")
                set_status(f"已保存定时任务，共 {len(entries)} 个。新增任务下次执行：{next_run}")
                self.send_text(json.dumps({"ok": True, "id": entry.id}, ensure_ascii=False), "application/json; charset=utf-8")
            except Exception as exc:
                self.send_text(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), "application/json; charset=utf-8")
            return

        if parsed.path == "/schedule/delete":
            params = urllib.parse.parse_qs(parsed.query)
            schedule_id = str((params.get("id") or [""])[0]).strip()
            if not schedule_id:
                self.send_text(json.dumps({"ok": False, "error": "缺少定时任务 id。"}, ensure_ascii=False), "application/json; charset=utf-8")
                return
            cancel_schedule_timer(schedule_id)
            entries = delete_schedule(schedule_id)
            reload_all_schedules()
            set_status(f"已删除定时任务，剩余 {len(entries)} 个。")
            self.send_text(json.dumps({"ok": True}, ensure_ascii=False), "application/json; charset=utf-8")
            return

        if parsed.path == "/shutdown":
            cancel_all_schedules()
            self.send_text(json.dumps({"ok": True}, ensure_ascii=False), "application/json; charset=utf-8")
            threading.Thread(target=shutdown_server, daemon=True).start()
            return

        self.send_error(404)


def shutdown_server():
    time.sleep(0.3)
    if SERVER:
        SERVER.shutdown()


def request_local(path: str, method: str = "GET", data: bytes | None = None, timeout: float = 1.0):
    request = urllib.request.Request(
        url=path,
        data=data,
        method=method,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8")


def debug_browser_ready(timeout: float = 1.5) -> bool:
    for host in ("localhost", "127.0.0.1"):
        try:
            request_local(f"http://{host}:{DEFAULT_DEBUG_PORT}/json/version", timeout=timeout)
            return True
        except Exception:
            continue
    return False


def debug_browser_ready_stable(checks: int = 3, interval: float = 0.6, timeout: float = 1.0) -> bool:
    hits = 0
    for _ in range(checks):
        if debug_browser_ready(timeout=timeout):
            hits += 1
            time.sleep(interval)
            continue
        return False
    return hits == checks


def verify_debug_browser_async():
    def worker():
        for _ in range(20):
            if debug_browser_ready_stable(checks=2, interval=0.3, timeout=0.8):
                append_runtime_log(f"debug browser ready on {DEFAULT_DEBUG_PORT}")
                set_status("准备就绪")
                return
            time.sleep(0.5)
        append_runtime_log(f"debug browser not ready on {DEFAULT_DEBUG_PORT} after async verification")
        set_status("浏览器已尝试启动，但暂未确认连接成功，请检查 Edge/Chrome 是否已弹出。")

    threading.Thread(target=worker, daemon=True).start()


def launch_browser_candidate(browser_path: str, profile_dir: Path, extra_args: list[str] | None = None) -> bool:
    launch_args = [
        browser_path,
        f"--remote-debugging-port={DEFAULT_DEBUG_PORT}",
        f"--user-data-dir={profile_dir}",
        "--new-window",
        DEFAULT_MAIMAI_URL,
    ]
    if extra_args:
        launch_args[1:1] = extra_args
    append_runtime_log(f"launch candidate args={' '.join(launch_args[1:])}")
    subprocess.Popen(
        launch_args,
        env=browser_launch_env(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(18):
        if debug_browser_ready_stable(checks=2, interval=0.3, timeout=0.8):
            append_runtime_log(f"browser candidate ready: {browser_path}")
            return True
        time.sleep(0.5)
    append_runtime_log(f"browser candidate not ready: {browser_path}")
    return False


def shutdown_existing_ui_instances():
    for port in range(PORT, PORT + 10):
        base_url = f"http://{HOST}:{port}"
        try:
            request_local(f"{base_url}/status", timeout=0.5)
        except Exception:
            continue
        try:
            request_local(f"{base_url}/shutdown", method="POST", data=b"", timeout=0.8)
        except urllib.error.URLError:
            pass
        except Exception:
            continue
        time.sleep(0.4)


def ensure_debug_browser():
    if debug_browser_ready_stable(checks=2, interval=0.4, timeout=0.8):
        append_runtime_log(f"debug browser already ready on {DEFAULT_DEBUG_PORT}")
        return
    reset_windows_dll_dir()
    runtime_dir = browser_runtime_dir()
    profile_dir = runtime_dir / f"browser-profile-{DEFAULT_DEBUG_PORT}"
    profile_dir.mkdir(parents=True, exist_ok=True)
    append_runtime_log(f"ensure_debug_browser runtime_dir={runtime_dir} profile_dir={profile_dir}")
    launch_variants = [
        [],
        ["--remote-debugging-address=127.0.0.1"],
    ]
    for browser_path in BROWSER_CANDIDATES:
        if not Path(browser_path).exists():
            append_runtime_log(f"browser candidate missing: {browser_path}")
            continue
        for extra_args in launch_variants:
            try:
                append_runtime_log(f"trying browser candidate: {browser_path} extra={extra_args}")
                set_status("正在准备浏览器环境")
                if launch_browser_candidate(browser_path, profile_dir, extra_args):
                    set_status("准备就绪")
                    return
            except Exception as exc:
                append_runtime_log(f"spawn failed for {browser_path} extra={extra_args}: {exc}")
                continue
    append_runtime_log("no browser candidate could be started")
    verify_debug_browser_async()
    set_status("未能自动确认浏览器连接，请稍后重试。")


def migrate_legacy_schedule_if_needed():
    settings = load_settings()
    entries = load_schedules()
    if entries:
        return
    if not settings.schedule_enabled or not settings.schedule_time:
        return
    try:
        upsert_schedule(make_schedule_entry(settings))
        settings.schedule_enabled = False
        save_settings(settings)
        append_runtime_log("legacy single schedule migrated to scheduled_tasks.json")
    except Exception as exc:
        append_runtime_log(f"legacy schedule migration failed: {exc}")


def launch_app():
    global SERVER
    append_runtime_log("launch_app called")
    shutdown_existing_ui_instances()
    ensure_debug_browser()
    migrate_legacy_schedule_if_needed()
    refresh_schedule_state()
    if get_state()["schedule_count"]:
        reload_all_schedules()
    settings = load_settings()
    for port in range(PORT, PORT + 10):
        try:
            SERVER = ThreadingHTTPServer((HOST, port), Handler)
            break
        except OSError:
            SERVER = None
    if SERVER is None:
        raise RuntimeError("本地 UI 端口 18765-18774 都被占用，请关闭旧程序后重试。")
    url = f"http://{HOST}:{SERVER.server_port}"
    print(f"[INFO] UI opened at {url}")
    threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        SERVER.serve_forever()
    finally:
        cancel_all_schedules()
