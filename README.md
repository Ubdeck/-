# 招聘软件助手

这是一个本地 Windows 桌面应用。界面由 `pywebview` 承载，后端复用现有浏览器自动化逻辑；用户看到的是桌面窗口，不需要再打开浏览器 Web 控制台。

## 当前工作区结构

```text
猎聘/
  run.py                         # 桌面应用入口
  build_exe.ps1                  # 打包脚本，生成无黑框 exe
  requirements.txt               # Python 依赖
  README.md                      # 项目说明
  src/
    recruit_assistant/
      desktop_app.py             # pywebview 桌面窗口
      app_backend.py             # 本地后端、任务调度、内嵌 UI/API
      platforms/
        liepin/                  # 猎聘平台核心代码
          automation.py          # 猎聘自动化主流程
        maimai/                  # 脉脉平台核心代码
          bridge.py              # 脉脉平台桥接层
          worker.py              # 脉脉子进程入口
          src/maimai_auto/       # 脉脉原核心模块，保留原导入结构降低迁移风险
          legacy/                # 脉脉原网页自动化脚本
          config/                # 脉脉默认配置
  runtime/
    maimai/                      # 脉脉运行数据
    *.json / *.log               # 猎聘运行数据
  dist/                          # 打包产物
  backups/                       # 历史备份包
  脉脉自动化/                    # 旧独立项目残留，仅作备份，不参与主程序运行
```

## 运行

```powershell
.\.venv\Scripts\python.exe run.py
```

默认连接浏览器调试端口 `9225`。

## 打包

```powershell
powershell -ExecutionPolicy Bypass -File .\build_exe.ps1
```

生成：

- `dist/招聘软件助手.exe`
- `dist/招聘软件助手Portable/run_portable.bat`

## 维护约定

- 猎聘主流程只改 `src/recruit_assistant/platforms/liepin/automation.py`。
- 脉脉主流程只改 `src/recruit_assistant/platforms/maimai/`。
- 桌面 UI 和本地 API 只改 `src/recruit_assistant/app_backend.py`。
- 运行数据统一写入 `runtime/`，打包产物统一写入 `dist/`。
- 源码文件统一使用 UTF-8，规则见 `.editorconfig` 和 `.gitattributes`。
- 根目录下旧的 `脉脉自动化/` 不再被主程序和打包脚本引用，后续确认无用后可手动删除。
