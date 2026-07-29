# 开发与排查

## 修改前先定位层级

| 问题 | 首先检查 |
| --- | --- |
| 操作台按钮、任务配置、定时运行 | `src/recruit_assistant/app_backend.py` |
| 浏览器无法启动、登录不保留、端口异常 | `app_backend.py` 中的调试浏览器函数 |
| 猎聘筛选、翻页、聊天页面行为 | `platforms/liepin/automation.py` |
| 脉脉整轮流程顺序 | `platforms/maimai/bridge.py` |
| 脉脉搜索条件 | `platforms/maimai/automation/search.py` |
| 脉脉简历提取与翻页 | `platforms/maimai/automation/candidates.py` |
| 脉脉 AI 筛选 | `platforms/maimai/matching.py` |
| 脉脉沟通弹窗 | `platforms/maimai/automation/communication.py` |
| 脉脉消息页交换手机 | `platforms/maimai/phone_exchange.py` |

## DrissionPage 修改原则

1. 先确认当前 `page.url` 和页面是否在 iframe 内。元素在 iframe 中时，要对 frame 调用 `ele()` 或 `run_js()`。
2. 优先等待可验证状态，不要只增加固定 `sleep()`。例如等待候选人姓名等于目标姓名、工具栏稳定、分页标记变化。
3. 点击之后验证结果。交换手机目前用“申请中”按钮或消息正文确认成功，不能把“执行过 click”当作成功。
4. 页面列表是虚拟滚动时，DOM 中只有可见项。滚动后必须重新获取元素，不能长期保存旧元素对象。
5. 页面跳转或刷新后旧元素可能失效。捕获 `ContextLostError` 时应重新取 page/frame/element，而不是继续使用旧引用。

## 常用检查

编译全部 Python 文件：

```powershell
$files = @(Get-ChildItem .\src -Recurse -Filter '*.py' | Select-Object -ExpandProperty FullName)
$files += (Resolve-Path .\run.py).Path
.\.venv\Scripts\python.exe -m py_compile @files
```

检查脉脉模块能否导入：

```powershell
$env:PYTHONPATH = (Resolve-Path .\src).Path
.\.venv\Scripts\python.exe -c "from recruit_assistant.platforms.maimai import bridge; print(bridge.maimai_options())"
```

检查 worker 入口。缺少参数时返回用法和退出码 2 是正常行为：

```powershell
$env:PYTHONPATH = (Resolve-Path .\src).Path
.\.venv\Scripts\python.exe -m recruit_assistant.platforms.maimai.worker
```

## 测试边界

- 静态重构先做编译、导入和 worker 入口检查，不要自动向真实候选人发消息。
- 页面流程测试先关闭“实际发送”，确认搜索、提取、AI 和翻页。
- 必须验证真实发送时，限定职位、页数和候选人数，并观察每一步结果。
- 不要为了清日志或删除锁定文件终止正在使用的 9225 浏览器。

## 打包验证

```powershell
powershell -ExecutionPolicy Bypass -File .\build_exe.ps1
.\dist\招聘软件助手.exe --maimai-worker
```

第二条命令应输出 worker 用法后退出，不应打开新的操作台。最终再从 EXE 正常启动一次，确认操作台和调试浏览器能打开。
