# 本机监测模式

本项目现在推荐在本机运行监测，避免 GitHub Actions 云端无法连接学校网站导致误报。

## 行为

- Windows 登录后自动启动后台监测循环。
- 启动后立即执行一次，之后默认每 10 分钟执行一次。
- 电脑关机或退出登录后监测自然停止。
- 关机期间不会执行监测，也不会补跑或同步关机期间的信息。
- PushPlus token 保存在本机 `.env.local`，该文件不会上传到 GitHub。

## 常用命令

安装或更新本机计划任务：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\install-local-monitor.ps1 -PushPlusToken "你的 PushPlus token" -IntervalMinutes 10
```

手动执行一次：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\run-local-monitor.ps1
```

查看日志：

```powershell
Get-Content .\logs\monitor-loop.log -Tail 20
Get-Content .\logs\last-run.log -Tail 20
```

停止并移除计划任务：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\uninstall-local-monitor.ps1
```
