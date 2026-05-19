import html
import json
import os
import re
import subprocess
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List, Optional

HOST = "127.0.0.1"
PORT = 8787
TASK_NAME = "HBOU Interview Monitor"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
ENV_FILE = PROJECT_ROOT / ".env.local"
LOG_DIR = PROJECT_ROOT / "logs"
RUN_LOG = LOG_DIR / "last-run.log"
LOOP_LOG = LOG_DIR / "monitor-loop.log"
LOCK_PATH = Path(os.environ.get("TEMP", str(PROJECT_ROOT))) / "hbou-interview-monitor-loop.lock"
ALLOWED_ACTIONS = {"start", "stop", "restart", "run-once"}


def tail_lines(text: str, count: int) -> List[str]:
    lines = [line for line in text.splitlines() if line.strip()]
    return lines[-count:]


def read_tail(path: Path, count: int = 12) -> List[str]:
    if not path.exists():
        return []
    return tail_lines(path.read_text(encoding="utf-8", errors="replace"), count)


def parse_last_run_summary(log_text: str) -> Dict[str, object]:
    summary: Dict[str, object] = {"finished": "Local monitor run finished" in log_text}
    run_match = re.search(r"Run at (.+)", log_text)
    if run_match:
        summary["last_run_at"] = run_match.group(1).strip()

    counts_match = re.search(
        r"Fetched pages: (\d+); candidates: (\d+); name hits: (\d+)",
        log_text,
    )
    if counts_match:
        summary["fetched_pages"] = int(counts_match.group(1))
        summary["candidate_notices"] = int(counts_match.group(2))
        summary["name_hits"] = int(counts_match.group(3))

    push_match = re.search(
        r"New notices pushed: (\d+); detail hits pushed: (\d+); new name hits pushed: (\d+)",
        log_text,
    )
    if push_match:
        summary["new_notices_pushed"] = int(push_match.group(1))
        summary["detail_hits_pushed"] = int(push_match.group(2))
        summary["new_name_hits_pushed"] = int(push_match.group(3))
    return summary


def get_token_status() -> str:
    if not ENV_FILE.exists():
        return "not configured"
    for line in ENV_FILE.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        if line.strip().startswith("PUSHPLUS_TOKEN=") and line.split("=", 1)[1].strip():
            return "configured"
    return "missing"


def validate_action(action: str) -> Optional[str]:
    return action if action in ALLOWED_ACTIONS else None


def run_powershell(script: str, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def get_task_status() -> Dict[str, object]:
    script = rf"""
$task = Get-ScheduledTask -TaskName '{TASK_NAME}' -ErrorAction SilentlyContinue
$loopPid = $null
$loopRunning = $false
if (Test-Path -LiteralPath '{LOCK_PATH}') {{
  $loopPid = Get-Content -LiteralPath '{LOCK_PATH}' -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($loopPid -and (Get-Process -Id $loopPid -ErrorAction SilentlyContinue)) {{
    $loopRunning = $true
  }}
}}
if ($task) {{
  $info = Get-ScheduledTaskInfo -TaskName '{TASK_NAME}'
  [pscustomobject]@{{
    installed = $true
    task_state = [string]$task.State
    last_task_start = [string]$info.LastRunTime
    last_task_result = [string]$info.LastTaskResult
    loop_running = $loopRunning
    loop_pid = [string]$loopPid
  }} | ConvertTo-Json -Compress
}} else {{
  [pscustomobject]@{{
    installed = $false
    task_state = 'Not installed'
    last_task_start = ''
    last_task_result = ''
    loop_running = $loopRunning
    loop_pid = [string]$loopPid
  }} | ConvertTo-Json -Compress
}}
"""
    result = run_powershell(script, timeout=15)
    if result.returncode != 0:
        return {
            "installed": False,
            "task_state": "Unknown",
            "loop_running": False,
            "error": result.stderr.strip() or result.stdout.strip(),
        }
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {
            "installed": False,
            "task_state": "Unknown",
            "loop_running": False,
            "error": result.stdout.strip(),
        }


def get_status() -> Dict[str, object]:
    task = get_task_status()
    run_log_text = RUN_LOG.read_text(encoding="utf-8", errors="replace") if RUN_LOG.exists() else ""
    status = {
        **task,
        "project_root": str(PROJECT_ROOT),
        "pushplus_token": get_token_status(),
        "last_run": parse_last_run_summary(run_log_text),
        "run_log_tail": read_tail(RUN_LOG, 12),
        "loop_log_tail": read_tail(LOOP_LOG, 12),
    }
    return status


def execute_action(action: str) -> Dict[str, object]:
    valid_action = validate_action(action)
    if not valid_action:
        return {"ok": False, "error": "Unsupported action"}

    timeout = 240 if valid_action == "run-once" else 60
    script_path = SCRIPTS_DIR / "monitor-switch.ps1"
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script_path),
            "-Action",
            valid_action,
        ],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    return {
        "ok": result.returncode == 0,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "status": get_status(),
    }


