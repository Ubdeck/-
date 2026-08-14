# 开发与排查

## 修改前先定位层级

| 问题 | 首先检查 |
| --- | --- |
| 操作台页面结构 | `src/recruit_assistant/web/index.html` |
| 操作台样式 | `src/recruit_assistant/web/styles.css` |
| 操作台按钮和前端状态 | `src/recruit_assistant/web/app.js` |
| 任务配置、本地 API、定时运行 | `src/recruit_assistant/app_backend.py` |
| 浏览器无法启动、登录不保留、端口异常 | `app_backend.py` 中的调试浏览器函数 |
| 平台切换和渠道分发 | `app_backend.py` 中的 `PLATFORM_DEFS`、`normalize_config()` 和 `AppState.run_task()` |
| 猎聘整轮流程 | `platforms/liepin/workflow.py` |
| 猎聘职位管理页 | `platforms/liepin/job_manager.py` |
| 猎聘候选人和简历 | `platforms/liepin/candidates.py` |
| 猎聘 AI 匹配 | `platforms/liepin/ai_matcher.py` |
| 猎聘搜索筛选控件 | `platforms/liepin/filters.py` |
| 猎聘沟通和联系方式 | `platforms/liepin/communication.py` |
| 脉脉主界面和流程骨架 | `platforms/maimai/automation.py` |
| 脉脉浏览器连接 | `platforms/maimai/browser.py` |

## DrissionPage 修改原则

1. 先确认当前 `page.url` 和页面是否在 iframe 内。元素在 iframe 中时，要对 frame 调用 `ele()` 或 `run_js()`。
2. 优先等待可验证状态，不要只增加固定 `sleep()`。例如等待候选人姓名等于目标姓名、工具栏稳定、分页标记变化。
3. 点击之后验证结果。交换手机目前用“申请中”按钮或消息正文确认成功，不能把“执行过 click”当作成功。
4. 页面列表是虚拟滚动时，DOM 中只有可见项。滚动后必须重新获取元素，不能长期保存旧元素对象。
5. 页面跳转或刷新后旧元素可能失效。捕获 `ContextLostError` 时应重新取 page/frame/element，而不是继续使用旧引用。

## 常用检查

运行不连接真实招聘网站的单元测试：

```powershell
$env:PYTHONPATH = (Resolve-Path .\src).Path
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

编译全部 Python 文件：

```powershell
$files = @(Get-ChildItem .\src -Recurse -Filter '*.py' | Select-Object -ExpandProperty FullName)
$files += (Resolve-Path .\run.py).Path
.\.venv\Scripts\python.exe -m py_compile @files
```

## 测试边界

- 静态重构先做编译和导入检查，不要自动向真实候选人发消息。
- 页面流程测试先关闭自动沟通，确认当前渠道的打开、搜索、提取、AI 和翻页。
- 必须验证真实发送时，限定职位、页数和候选人数，并观察每一步结果。
- 不要为了清日志或删除锁定文件终止正在使用的 9225 浏览器。

## 打包验证

```powershell
powershell -ExecutionPolicy Bypass -File .\build_exe.ps1
```

最终从 EXE 正常启动一次，确认操作台和调试浏览器能打开。
