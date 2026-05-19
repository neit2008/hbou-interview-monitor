import hashlib
import html
import json
import os
import re
import smtplib
import sys
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from email.mime.text import MIMEText
from html.parser import HTMLParser
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse, urldefrag

import requests
import yaml

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.yml"
STATE_PATH = ROOT / "state" / "state.json"
DOCS_DIR = ROOT / "docs"
DOCS_INDEX = DOCS_DIR / "index.html"
DOCS_STATUS = DOCS_DIR / "status.json"

CHINA_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")


@dataclass
class FetchedPage:
    url: str
    title: str
    text: str
    links: List[Tuple[str, str]]
    ok: bool
    error: Optional[str] = None


class PageHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text_chunks: List[str] = []
        self.links: List[Tuple[str, str]] = []
        self.title_candidates: Dict[str, List[str]] = {"h1": [], "h2": [], "class_title": [], "title": []}
        self._skip_depth = 0
        self._current_link_href: Optional[str] = None
        self._current_link_text: List[str] = []
        self._capture_title_kind: Optional[str] = None
        self._capture_title_end_tag: Optional[str] = None
        self._capture_title_text: List[str] = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        tag = tag.lower()
        attr_dict = {name.lower(): value or "" for name, value in attrs}
        if tag in {"script", "style", "noscript"}:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag == "a":
            self._current_link_href = attr_dict.get("href")
            self._current_link_text = []
        if self._capture_title_kind is None:
            class_names = set(attr_dict.get("class", "").split())
            if tag in {"h1", "h2", "title"}:
                self._capture_title_kind = tag
                self._capture_title_end_tag = tag
                self._capture_title_text = []
            elif "title" in class_names:
                self._capture_title_kind = "class_title"
                self._capture_title_end_tag = tag
                self._capture_title_text = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript"} and self._skip_depth:
            self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if tag == "a" and self._current_link_href is not None:
            label = clean_text(" ".join(self._current_link_text))
            self.links.append((label, self._current_link_href))
            self._current_link_href = None
            self._current_link_text = []
        if self._capture_title_kind and tag == self._capture_title_end_tag:
            value = clean_text(" ".join(self._capture_title_text))
            if value:
                self.title_candidates[self._capture_title_kind].append(value[:160])
            self._capture_title_kind = None
            self._capture_title_end_tag = None
            self._capture_title_text = []

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if data:
            self.text_chunks.append(data)
            if self._current_link_href is not None:
                self._current_link_text.append(data)
            if self._capture_title_kind is not None:
                self._capture_title_text.append(data)


def now_cn() -> str:
    return datetime.now(CHINA_TZ).strftime("%Y-%m-%d %H:%M:%S %Z")


def load_config() -> dict:
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {
            "created_at": now_cn(),
            "last_run_at": None,
            "seen_notice_ids": [],
            "seen_name_hit_ids": [],
            "page_availability": {},
            "recent_events": [],
        }
    with STATE_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with STATE_PATH.open("w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def normalize_recent_events(state: dict) -> None:
    replacements = {
        "监测页面无法打开": "监测任务无法连接页面",
        "监测页面已恢复打开": "监测任务已恢复连接页面",
    }
    for event in state.get("recent_events", []):
        if event.get("kind") in replacements:
            event["kind"] = replacements[event["kind"]]
        if event.get("title") in replacements:
            event["title"] = replacements[event["title"]]


def normalize_url(url: str) -> str:
    url, _frag = urldefrag(url)
    return url.strip()


def same_allowed_domain(url: str, allowed_domains: Iterable[str]) -> bool:
    host = urlparse(url).netloc.lower()
    return any(host == d.lower() for d in allowed_domains)


def clean_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text or " ").strip()
    return text


def make_id(*parts: str) -> str:
    raw = "||".join(parts)
    return hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()[:24]


def extract_title(parser: PageHTMLParser, fallback_url: str) -> str:
    for kind in ["h1", "h2", "class_title", "title"]:
        values = parser.title_candidates.get(kind, [])
        if values:
            return values[0][:160]
    return fallback_url


