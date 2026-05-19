import sys
import types

bs4_stub = types.ModuleType("bs4")
bs4_stub.BeautifulSoup = object
sys.modules.setdefault("bs4", bs4_stub)

import monitor


def test_build_notice_detail_hits_includes_matching_notice_body():
    notice = {
        "id": "notice-1",
        "title": "2026年公开招聘面试公告",
        "url": "https://example.test/notice.html",
    }
    pages = {
        notice["url"]: monitor.FetchedPage(
            url=notice["url"],
            title=notice["title"],
            text="湖北开放大学人工智能D岗进入面试，刘国栋请按要求参加资格复审。",
            links=[],
            ok=True,
        )
    }

    hits = monitor.build_notice_detail_hits([notice], pages, ["人工智能D岗", "刘国栋"])

    assert hits == [
        {
            "id": "notice-1:人工智能D岗|刘国栋",
            "title": "2026年公开招聘面试公告",
            "url": "https://example.test/notice.html",
            "matched_keywords": ["人工智能D岗", "刘国栋"],
            "content": "湖北开放大学人工智能D岗进入面试，刘国栋请按要求参加资格复审。",
        }
    ]


def test_detect_availability_changes_pushes_first_down_and_recovery_once():
    url = "https://example.test/index.htm"
    state = {"page_availability": {}}
    down_pages = {
        url: monitor.FetchedPage(url=url, title=url, text="", links=[], ok=False, error="timeout")
    }

    first_down = monitor.detect_availability_changes(state, down_pages, [url])
    repeat_down = monitor.detect_availability_changes(state, down_pages, [url])
    recovered = monitor.detect_availability_changes(
        state,
        {url: monitor.FetchedPage(url=url, title="首页", text="ok", links=[], ok=True)},
        [url],
    )
    repeat_ok = monitor.detect_availability_changes(
        state,
        {url: monitor.FetchedPage(url=url, title="首页", text="ok", links=[], ok=True)},
        [url],
    )

    assert first_down == [
        {"kind": "down", "title": "监测页面无法打开", "url": url, "error": "timeout"}
    ]
    assert repeat_down == []
    assert recovered == [{"kind": "recovered", "title": "监测页面已恢复打开", "url": url}]
    assert repeat_ok == []
