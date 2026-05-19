from scripts import control_server


def test_tail_lines_returns_recent_lines():
    text = "\n".join(["one", "two", "three", "four"])

    assert control_server.tail_lines(text, 2) == ["three", "four"]


def test_parse_last_run_summary_reads_monitor_counts():
    log_text = "\n".join(
        [
            "[2026-05-19 18:09:49] Starting local monitor run...",
            "Run at 2026-05-19 18:10:24 Asia/Shanghai",
            "Fetched pages: 52; candidates: 16; name hits: 0",
            "New notices pushed: 0; detail hits pushed: 0; new name hits pushed: 0",
            "[2026-05-19 18:10:24] Local monitor run finished.",
        ]
    )

    summary = control_server.parse_last_run_summary(log_text)

    assert summary == {
        "last_run_at": "2026-05-19 18:10:24 Asia/Shanghai",
        "fetched_pages": 52,
        "candidate_notices": 16,
        "name_hits": 0,
        "new_notices_pushed": 0,
        "detail_hits_pushed": 0,
        "new_name_hits_pushed": 0,
        "finished": True,
    }


def test_validate_action_rejects_unknown_commands():
    assert control_server.validate_action("start") == "start"
    assert control_server.validate_action("run-once") == "run-once"
    assert control_server.validate_action("delete-everything") is None


def test_render_page_contains_status_and_controls():
    status = {
        "task_state": "Running",
        "loop_running": True,
        "pushplus_token": "configured",
        "last_run": {
            "last_run_at": "2026-05-19 18:10:24 Asia/Shanghai",
            "fetched_pages": 52,
            "candidate_notices": 16,
            "name_hits": 0,
        },
        "run_log_tail": ["Run at 2026-05-19 18:10:24 Asia/Shanghai"],
        "loop_log_tail": ["Run completed."],
    }

    html = control_server.render_page(status)

    assert "本机监测控制台" in html
    assert "启动监测" in html
    assert "停止监测" in html
    assert "立即运行一次" in html
    assert "2026-05-19 18:10:24 Asia/Shanghai" in html