def fetch_page(url: str, timeout: int, user_agent: str, retries: int = 1, retry_delay: float = 0.0) -> FetchedPage:
    headers = {
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
        "Cache-Control": "no-cache",
    }
    attempts = max(1, int(retries))
    last_error = ""
    for attempt in range(1, attempts + 1):
        try:
            r = requests.get(url, timeout=timeout, headers=headers, allow_redirects=True)
            r.raise_for_status()
            if not r.encoding or r.encoding.lower() in {"iso-8859-1", "ascii"}:
                r.encoding = r.apparent_encoding
            content_type = r.headers.get("content-type", "")
            if "text/html" not in content_type and "application/xhtml" not in content_type and "" != content_type:
                return FetchedPage(url=url, title=url, text="", links=[], ok=False, error=f"非HTML页面：{content_type}")
            parser = PageHTMLParser()
            parser.feed(r.text)
            title = extract_title(parser, url)
            text = clean_text(" ".join(parser.text_chunks))
            links: List[Tuple[str, str]] = []
            for label, href in parser.links:
                if not href:
                    continue
                href = href.strip()
                if href.startswith(("javascript:", "mailto:", "tel:")):
                    continue
                link_url = normalize_url(urljoin(r.url, href))
                label = clean_text(label) or link_url
                links.append((label[:160], link_url))
            return FetchedPage(url=normalize_url(r.url), title=title, text=text, links=links, ok=True)
        except Exception as e:
            last_error = repr(e)
            if attempt < attempts and retry_delay > 0:
                time.sleep(retry_delay)
    return FetchedPage(url=url, title=url, text="", links=[], ok=False, error=f"{last_error}（已重试 {attempts} 次）")


def contains_any(text: str, keywords: Iterable[str]) -> bool:
    return any(k and k in text for k in keywords)


def excerpt(text: str, keyword: str, width: int = 80) -> str:
    idx = text.find(keyword)
    if idx < 0:
        return ""
    start = max(0, idx - width)
    end = min(len(text), idx + len(keyword) + width)
    return text[start:end]


def crawl(config: dict) -> Tuple[Dict[str, FetchedPage], List[dict], List[dict]]:
    mcfg = config["monitor"]
    start_urls = [normalize_url(u) for u in mcfg["start_urls"]]
    allowed_domains = mcfg["allowed_domains"]
    notice_keywords = mcfg["notice_keywords"]
    name_keywords = mcfg["name_keywords"]
    max_depth = int(mcfg.get("max_depth", 2))
    max_pages = int(mcfg.get("max_pages", 80))
    timeout = int(mcfg.get("request_timeout_seconds", 20))
    retries = int(mcfg.get("request_retries", 1))
    retry_delay = float(mcfg.get("request_retry_delay_seconds", 0.0))
    user_agent = mcfg.get("user_agent", "Mozilla/5.0 notice-monitor")
    polite_delay = float(mcfg.get("polite_delay_seconds", 0.3))

    pages: Dict[str, FetchedPage] = {}
    candidates: Dict[str, dict] = {}
    q = deque((u, 0, "入口页") for u in start_urls)
    queued: Set[str] = set(start_urls)

    while q and len(pages) < max_pages:
        url, depth, source_label = q.popleft()
        page = fetch_page(url, timeout=timeout, user_agent=user_agent, retries=retries, retry_delay=retry_delay)
        pages[url] = page
        time.sleep(polite_delay)

        combined_self = f"{page.title} {page.text} {url}"
        if page.ok and contains_any(combined_self, notice_keywords):
            candidates[url] = {
                "url": url,
                "title": page.title,
                "source": source_label,
                "reason": "页面正文/标题包含监测关键词",
            }

        if not page.ok or depth >= max_depth:
            continue

        for label, link_url in page.links:
            link_url = normalize_url(link_url)
            if not same_allowed_domain(link_url, allowed_domains):
                continue
            # 跳过明显的静态资源。
            if re.search(r"\.(jpg|jpeg|png|gif|css|js|ico|zip|rar)$", link_url, re.I):
                continue

            link_text = f"{label} {link_url}"
            if contains_any(link_text, notice_keywords):
                candidates[link_url] = {
                    "url": link_url,
                    "title": label or link_url,
                    "source": page.title,
                    "reason": "链接标题/地址包含监测关键词",
                }

            # “二级菜单”检索：继续打开站内链接，最多到 max_depth。
            if link_url not in queued and len(queued) < max_pages * 3:
                queued.add(link_url)
                q.append((link_url, depth + 1, page.title))

    # 补抓候选公告页面，保证能查正文里的姓名。
    for url in list(candidates.keys()):
        if url not in pages and len(pages) < max_pages:
            page = fetch_page(url, timeout=timeout, user_agent=user_agent, retries=retries, retry_delay=retry_delay)
            pages[url] = page
            time.sleep(polite_delay)

    notices: List[dict] = []
    name_hits: List[dict] = []
    for url, cand in candidates.items():
        page = pages.get(url)
        title = cand.get("title") or (page.title if page else url)
        body = page.text if page and page.ok else ""
        page_title = page.title if page and page.title else title
        combined = f"{title} {page_title} {body} {url}"
        if not contains_any(combined, notice_keywords):
            continue
        nid = make_id(url, title)
        notices.append({
            "id": nid,
            "title": page_title or title,
            "url": url,
            "source": cand.get("source", ""),
            "reason": cand.get("reason", ""),
            "matched_keywords": [k for k in notice_keywords if k in combined],
        })
        for name in name_keywords:
            if name and name in combined:
                hid = make_id(url, name)
                name_hits.append({
                    "id": hid,
                    "name": name,
                    "title": page_title or title,
                    "url": url,
                    "excerpt": excerpt(combined, name, width=90),
                    "source": cand.get("source", ""),
                })
    # 去重并按标题排序，保证输出稳定。
    notices = list({n["id"]: n for n in notices}.values())
    name_hits = list({h["id"]: h for h in name_hits}.values())
    notices.sort(key=lambda x: x["title"], reverse=True)
    name_hits.sort(key=lambda x: x["title"], reverse=True)
    return pages, notices, name_hits


