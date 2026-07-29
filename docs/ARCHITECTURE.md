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
     -> 猎聘: LiepinSearchPage
     -> 脉脉: bridge.run_pipeline_subprocess()
        -> worker.py
        -> bridge.run_pipeline()
```

`app_backend.py` 同时承担本地 HTTP 服务、操作台页面、任务配置、定时调度、调试浏览器启动和平台分发。平台具体行为留在 `platforms/`，不要把页面选择器继续加回后端。

## 浏览器与登录状态

应用启动 Edge 时使用固定的 `--user-data-dir`，目录名称包含调试端口。相同端口会复用相同浏览器资料，因此 Cookie 和登录状态通常可以保留。手动清理浏览器资料目录、换端口，或平台主动让登录失效时才需要重新登录。

DrissionPage 不负责保存账号，它只连接调试端口并操作现有页面。调试浏览器的创建和检查在 `app_backend.py`，平台连接方法分别在猎聘 `automation.py` 和脉脉 `browser.py`。

## 猎聘流程

猎聘目前集中在一个文件：`platforms/liepin/automation.py`。主要顺序是：

1. 连接调试浏览器并进入搜索页。
2. 获取职位、填写筛选条件并搜索。
3. 逐个提取候选人简历。
4. 调用 DeepSeek 判断匹配度。
5. 对通过候选人发起沟通和后续动作。

这个文件仍然偏大，但内部方法都属于同一个页面对象。后续拆分时应按“搜索页、候选人详情、聊天页、AI”拆，不要按临时问题随意拆文件。

## 脉脉流程

脉脉入口是 `bridge.run_pipeline()`：

1. `automation/search.py` 清理并填写筛选项。
2. `automation/candidates.py` 提取当前页候选人和简历。
3. `matching.py` 调用 DeepSeek，累积通过和拒绝结果。
4. `automation/communication.py` 只处理当前页通过的人选。
5. 实际发送时，沟通弹窗点击“发送后继续沟通”。
6. 跳到消息会话页后，`phone_exchange.py` 等待候选人姓名和工具栏稳定，再点击“交换手机”。
7. 确认交换状态后关闭消息页，恢复人才列表并继续下一人。
8. 当前页完成后由 `candidates.goto_next_page()` 翻页。达到设定页数后直接结束，不监听后续回复。

`bridge.py` 是流程编排层，不应放 CSS 选择器。`automation/` 和 `phone_exchange.py` 是页面操作层。

## 为什么脉脉使用 worker

脉脉流程在独立子进程中运行，主要是为了隔离长时间页面操作和 DrissionPage 状态，避免脉脉异常阻塞整个桌面操作台。主进程可以持续读取日志、响应停止按钮，并在超时时终止 worker。

源码模式使用：

```text
python -m recruit_assistant.platforms.maimai.worker <config.json> <result.json>
```

打包后同一个 EXE 使用隐藏参数 `--maimai-worker` 进入 worker 模式，不会再打开第二个操作台窗口。

## 数据目录

- `runtime/liepin_web_config.json`：操作台任务配置。
- `runtime/`：猎聘日志、候选人、AI 结果。
- `runtime/maimai/`：脉脉配置、候选人、匹配和沟通记录。
- `dist/`：可分发 EXE。
- `backups/`：人工保留的历史备份，不参与导入和打包。
