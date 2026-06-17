# Liepin Automation

猎聘自动化本地控制台。

## 启动

运行 `run.py` 或打包后的 `dist\\LiepinAutomation\\run_portable.bat` 后，程序会：

1. 自动启动 Microsoft Edge，并开放本地调试端口 `9224`。
2. 打开猎聘搜索页。
3. 启动本地 Web 控制台：`http://127.0.0.1:8765`。

首次在新电脑或新目录运行时，Edge 使用独立数据目录 `edge_profile_9224`，需要先在自动打开的 Edge 里登录猎聘。登录状态会保存在该目录中。

## 打包

```powershell
.\build_exe.ps1
```

生成文件在 `dist\LiepinAutomation\`。

运行时配置、职位缓存、候选人记录和 AI 日志会保存在 exe 同目录，不会写入系统 Python 目录。