def build_markdown(title: str, items: List[dict], kind: str) -> str:
    lines = [f"# {title}", "", f"检查时间：{now_cn()}", ""]
    for item in items[:10]:
        lines.append(f"## {item.get('title', '未命名')}")
        lines.append(f"- 链接：{item.get('url')}")
        if kind == "detail":
            lines.append(f"- 命中关键词：{'、'.join(item.get('matched_keywords', []))}")
            lines.append("")
            lines.append(item.get("content", ""))
        elif kind == "name":
            lines.append(f"- 命中姓名：{item.get('name')}")
            lines.append(f"- 摘要：{item.get('excerpt', '')}")
        else:
            lines.append(f"- 关键词：{'、'.join(item.get('matched_keywords', []))}")
        lines.append("")
    return "\n".join(lines)


def limit_text(text: str, max_chars: int = 3500) -> str:
    text = clean_text(text)
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "\n\n（内容较长，已截断；请打开原文链接查看全文。）"


def build_notice_detail_hits(new_notices: List[dict], pages: Dict[str, FetchedPage], keywords: Iterable[str]) -> List[dict]:
    hits: List[dict] = []
    keyword_list = [k for k in keywords if k]
    for notice in new_notices:
        url = notice.get("url", "")
        page = pages.get(url)
        if not page or not page.ok:
            continue
        combined = f"{notice.get('title', '')} {page.title} {page.text}"
        matched = [k for k in keyword_list if k in combined]
        if not matched:
            continue
        hits.append({
            "id": f"{notice.get('id', make_id(url))}:{'|'.join(matched)}",
            "title": notice.get("title") or page.title or url,
            "url": url,
            "matched_keywords": matched,
            "content": limit_text(page.text),
        })
    return hits


def detect_availability_changes(state: dict, pages: Dict[str, FetchedPage], start_urls: Iterable[str]) -> List[dict]:
    availability = state.setdefault("page_availability", {})
    events: List[dict] = []
    for url in start_urls:
        page = pages.get(url)
        ok = bool(page and page.ok)
        previous = availability.get(url)
        if ok:
            if previous == "down":
                events.append({"kind": "recovered", "title": "监测任务已恢复连接页面", "url": url})
            availability[url] = "ok"
            continue

        error = page.error if page else "页面未抓取"
        if previous != "down":
            events.append({"kind": "down", "title": "监测任务无法连接页面", "url": url, "error": error})
        availability[url] = "down"
    return events


def build_availability_markdown(event: dict) -> str:
    lines = [
        f"# {event['title']}",
        "",
        f"检查时间：{now_cn()}",
        f"- 链接：{event.get('url')}",
    ]
    if event.get("kind") == "down":
        lines.append(f"- 错误：{event.get('error', '')}")
        lines.append("")
        lines.append("这表示 GitHub Actions 本次无法连接学校站点，不一定代表你本机浏览器也打不开。后续定时监测会继续检查；连接恢复时会再推送一次。")
    else:
        lines.append("")
        lines.append("GitHub Actions 已从无法连接状态恢复，可以继续抓取页面。")
    return "\n".join(lines)


