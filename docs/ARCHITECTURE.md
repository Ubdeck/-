# 应用架构

## 总体调用链

```text
run.py
  -> desktop_app.main()
     -> app_backend.start_server()
     -> pywebview.create_window()

网页操作台
  -> app_backend.Handler
  -> AppState.run_task()
     -> 平台分发
        -> 猎聘: LiepinSearchPage
        -> 脉脉: MaimaiRecruitPage
```

`app_backend.py` 只负责本地 HTTP API、任务配置、定时调度、浏览器启动和平台分发。页面行为必须留在 `platforms/`，前端结构、样式和交互分别放在 `web/index.html`、`web/styles.css` 和 `web/app.js`。

## 状态与并发

`AppState` 是桌面进程内的状态中心。任务配置持久化到 `runtime/recruit_assistant_config.json`；旧 `runtime/liepin_web_config.json` 会作为迁移来源读取。日志和结果通过 `/api/state` 返回前端。`run_lock` 保证同一时间只有一个任务执行，`task_stop_event` 用于协作式停止。

定时器每 10 秒检查一次任务，以日期和分钟组合去重。定时任务与手动任务共用同一个执行锁。

## 浏览器与登录状态

应用通过 CDP 连接调试浏览器，并使用按端口区分的固定 `--user-data-dir`。Cookie 和登录状态属于浏览器资料目录，DrissionPage 只负责接管页面。

浏览器进程的创建和诊断位于 `app_backend.py`。具体平台的连接适配位于各自的 `platforms/<platform>/browser.py`。

## 猎聘模块

`platforms/liepin/automation.py` 是兼容门面，对外继续导出 `LiepinSearchPage`、`SearchFilters` 和浏览器连接函数。内部按职责拆分：

| 模块 | 职责 |
| --- | --- |
| `workflow.py` | 填写条件、处理候选人和汇总整轮结果 |
| `job_manager.py` | 职位管理页、分页和职位卡片提取 |
| `candidates.py` | 候选人切换、等待和简历提取 |
| `ai_matcher.py` | DeepSeek 请求、提示词、解析和日志 |
| `communication.py` | 立即沟通、职位选择、聊天及联系方式请求 |
| `filters.py` | 城市、行业、职能和下拉筛选控件 |
| `browser.py` | CDP 连接和浏览器就绪检查 |
| `models.py` | `SearchFilters` 和进度事件 |

这些模块以 mixin 组合成一个门面，目的是保持外部 API 稳定，同时让页面改版影响集中在对应模块。跨模块调用通过 `LiepinSearchPage` 门面解析，不应在 mixin 之间直接互相实例化。

## 脉脉模块

`platforms/maimai/automation.py` 是脉脉招聘渠道的新门面。当前第一阶段只负责接管浏览器、打开 `https://maimai.cn/ent/v41/recruit/talents?pid=&tab=1` 并回传页面状态；搜索筛选、候选人提取、AI 匹配和沟通会按真实页面验证逐步补齐。

## 数据目录

- `runtime/recruit_assistant_config.json`：操作台任务配置，可能包含 API Key。
- `runtime/liepin_web_config.json`：旧猎聘配置文件，存在时作为迁移来源读取。
- `runtime/liepin_jobs.json`：猎聘职位缓存。
- `runtime/`：平台日志、候选人和 AI 结果。
- `dist/`：本地构建产物，不提交 Git。
- `backups/`：人工备份，不参与导入、测试和打包。