def status_badge(status: Dict[str, object]) -> str:
    if status.get("loop_running"):
        return "运行中"
    if status.get("installed"):
        return "已停止"
    return "未安装"


def render_log(lines: List[str]) -> str:
    if not lines:
        return '<div class="empty">暂无日志</div>'
    return "\n".join(f"<div>{html.escape(line)}</div>" for line in lines)


def render_page(status: Dict[str, object]) -> str:
    badge = status_badge(status)
    last_run = status.get("last_run", {}) if isinstance(status.get("last_run"), dict) else {}
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>本机监测控制台</title>
  <style>
    :root {{ color-scheme: light; --red:#9b111e; --ink:#20242a; --muted:#657080; --line:#d9dee7; --bg:#f4f6f8; --ok:#087f5b; --warn:#b7791f; }}
    * {{ box-sizing: border-box; }}
    body {{ margin:0; font-family: "Microsoft YaHei", "Segoe UI", Arial, sans-serif; background:var(--bg); color:var(--ink); }}
    header {{ background:var(--red); color:white; padding:18px 24px; }}
    header h1 {{ margin:0; font-size:22px; font-weight:650; letter-spacing:0; }}
    main {{ max-width:1120px; margin:0 auto; padding:22px; }}
    .toolbar {{ display:flex; flex-wrap:wrap; gap:10px; margin:18px 0; }}
    button {{ min-height:38px; border:1px solid var(--line); background:white; color:var(--ink); padding:8px 14px; cursor:pointer; font-size:14px; }}
    button.primary {{ background:var(--red); color:white; border-color:var(--red); }}
    button:disabled {{ opacity:.55; cursor:wait; }}
    .grid {{ display:grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap:12px; }}
    .panel {{ background:white; border:1px solid var(--line); padding:14px; }}
    .panel h2 {{ margin:0 0 10px; font-size:15px; }}
    .value {{ font-size:20px; font-weight:650; overflow-wrap:anywhere; }}
    .muted {{ color:var(--muted); font-size:12px; line-height:1.6; }}
    .badge {{ display:inline-block; padding:4px 10px; border:1px solid var(--line); background:#fff; }}
    .badge.run {{ color:var(--ok); border-color:#9fd8c6; background:#edfdf7; }}
    .badge.stop {{ color:var(--warn); border-color:#efd59f; background:#fff8e6; }}
    .logs {{ display:grid; grid-template-columns:1fr 1fr; gap:12px; margin-top:12px; }}
    pre, .log {{ white-space:pre-wrap; word-break:break-word; font-family: Consolas, "Courier New", monospace; font-size:12px; line-height:1.55; background:#101419; color:#e8edf2; padding:12px; min-height:170px; margin:0; }}
    .empty {{ color:#9aa4b2; }}
    #message {{ min-height:22px; color:var(--muted); }}
    @media (max-width: 760px) {{ .grid, .logs {{ grid-template-columns:1fr; }} main {{ padding:14px; }} }}
  </style>
</head>
<body>
  <header><h1>本机监测控制台</h1></header>
  <main>
    <section class="grid">
      <div class="panel"><h2>监测状态</h2><div class="value"><span id="badge" class="badge {'run' if status.get('loop_running') else 'stop'}">{html.escape(badge)}</span></div><div class="muted">计划任务：{html.escape(str(status.get('task_state', 'Unknown')))}</div></div>
      <div class="panel"><h2>最近运行</h2><div class="value">{html.escape(str(last_run.get('last_run_at', '暂无')))}</div><div class="muted">完成：{html.escape(str(last_run.get('finished', False)))}</div></div>
      <div class="panel"><h2>抓取结果</h2><div class="value">{html.escape(str(last_run.get('fetched_pages', 0)))} 页</div><div class="muted">候选公告 {html.escape(str(last_run.get('candidate_notices', 0)))} 条，姓名命中 {html.escape(str(last_run.get('name_hits', 0)))} 条</div></div>
      <div class="panel"><h2>微信推送</h2><div class="value">{html.escape(str(status.get('pushplus_token', 'unknown')))}</div><div class="muted">新增公告 {html.escape(str(last_run.get('new_notices_pushed', 0)))}，重点命中 {html.escape(str(last_run.get('detail_hits_pushed', 0)))}</div></div>
    </section>

    <section class="toolbar">
      <button class="primary" data-action="start">启动监测</button>
      <button data-action="stop">停止监测</button>
      <button data-action="restart">重启监测</button>
      <button data-action="run-once">立即运行一次</button>
      <button id="refresh">刷新状态</button>
    </section>
    <div id="message"></div>

    <section class="logs">
      <div class="panel"><h2>最近一次运行日志</h2><div id="runLog" class="log">{render_log(status.get('run_log_tail', []))}</div></div>
      <div class="panel"><h2>后台循环日志</h2><div id="loopLog" class="log">{render_log(status.get('loop_log_tail', []))}</div></div>
    </section>
  </main>
  <script>
    async function loadStatus() {{
      const response = await fetch('/api/status');
      const status = await response.json();
      location.reload();
    }}
    async function postAction(action) {{
      const buttons = [...document.querySelectorAll('button')];
      buttons.forEach(button => button.disabled = true);
      document.getElementById('message').textContent = '正在执行：' + action;
      try {{
        const response = await fetch('/api/action', {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify({{ action }})
        }});
        const result = await response.json();
        document.getElementById('message').textContent = result.ok ? '执行完成' : ('执行失败：' + (result.error || result.stderr || 'unknown'));
        setTimeout(() => location.reload(), 800);
      }} catch (error) {{
        document.getElementById('message').textContent = '请求失败：' + error;
      }} finally {{
        buttons.forEach(button => button.disabled = false);
      }}
    }}
    document.querySelectorAll('[data-action]').forEach(button => {{
      button.addEventListener('click', () => postAction(button.dataset.action));
    }});
    document.getElementById('refresh').addEventListener('click', () => location.reload());
    setInterval(() => location.reload(), 30000);
  </script>
</body>
</html>"""


class ControlHandler(BaseHTTPRequestHandler):
    def _send(self, status_code: int, body: bytes, content_type: str) -> None:
        self.send_response(status_code)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/" or self.path.startswith("/?"):
            self._send(200, render_page(get_status()).encode("utf-8"), "text/html; charset=utf-8")
            return
        if self.path == "/api/status":
            self._send(200, json.dumps(get_status(), ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")
            return
        self._send(404, b"Not found", "text/plain; charset=utf-8")

    def do_POST(self) -> None:
        if self.path != "/api/action":
            self._send(404, b"Not found", "text/plain; charset=utf-8")
            return
        length = int(self.headers.get("Content-Length", "0") or "0")
        payload = self.rfile.read(length).decode("utf-8") if length else "{}"
        try:
            action = json.loads(payload).get("action", "")
            result = execute_action(action)
        except Exception as exc:
            result = {"ok": False, "error": str(exc)}
        self._send(200, json.dumps(result, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def log_message(self, format: str, *args: object) -> None:
        return


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), ControlHandler)
    url = f"http://{HOST}:{PORT}/"
    if "--no-open" not in sys.argv:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    print(f"Control page: {url}")
    server.serve_forever()


if __name__ == "__main__":
    main()