def send_pushplus(title: str, content: str) -> None:
    token = os.getenv("PUSHPLUS_TOKEN")
    if not token:
        print("PUSHPLUS_TOKEN not set; skip PushPlus.")
        return
    try:
        resp = requests.post(
            "https://www.pushplus.plus/send",
            json={"token": token, "title": title, "content": content, "template": "markdown"},
            timeout=20,
        )
        print("PushPlus response:", resp.status_code, resp.text[:200])
    except Exception as e:
        print("PushPlus failed:", repr(e), file=sys.stderr)


def send_email(title: str, content: str) -> None:
    host = os.getenv("SMTP_HOST")
    port = int(os.getenv("SMTP_PORT") or "465")
    user = os.getenv("SMTP_USER")
    password = os.getenv("SMTP_PASS")
    to_addr = os.getenv("MAIL_TO")
    if not all([host, user, password, to_addr]):
        print("SMTP secrets not complete; skip email.")
        return
    msg = MIMEText(content, "plain", "utf-8")
    msg["Subject"] = title
    msg["From"] = user
    msg["To"] = to_addr
    try:
        with smtplib.SMTP_SSL(host, port, timeout=20) as s:
            s.login(user, password)
            s.sendmail(user, [to_addr], msg.as_string())
        print("Email sent.")
    except Exception as e:
        print("Email failed:", repr(e), file=sys.stderr)


def notify(config: dict, title: str, content: str) -> None:
    pcfg = config.get("push", {})
    if pcfg.get("pushplus_enabled", True):
        send_pushplus(title, content)
    if pcfg.get("email_enabled", False):
        send_email(title, content)


def event_record(kind: str, title: str, url: str, extra: Optional[dict] = None) -> dict:
    data = {"time": now_cn(), "kind": kind, "title": title, "url": url}
    if extra:
        data.update(extra)
    return data


