# 招聘软件助手

这是一个 Windows 桌面招聘自动化工具，目前支持猎聘和脉脉。桌面窗口由 `pywebview` 承载，自动化通过 DrissionPage 连接同一个调试浏览器。浏览器数据保存在固定用户目录中，正常情况下登录状态会跨应用重启保留。

## 快速开始

直接运行已有虚拟环境：

```powershell
.\.venv\Scripts\python.exe run.py
```

PowerShell 中如需激活虚拟环境，应运行：

```powershell
.\.venv\Scripts\Activate.ps1
```

不激活也不影响运行，直接使用 `.venv\Scripts\python.exe` 更明确。

## 项目结构

```text
招聘工具/
  run.py                         # 源码运行入口，也是打包后的入口
  build_exe.ps1                  # 单文件 EXE 打包脚本
  requirements.txt               # 运行依赖
  docs/                          # 架构、开发和使用说明
  src/recruit_assistant/
    desktop_app.py               # pywebview 窗口
    app_backend.py               # 内嵌网页、API、任务和浏览器生命周期
    platforms/
      liepin/automation.py       # 猎聘完整自动化流程
      maimai/
        bridge.py                # 脉脉流程编排和 worker 管理
        worker.py                # 脉脉子进程入口
        settings.py              # 脉脉配置模型
        matching.py              # DeepSeek 简历筛选
        phone_exchange.py        # 指定候选人的交换手机流程
        automation/              # 搜索、简历提取、翻页和沟通页面操作
  runtime/                       # 配置、日志和流程中间结果，不提交 Git
  dist/                          # 打包产物
  backups/                       # 历史备份，不参与运行
```

详细调用关系见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)，修改和验证方法见 [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)。

## 打包

```powershell
powershell -ExecutionPolicy Bypass -File .\build_exe.ps1
```

脚本会先检查 `.venv` 和依赖。依赖已安装时不会重复执行 `pip install`。最终只生成：

```text
dist/招聘软件助手.exe
```

## 维护边界

- 通用 UI、任务调度、浏览器启动问题看 `app_backend.py`。
- 猎聘页面行为只在 `platforms/liepin/automation.py` 修改。
- 脉脉流程顺序看 `platforms/maimai/bridge.py`，页面动作看 `platforms/maimai/automation/`。
- 脉脉 AI 筛选只在 `platforms/maimai/matching.py` 修改。
- 脉脉交换手机只在 `platforms/maimai/phone_exchange.py` 修改。
- `runtime/`、`dist/` 和 `backups/` 不是源码，不要从这些目录复制代码回来修改。
- WorkBuddy、旧版独立脉脉 UI、兼容包装层和最终消息监听均已移除。
