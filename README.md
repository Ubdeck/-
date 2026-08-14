# 招聘工具

一个面向 Windows 的多渠道招聘自动化桌面工具，通过 DrissionPage 接管本机已登录的 Chromium 浏览器。桌面界面由 `pywebview` 承载，当前支持猎聘已跑通流程，并开始接入脉脉招聘渠道；DeepSeek 用于候选人匹配判断。

> 项目目前处于 Alpha 阶段。它依赖招聘网站的页面结构，不使用平台官方 API；网站改版后可能需要更新选择器。真实发送会对候选人产生实际影响，开发和验证时请保持测试模式。

## 功能

- 保存多套招聘任务，在猎聘和脉脉等渠道之间切换，并按多个时间点运行。
- 自动填写关键词、城市、学历、经历、行业和职能等筛选条件。
- 提取候选人简历，使用 DeepSeek 按招聘要求判断匹配度。
- 对匹配候选人发起沟通，并按平台能力索要简历或手机号。
- 在桌面操作台查看执行日志、AI 结论和沟通状态。
- 使用固定浏览器资料目录保留登录状态。

## 运行要求

- Windows 10/11
- Python 3.10 或更高版本
- Microsoft Edge 或兼容 Chromium 浏览器
- 有效的招聘平台账号（猎聘、脉脉等）
- DeepSeek API Key

## 快速开始

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run.py
```

也可以按标准 Python 包安装：

```powershell
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\recruit-assistant.exe
```

首次运行会打开调试浏览器。请在该浏览器中完成平台登录；相同调试端口会复用相同浏览器资料。

## API 配置

可以在操作台本地填写 DeepSeek Key，也可以从 [.env.example](.env.example) 创建 `runtime/.env`：

```dotenv
DEEPSEEK_API_KEY=your-key
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat
```

`runtime/` 已被 Git 忽略，但其中可能包含 API Key、筛选条件、简历和沟通记录。不要将该目录作为附件上传，更多说明见 [SECURITY.md](SECURITY.md)。

## 项目结构

```text
src/recruit_assistant/
  desktop_app.py              # pywebview 桌面窗口
  app_backend.py              # 本地 API、任务、调度和浏览器生命周期
  web/                        # HTML、CSS 和前端交互
  platforms/
    liepin/
      automation.py           # 稳定的对外门面和 CLI
      workflow.py             # 猎聘整轮流程编排
      browser.py              # CDP 浏览器连接
      job_manager.py          # 职位管理页
      candidates.py           # 候选人列表和简历提取
      ai_matcher.py           # DeepSeek 匹配
      communication.py        # 沟通及联系方式请求
      filters.py              # 搜索筛选控件
      models.py               # 配置和进度模型
    maimai/
      automation.py           # 脉脉招聘流程门面，当前先接入人才主界面
      browser.py              # 脉脉 CDP 浏览器连接
      constants.py            # 脉脉主界面 URL 和运行目录
tests/                        # 不触发真实平台操作的单元测试
```

详细调用关系见 [架构说明](docs/ARCHITECTURE.md)，修改和验证方法见 [开发说明](docs/DEVELOPMENT.md)。

## 开发验证

```powershell
$env:PYTHONPATH = (Resolve-Path .\src).Path
.\.venv\Scripts\python.exe -m unittest discover -s tests -v

$files = @(Get-ChildItem .\src -Recurse -Filter '*.py' | Select-Object -ExpandProperty FullName)
$files += (Resolve-Path .\run.py).Path
.\.venv\Scripts\python.exe -m py_compile @files
```

自动化测试不连接招聘网站，也不会发送消息。真实页面验证必须手动进行，并先关闭自动沟通。

## 打包

```powershell
powershell -ExecutionPolicy Bypass -File .\build_exe.ps1
```

产物为 `dist/招聘工具.exe`。构建产物不应提交到源码仓库，适合通过 GitHub Releases 单独发布。

## 自动更新

软件会通过 GitHub Releases 检查最新版本。发布新版本时创建 `v0.1.1` 这类 tag，GitHub Actions 会自动构建 Windows exe 并上传到对应 Release；同事安装过一次带更新功能的版本后，可以在软件里点击“检查更新”完成下载、替换和重启。

更新测试记录：`v0.1.2` 用于验证 GitHub Release 自动更新链路。

## 已知限制

- 页面选择器依赖招聘平台当前 DOM，网站改版可能导致流程失效。
- 本地 API 没有鉴权，只应监听 `127.0.0.1`。
- 实际发送、索要联系方式和登录态验证无法在 CI 中自动完成。
- 当前任务配置文件是本地 JSON，尚未提供加密存储。

## 许可证

仓库目前未附带开源许可证。在公开发布前，请根据期望的使用和再分发方式选择许可证。