def write_status_page(state: dict, pages: Dict[str, FetchedPage], notices: List[dict], name_hits: List[dict], new_notices: List[dict], new_name_hits: List[dict]) -> None:
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    failed_pages = [p for p in pages.values() if not p.ok]
    status = {
        "last_run_at": state.get("last_run_at"),
        "fetched_pages": len(pages),
        "candidate_notices": len(notices),
        "name_hits_total": len(name_hits),
        "new_notices": new_notices,
        "new_name_hits": new_name_hits,
        "failed_pages": [{"url": p.url, "error": p.error} for p in failed_pages[:20]],
        "recent_events": state.get("recent_events", [])[:30],
    }
    DOCS_STATUS.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")

    def rows(items: List[dict], empty: str) -> str:
        if not items:
            return f"<tr><td colspan='4'>{html.escape(empty)}</td></tr>"
        out = []
        for it in items[:30]:
            out.append(
                "<tr>"
                f"<td>{html.escape(it.get('time', ''))}</td>"
                f"<td>{html.escape(it.get('kind', ''))}</td>"
                f"<td><a href='{html.escape(it.get('url', ''))}' target='_blank'>{html.escape(it.get('title', ''))}</a></td>"
                f"<td>{html.escape(it.get('name', '') or '—')}</td>"
                "</tr>"
            )
        return "\n".join(out)

    recent_events = state.get("recent_events", [])[:30]
    failed_html = "<br>".join(html.escape(f"{p.url}：{p.error}") for p in failed_pages[:10]) or "无"
    html_text = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>湖北开放大学人事处面试通知监测</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; margin: 24px; color: #1f2937; }}
    .card {{ border: 1px solid #e5e7eb; border-radius: 14px; padding: 18px; margin: 14px 0; box-shadow: 0 2px 8px rgba(0,0,0,.04); }}
    h1 {{ font-size: 24px; }}
    h2 {{ font-size: 18px; margin-top: 0; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ border-bottom: 1px solid #e5e7eb; padding: 9px; text-align: left; vertical-align: top; }}
    th {{ background: #f9fafb; }}
    .ok {{ color: #047857; font-weight: 600; }}
    .warn {{ color: #b45309; font-weight: 600; }}
    .muted {{ color: #6b7280; }}
  </style>
</head>
<body>
  <h1>湖北开放大学人事处面试通知监测</h1>
  <p class="muted">监测入口：<a href="https://rsc.hbou.edu.cn/index.htm" target="_blank">https://rsc.hbou.edu.cn/index.htm</a></p>

  <div class="card">
    <h2>运行状态</h2>
    <p>最近检查时间：<strong>{html.escape(str(state.get('last_run_at')))}</strong></p>
    <p>本轮抓取页面：<strong>{len(pages)}</strong> 个；候选通知：<strong>{len(notices)}</strong> 条；姓名命中总数：<strong>{len(name_hits)}</strong> 条。</p>
    <p>本轮新增面试/招聘相关通知：<span class="{'warn' if new_notices else 'ok'}">{len(new_notices)}</span>；本轮新增姓名命中：<span class="{'warn' if new_name_hits else 'ok'}">{len(new_name_hits)}</span>。</p>
  </div>

  <div class="card">
    <h2>最近事件</h2>
    <table>
      <thead><tr><th>时间</th><th>类型</th><th>标题</th><th>姓名</th></tr></thead>
      <tbody>{rows(recent_events, '暂无事件')}</tbody>
    </table>
  </div>

  <div class="card">
    <h2>抓取异常</h2>
    <p>{failed_html}</p>
  </div>
</body>
</html>"""
    DOCS_INDEX.write_text(html_text, encoding="utf-8")


def main() -> None:
    config = load_config()
    state = load_state()
    normalize_recent_events(state)
    first_run = not bool(state.get("last_run_at"))
    seen_notice_ids: Set[str] = set(state.get("seen_notice_ids", []))
    seen_name_hit_ids: Set[str] = set(state.get("seen_name_hit_ids", []))

    pages, notices, name_hits = crawl(config)

    start_urls = [normalize_url(u) for u in config["monitor"]["start_urls"]]
    availability_events = detect_availability_changes(state, pages, start_urls)
    notify_first = bool(config["monitor"].get("notify_new_notices_on_first_run", False))
    new_notices = [n for n in notices if n["id"] not in seen_notice_ids]
    new_name_hits = [h for h in name_hits if h["id"] not in seen_name_hit_ids]

    # 第一次运行只建立历史基线，不把历史通知都当新增推送；但姓名命中仍会推送。
    push_notices = new_notices if (notify_first or not first_run) else []
    push_name_hits = new_name_hits
    content_keywords = config["monitor"].get("content_keywords") or config["monitor"].get("name_keywords", [])
    push_notice_detail_hits = build_notice_detail_hits(push_notices, pages, content_keywords)

    for event in availability_events:
        notify(config, f"【网页监测】{event['title']}", build_availability_markdown(event))
    if push_notices:
        content = build_markdown("发现新的面试/招聘相关通知", push_notices, kind="notice")
        notify(config, "【网页监测】发现新的面试/招聘相关通知", content)
    if push_notice_detail_hits:
        content = build_markdown("新增公告命中重点关键词", push_notice_detail_hits, kind="detail")
        notify(config, "【重要】新增公告命中重点关键词", content)
    if push_name_hits:
        content = build_markdown("面试/招聘相关页面命中姓名", push_name_hits, kind="name")
        notify(config, "【重要】页面中检索到目标姓名", content)

    for event in availability_events:
        state.setdefault("recent_events", []).insert(0, event_record(event["title"], event["title"], event["url"], {"error": event.get("error", "")}))
    for n in push_notices:
        state.setdefault("recent_events", []).insert(0, event_record("新增通知", n["title"], n["url"]))
    for h in push_notice_detail_hits:
        state.setdefault("recent_events", []).insert(0, event_record("重点关键词命中", h["title"], h["url"], {"name": "、".join(h.get("matched_keywords", []))}))
    for h in push_name_hits:
        state.setdefault("recent_events", []).insert(0, event_record("姓名命中", h["title"], h["url"], {"name": h["name"], "excerpt": h.get("excerpt", "")}))

    state["last_run_at"] = now_cn()
    state["seen_notice_ids"] = sorted(set(state.get("seen_notice_ids", [])) | {n["id"] for n in notices})
    state["seen_name_hit_ids"] = sorted(set(state.get("seen_name_hit_ids", [])) | {h["id"] for h in name_hits})
    state["recent_events"] = state.get("recent_events", [])[:100]

    save_state(state)
    write_status_page(state, pages, notices, name_hits, push_notices, push_name_hits)

    print(f"Run at {state['last_run_at']}")
    print(f"Fetched pages: {len(pages)}; candidates: {len(notices)}; name hits: {len(name_hits)}")
    print(f"New notices pushed: {len(push_notices)}; detail hits pushed: {len(push_notice_detail_hits)}; new name hits pushed: {len(push_name_hits)}")


if __name__ == "__main__":
    main()
