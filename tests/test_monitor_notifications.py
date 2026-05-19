import sys
import types

bs4_stub = types.ModuleType("bs4")
bs4_stub.BeautifulSoup = object
sys.modules.setdefault("bs4", bs4_stub)

import monitor


class FakeTag:
    def __init__(self, text="", href=None):
        self._text = text
        self._href = href

    def get_text(self, _separator=" "):
        return self._text

    def get(self, name):
        if name == "href":
            return self._href
        return None


class FakeSoup:
    def __init__(self, text, *_args):
        self.text = text

    def __call__(self, _selectors):
        return []

    def select_one(self, selector):
        if selector == "title":
            return FakeTag("首页可访问")
        return None

    def get_text(self, _separator=" "):
        return "首页可访问 通知公告"

    def find_all(self, tag_name):
        if tag_name == "a":
            return [FakeTag("通知公告", "tzgg.htm")]
        return []


class FakeResponse:
    url = "https://example.test/index.htm"
    text = "<html><title>首页可访问</title><a href='tzgg.htm'>通知公告</a></html>"
    encoding = "utf-8"
    apparent_encoding = "utf-8"
    headers = {"content-type": "text/html"}

    def raise_for_status(self):
        return None


def test_fetch_page_retries_transient_connect_timeout(monkeypatch):
    attempts = {"count": 0}

    def fake_get(*_args, **_kwargs):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise monitor.requests.exceptions.ConnectTimeout("connect timeout")
        return FakeResponse()

    monkeypatch.setattr(monitor.requests, "get", fake_get)
    monkeypatch.setattr(monitor, "BeautifulSoup", FakeSoup)

    page = monitor.fetch_page(
        "https://example.test/index.htm",
        timeout=20,
        user_agent="test",
        retries=2,
        retry_delay=0,
    )

    assert page.ok is True
    assert attempts["count"] == 2
    assert page.title == "首页可访问"


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
        {"kind": "down", "title": "监测任务无法连接页面", "url": url, "error": "timeout"}
    ]
    assert repeat_down == []
    assert recovered == [{"kind": "recovered", "title": "监测任务已恢复连接页面", "url": url}]
    assert repeat_ok == []


def test_normalize_recent_events_renames_old_availability_wording():
    state = {
        "recent_events": [
            {"kind": "监测页面无法打开", "title": "监测页面无法打开", "url": "https://example.test"},
            {"kind": "监测页面已恢复打开", "title": "监测页面已恢复打开", "url": "https://example.test"},
        ]
    }

    monitor.normalize_recent_events(state)

    assert state["recent_events"][0]["kind"] == "监测任务无法连接页面"
    assert state["recent_events"][0]["title"] == "监测任务无法连接页面"
    assert state["recent_events"][1]["kind"] == "监测任务已恢复连接页面"
    assert state["recent_events"][1]["title"] == "监测任务已恢复连接页面"
