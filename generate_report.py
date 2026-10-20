from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


BASE_URL = "https://store.steampowered.com"
SEARCH_URL = f"{BASE_URL}/search/results/"
STORE_BROWSE_URL = "https://api.steampowered.com/IStoreBrowseService/GetItems/v1/"
STORE_ASSET_CDN = "https://shared.akamai.steamstatic.com/store_item_assets/"
try:
    TIMEZONE = ZoneInfo("Asia/Hong_Kong")
except ZoneInfoNotFoundError:
    TIMEZONE = dt.timezone(dt.timedelta(hours=8), name="Asia/Hong_Kong")
DEFAULT_CC = "CN"
DEFAULT_LANG = "schinese"
DEFAULT_TARGET_COUNT = 300
DEFAULT_MAX_PAGES = 12
DEFAULT_POTENTIAL_COUNT = 100
DEFAULT_POTENTIAL_DAYS = 365
DEFAULT_POTENTIAL_MIN_REVIEWS = 50
DEFAULT_POTENTIAL_MIN_REVIEWS_DEMO = 10
DEFAULT_POTENTIAL_MAX_PAGES = 160
DEFAULT_POTENTIAL_DEMO_EMPTY_PAGE_LIMIT = 12
DEFAULT_STORE_BROWSE_BATCH_SIZE = 50
PAGE_SIZE = 25
REQUEST_DELAY_SECONDS = 1.2
RATE_LIMIT_COOLDOWN_SECONDS = 45
DETAIL_CACHE_NAME = "detail_cache.json"
AI_CACHE_MAX_AGE_DAYS = 7
HTTP_RETRIES = 3
# Steam publishes the AI Generated Content Disclosure on the store page only.
# StoreBrowse and /api/appdetails do not carry it (verified against live responses).
AI_DISCLOSURE_HEADINGS = frozenset({
    "ai generated content disclosure",
    "ai 生成内容披露",
    "ai 生成內容披露",
})
AI_DISCLOSURE_BOILERPLATE = frozenset({
    "the developers describe how their game uses ai generated content like this",
    "开发者对其游戏如何使用 ai 生成内容的描述如下",
    "開發者對其遊戲如何使用 ai 生成內容的描述如下",
})
STORE_ASSET_PRIORITY = (
    "main_capsule_2x",
    "main_capsule",
    "header_2x",
    "header",
)
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)
REQUEST_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json;q=0.8,*/*;q=0.7",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
    "Cache-Control": "no-cache",
    "Cookie": "timezoneOffset=28800,0",
    "Pragma": "no-cache",
    "Referer": "https://store.steampowered.com/search/",
}


TAG_NAMES: dict[str, str] = {
    "9": "策略",
    "19": "动作",
    "21": "冒险",
    "84": "设计与插画",
    "113": "免费开玩",
    "122": "角色扮演",
    "492": "独立",
    "493": "抢先体验",
    "597": "休闲",
    "599": "模拟",
    "872": "动画制作与建模",
    "1036": "教育",
    "1625": "平台游戏",
    "1643": "建造",
    "1645": "塔防",
    "1646": "回合制",
    "1654": "放松",
    "1664": "解谜",
    "1667": "恐怖",
    "1684": "奇幻",
    "1698": "指向点击",
    "1716": "类 Rogue",
    "1741": "回合制战斗",
    "1742": "剧情丰富",
    "1754": "大型多人在线",
    "1773": "街机",
    "1774": "射击",
    "3799": "视觉小说",
    "3810": "沙盒",
    "3834": "探索",
    "3839": "第一人称",
    "3859": "多人",
    "3871": "2D",
    "3877": "精确平台",
    "3916": "老式",
    "3959": "类魂",
    "3964": "像素图形",
    "3968": "物理",
    "3993": "战斗",
    "4004": "战争",
    "4026": "困难",
    "4057": "魔法",
    "4085": "动漫",
    "4106": "动作冒险",
    "4136": "欢乐",
    "4166": "氛围",
    "4175": "真实",
    "4182": "单人",
    "4191": "3D",
    "4231": "动作角色扮演",
    "4236": "刷宝",
    "4255": "清版动作",
    "42804": "动作类 Rogue",
    "4305": "彩色",
    "4325": "回合制策略",
    "4637": "俯视",
    "4726": "可爱",
    "4758": "双摇杆射击",
    "4791": "俯视射击",
    "4885": "弹幕射击",
    "4975": "2.5D",
    "5154": "分数挑战",
    "5300": "游戏开发",
    "5350": "家庭友好",
    "5379": "2D 平台",
    "5390": "时间竞速",
    "5537": "益智平台",
    "6129": "逻辑",
    "6915": "武术",
    "7481": "控制器",
    "8666": "奔跑",
    "9204": "沉浸式模拟",
    "9551": "动态叙事",
    "10235": "生活模拟",
    "10437": "益智问答",
    "10808": "超现实",
    "11014": "互动小说",
    "11095": "Boss Rush",
    "14139": "回合制战术",
    "16689": "时间管理",
    "17305": "策略角色扮演",
    "24003": "文字游戏",
}


class SearchResultParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: list[dict[str, Any]] = []
        self._item: dict[str, Any] | None = None
        self._stack: list[dict[str, Any]] = []
        self._capture: str | None = None
        self._capture_chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs_list: list[tuple[str, str | None]]) -> None:
        attrs = {key: value or "" for key, value in attrs_list}
        classes = set(attrs.get("class", "").split())

        appid = attrs.get("data-ds-appid", "").strip()
        # Bundle rows carry a comma-separated list of member appids (and a
        # data-ds-bundleid); the member games appear as standalone rows, so
        # skip the bundle row itself.
        is_bundle = "," in appid or bool(attrs.get("data-ds-bundleid"))
        if tag == "a" and "search_result_row" in classes and appid and not is_bundle:
            self._item = {
                "appid": appid,
                "steam_url": attrs.get("href", "").split("?")[0],
                "tag_ids": self._parse_tag_ids(attrs.get("data-ds-tagids", "")),
                "platforms": [],
                "image": "",
                "title": "",
                "release_date_text": "",
                "price_text": "",
                "discount_text": "",
                "review_summary": "",
                "review_count": None,
                "review_positive_percent": None,
                "review_label": "",
                "price_final": None,
            }

        if self._item is None:
            return

        self._stack.append({"tag": tag, "classes": classes})

        if tag == "img" and self._is_inside("search_capsule"):
            self._item["image"] = attrs.get("src", "").strip()

        if tag == "span" and "platform_img" in classes:
            platform = self._platform_name(classes)
            if platform and platform not in self._item["platforms"]:
                self._item["platforms"].append(platform)

        if tag == "span" and "search_review_summary" in classes:
            tooltip = attrs.get("data-tooltip-html", "")
            self._item["review_summary"] = clean_text(tooltip.replace("<br>", " "))
            self._item.update(parse_review_summary(self._item["review_summary"]))

        if tag == "div" and "search_price_discount_combined" in classes:
            raw_price = attrs.get("data-price-final")
            if raw_price and raw_price.isdigit():
                self._item["price_final"] = int(raw_price)

        capture = None
        if tag == "span" and "title" in classes:
            capture = "title"
        elif tag == "div" and "search_released" in classes:
            capture = "release_date_text"
        elif tag == "div" and "discount_final_price" in classes:
            capture = "price_text"
        elif tag == "div" and "discount_pct" in classes:
            capture = "discount_text"
        elif tag == "div" and "discount_original_price" in classes:
            capture = "original_price_text"

        if capture:
            self._capture = capture
            self._capture_chunks = []

    def handle_data(self, data: str) -> None:
        if self._capture is not None:
            self._capture_chunks.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._item is None:
            return

        if self._capture is not None and self._stack and self._stack[-1]["tag"] == tag:
            value = clean_text("".join(self._capture_chunks))
            if value:
                if self._capture == "original_price_text":
                    self._item["original_price_text"] = value
                else:
                    self._item[self._capture] = value
            self._capture = None
            self._capture_chunks = []

        if tag == "a":
            self._finalize_item()
            return

        if self._stack:
            self._stack.pop()

    @staticmethod
    def _parse_tag_ids(raw: str) -> list[str]:
        return re.findall(r"\d+", raw)

    def _is_inside(self, class_name: str) -> bool:
        return any(class_name in frame["classes"] for frame in self._stack)

    @staticmethod
    def _platform_name(classes: set[str]) -> str | None:
        if "win" in classes:
            return "Windows"
        if "mac" in classes:
            return "macOS"
        if "linux" in classes:
            return "Linux"
        return None

    def _finalize_item(self) -> None:
        if not self._item:
            return
        if self._item.get("appid") and self._item.get("title"):
            if not self._item.get("price_text"):
                self._item["price_text"] = "价格未显示"
            self.items.append(self._item)
        self._item = None
        self._stack = []
        self._capture = None
        self._capture_chunks = []


class StoreMetaParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.description = ""

    def handle_starttag(self, tag: str, attrs_list: list[tuple[str, str | None]]) -> None:
        if tag != "meta" or self.description:
            return
        attrs = {key.lower(): value or "" for key, value in attrs_list}
        key = (attrs.get("name") or attrs.get("property") or "").lower()
        if key in {"description", "og:description", "twitter:description"}:
            self.description = clean_text(attrs.get("content", ""))


class AiDisclosureUnavailable(Exception):
    """The store page was not readable, so AI involvement must stay unknown."""


def normalize_heading(value: str) -> str:
    return clean_text(value).strip().rstrip(".:：。").lower()


class AiDisclosureParser(HTMLParser):
    """Read Steam's store-page AI Generated Content Disclosure block.

    The block is a repeated `#game_area_content_descriptors` section. Mature-content
    notes use the same wrapper, so the heading is what distinguishes AI disclosure.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.found = False
        self.text = ""
        self._in_block = False
        self._block_depth = 0
        self._heading_matched = False
        self._in_h2 = False
        self._h2_chunks: list[str] = []
        self._in_p = False
        self._p_chunks: list[str] = []
        self._paragraphs: list[str] = []
        self._in_i = False
        self._i_chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs_list: list[tuple[str, str | None]]) -> None:
        attrs = {key: value or "" for key, value in attrs_list}
        if tag == "div" and attrs.get("id") == "game_area_content_descriptors":
            if self._in_block and self._heading_matched:
                self._save_text()
            self._in_block = True
            self._block_depth = 1
            self._heading_matched = False
            self._in_h2 = False
            self._in_p = False
            self._in_i = False
            self._paragraphs = []
            self._i_chunks = []
            return
        if tag == "div" and self._in_block:
            self._block_depth += 1
        if not self._in_block:
            return
        if tag == "h2":
            self._in_h2 = True
            self._h2_chunks = []
        elif self._heading_matched and tag == "p":
            self._in_p = True
            self._p_chunks = []
        elif self._heading_matched and tag == "i":
            self._in_i = True

    def handle_data(self, data: str) -> None:
        if self._in_h2:
            self._h2_chunks.append(data)
        elif self._in_p:
            self._p_chunks.append(data)
        elif self._in_i and self._heading_matched:
            self._i_chunks.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._in_h2 and tag == "h2":
            self._heading_matched = normalize_heading("".join(self._h2_chunks)) in AI_DISCLOSURE_HEADINGS
            if self._heading_matched:
                self.found = True
            self._in_h2 = False
            self._h2_chunks = []
            return
        if self._in_p and tag == "p":
            self._paragraphs.append("".join(self._p_chunks))
            self._in_p = False
            self._p_chunks = []
            return
        if self._in_i and tag == "i":
            self._in_i = False
            return
        if self._in_block and tag == "div":
            self._block_depth -= 1
            if self._block_depth <= 0:
                if self._heading_matched:
                    self._save_text()
                self._in_block = False
                self._heading_matched = False

    def finish(self) -> None:
        if self._heading_matched:
            self._save_text()

    def _save_text(self) -> None:
        paragraphs: list[str] = []
        for raw in self._paragraphs:
            text = clean_text(raw)
            if not text or normalize_heading(text) in AI_DISCLOSURE_BOILERPLATE:
                continue
            paragraphs.append(text)
        text = clean_text(" ".join(paragraphs))
        if not text:
            text = clean_text("".join(self._i_chunks))
        self.text = text[:800]
        self.found = True


def parse_ai_disclosure(page_html: str) -> tuple[bool, str]:
    parser = AiDisclosureParser()
    parser.feed(page_html)
    parser.finish()
    return parser.found, parser.text


def classify_store_page(page_html: str, final_url: str) -> str:
    """Return 'app', 'agegate', or 'other'."""
    url = (final_url or "").lower()
    if "/agecheck/" in url or "/login/" in url:
        return "agegate" if "/agecheck/" in url else "other"
    sample = page_html[:30000].lower()
    if 'name="ageyear"' in sample or "app_agegate" in sample or 'id="agegate' in sample:
        return "agegate"
    lowered = page_html.lower()
    if "apphubappname" in lowered or "game_area_purchase" in lowered or "glance_ctn" in lowered:
        return "app"
    return "other"


def fetch_store_app_page(appid: str, *, cc: str, lang: str) -> tuple[str, str]:
    params = urllib.parse.urlencode({"cc": cc, "l": lang})
    url = f"{BASE_URL}/app/{appid}/?{params}"
    language_cookie = "schinese" if lang == "schinese" else lang
    headers = dict(REQUEST_HEADERS)
    headers["Cookie"] = (
        "birthtime=568022401; lastagecheckage=1-January-1988; "
        "wants_mature_content=1; mature_content=1; timezoneOffset=28800,0; "
        f"Steam_Language={language_cookie}"
    )
    headers["Referer"] = "https://store.steampowered.com/"
    return fetch_response(url, timeout=30, headers=headers)


def fetch_ai_disclosure(appid: str, *, cc: str, lang: str) -> tuple[bool, str]:
    body, final_url = fetch_store_app_page(appid, cc=cc, lang=lang)
    kind = classify_store_page(body, final_url)
    if kind != "app":
        raise AiDisclosureUnavailable(f"{appid}: store page unreadable ({kind}); AI disclosure left unknown")
    return parse_ai_disclosure(body)


def ai_cache_is_fresh(cached: Any, now: dt.datetime, max_age_days: int) -> bool:
    if max_age_days <= 0 or not isinstance(cached, dict) or "ai_involved" not in cached:
        return False
    checked = cached.get("ai_checked_at")
    if not isinstance(checked, str) or not checked:
        return False
    try:
        parsed = dt.datetime.fromisoformat(checked)
    except ValueError:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=TIMEZONE)
    return now - parsed <= dt.timedelta(days=max_age_days)


def copy_cached_ai(item: dict[str, Any], cached: Any) -> None:
    if not isinstance(cached, dict):
        return
    for key in ("ai_involved", "ai_disclosure", "ai_checked_at"):
        if key in cached and key not in item:
            item[key] = cached[key]


def attach_ai_disclosures(
    items: list[dict[str, Any]],
    *,
    cache: dict[str, Any],
    cc: str,
    lang: str,
    log_lines: list[str],
    max_age_days: int = AI_CACHE_MAX_AGE_DAYS,
    cache_path: Path | None = None,
    now: dt.datetime | None = None,
) -> dict[str, int | bool]:
    """Fill ai_involved / ai_disclosure, reusing detail_cache.json when it is fresh."""
    now = now or dt.datetime.now(TIMEZONE)
    unique: list[dict[str, Any]] = []
    by_appid: dict[str, dict[str, Any]] = {}
    for item in items:
        appid = str(item.get("appid") or "")
        if not appid or appid in by_appid:
            continue
        by_appid[appid] = item
        unique.append(item)

    stats: dict[str, int | bool] = {
        "checked": 0,
        "cache_hits": 0,
        "involved": 0,
        "failed": 0,
        "stopped": False,
    }
    stopped = False
    for item in unique:
        appid = str(item["appid"])
        cached = cache.get(appid)
        if ai_cache_is_fresh(cached, now, max_age_days):
            item["ai_involved"] = bool(cached.get("ai_involved"))
            item["ai_disclosure"] = cached.get("ai_disclosure") or ""
            item["ai_checked_at"] = cached.get("ai_checked_at") or ""
            stats["cache_hits"] = int(stats["cache_hits"]) + 1
            continue
        if stopped:
            continue
        try:
            involved, disclosure = fetch_ai_disclosure(appid, cc=cc, lang=lang)
        except urllib.error.HTTPError as exc:
            stats["failed"] = int(stats["failed"]) + 1
            log_lines.append(f"{appid}: AI disclosure HTTP {exc.code}")
            if exc.code == 429:
                stopped = True
                stats["stopped"] = True
                log_lines.append("AI disclosure fetch hit Steam rate limit; left remaining items unchecked.")
                if cache_path is not None:
                    save_detail_cache(cache_path, cache)
            continue
        except AiDisclosureUnavailable as exc:
            stats["failed"] = int(stats["failed"]) + 1
            log_lines.append(str(exc))
            continue
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            stats["failed"] = int(stats["failed"]) + 1
            log_lines.append(f"{appid}: AI disclosure failed: {type(exc).__name__}: {exc}")
            continue

        item["ai_involved"] = involved
        item["ai_disclosure"] = disclosure
        item["ai_checked_at"] = now.isoformat(timespec="seconds")
        remember_detail_cache(cache, item)
        stats["checked"] = int(stats["checked"]) + 1
        if int(stats["checked"]) % 25 == 0 and cache_path is not None:
            save_detail_cache(cache_path, cache)
        time.sleep(REQUEST_DELAY_SECONDS)

    for item in items:
        source = by_appid.get(str(item.get("appid") or ""))
        if source is None or source is item:
            continue
        for key in ("ai_involved", "ai_disclosure", "ai_checked_at"):
            if key in source:
                item[key] = source[key]

    stats["involved"] = sum(1 for item in by_appid.values() if item.get("ai_involved"))
    log_lines.append(
        f"AI disclosure checked={stats['checked']} cache_hits={stats['cache_hits']} "
        f"involved={stats['involved']} failed={stats['failed']} stopped={stats['stopped']}"
    )
    return stats


def clean_text(value: str) -> str:
    value = html.unescape(value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = value.replace("\xa0", " ")
    return re.sub(r"\s+", " ", value).strip()


def parse_review_summary(value: str) -> dict[str, Any]:
    text = clean_text(value)
    summary: dict[str, Any] = {
        "review_count": None,
        "review_positive_percent": None,
        "review_label": "",
    }
    if not text:
        return summary

    label_match = re.match(r"^(.+?)\s+(?:此游戏|This game)", text)
    if label_match:
        summary["review_label"] = label_match.group(1).strip()
    else:
        first_sentence = re.split(r"[。.]", text, maxsplit=1)[0].strip()
        if first_sentence:
            summary["review_label"] = first_sentence

    count_patterns = (
        r"([\d,]+)\s*篇用户评测",
        r"([\d,]+)\s*user reviews",
    )
    for pattern in count_patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            summary["review_count"] = int(match.group(1).replace(",", ""))
            break

    percent_match = re.search(r"(\d{1,3})\s*%\s*(?:为好评|of the .*?user reviews.*?positive)", text, flags=re.IGNORECASE)
    if percent_match:
        summary["review_positive_percent"] = int(percent_match.group(1))

    return summary


def fetch_response(
    url: str,
    *,
    timeout: int = 30,
    retries: int = HTTP_RETRIES,
    headers: dict[str, str] | None = None,
    max_bytes: int = 4_000_000,
) -> tuple[str, str]:
    request = urllib.request.Request(url, headers=headers or REQUEST_HEADERS)
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                body = response.read(max_bytes).decode(charset, errors="replace")
                return body, response.geturl()
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code not in {403, 429, 500, 502, 503, 504} or attempt >= retries:
                raise
            if exc.code == 429:
                time.sleep(RATE_LIMIT_COOLDOWN_SECONDS)
                continue
        except urllib.error.URLError as exc:
            last_error = exc
            if attempt >= retries:
                raise
        time.sleep(min(18, 2 ** attempt * 2))
    assert last_error is not None
    raise last_error


def fetch_text(url: str, *, timeout: int = 30, retries: int = HTTP_RETRIES) -> str:
    body, _final_url = fetch_response(url, timeout=timeout, retries=retries)
    return body


def fetch_json(url: str, *, timeout: int = 30) -> dict[str, Any]:
    return json.loads(fetch_text(url, timeout=timeout))


def chunks(values: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    size = max(1, size)
    return [values[index:index + size] for index in range(0, len(values), size)]


def fetch_store_short_description(item: dict[str, Any], *, cc: str, lang: str) -> str:
    url = item.get("steam_url") or f"{BASE_URL}/app/{item['appid']}/"
    separator = "&" if "?" in url else "?"
    url = f"{url}{separator}{urllib.parse.urlencode({'cc': cc, 'l': lang})}"
    parser = StoreMetaParser()
    parser.feed(fetch_text(url))
    return parser.description


def build_search_url(category: int | str, start: int, count: int, cc: str, lang: str) -> str:
    params = {
        "query": "",
        "start": start,
        "count": count,
        "sort_by": "Released_DESC",
        "sort_order": "DESC",
        "category1": category,
        "cc": cc,
        "l": lang,
        "ignore_preferences": 1,
    }
    return f"{SEARCH_URL}?{urllib.parse.urlencode(params)}"


def parse_release_date(text: str) -> dt.date | None:
    text = clean_text(text)
    if not text:
        return None

    chinese_match = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", text)
    if chinese_match:
        year, month, day = map(int, chinese_match.groups())
        return dt.date(year, month, day)

    for fmt in ("%Y-%m-%d", "%b %d, %Y", "%B %d, %Y", "%d %b, %Y", "%d %B, %Y"):
        try:
            return dt.datetime.strptime(text, fmt).date()
        except ValueError:
            continue

    return None


def search_kind(
    *,
    kind: str,
    category: int | str,
    cc: str,
    lang: str,
    max_pages: int,
    log_lines: list[str],
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    source_order = 0

    for page in range(max_pages):
        url = build_search_url(category, page * PAGE_SIZE, PAGE_SIZE, cc, lang)
        log_lines.append(f"Fetch search {kind} page={page + 1} url={url}")
        body = fetch_text(url)
        parser = SearchResultParser()
        parser.feed(body)
        if not parser.items:
            log_lines.append(f"No search rows found for {kind} page={page + 1}; stopping.")
            break

        for item in parser.items:
            release_date = prepare_search_item(item, kind=kind)
            if release_date is None:
                continue
            appid = item["appid"]
            if appid in seen:
                continue
            seen.add(appid)
            item["source_order"] = source_order
            source_order += 1
            results.append(item)

        time.sleep(REQUEST_DELAY_SECONDS)

    return results


def prepare_search_item(item: dict[str, Any], *, kind: str) -> dt.date | None:
    release_date = parse_release_date(item.get("release_date_text", ""))
    item["release_date"] = release_date.isoformat() if release_date else ""
    item["kind"] = kind
    item["tag_names"] = [TAG_NAMES[tag] for tag in item.get("tag_ids", []) if tag in TAG_NAMES][:5]
    if item.get("review_summary") and item.get("review_count") is None:
        item.update(parse_review_summary(item.get("review_summary", "")))
    return release_date


def parse_potential_search_page(
    *,
    kind: str,
    category: int,
    cc: str,
    lang: str,
    page: int,
    cutoff_date: dt.date,
    min_reviews: int,
    seen: set[str],
    log_lines: list[str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    url = build_search_url(category, page * PAGE_SIZE, PAGE_SIZE, cc, lang)
    log_lines.append(f"Fetch potential {kind} page={page + 1} url={url}")
    try:
        body = fetch_text(url)
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            log_lines.append(f"Potential {kind} page={page + 1} rate limited; using collected candidates.")
            return [], {
                "found_rows": 0,
                "candidates": 0,
                "qualified": 0,
                "older_than_window": 0,
                "oldest_date": None,
                "rate_limited": True,
            }
        raise
    parser = SearchResultParser()
    parser.feed(body)
    stats: dict[str, Any] = {
        "found_rows": len(parser.items),
        "candidates": 0,
        "qualified": 0,
        "older_than_window": 0,
        "oldest_date": None,
        "rate_limited": False,
    }
    qualified: list[dict[str, Any]] = []
    if not parser.items:
        return qualified, stats

    oldest_date: dt.date | None = None
    for item in parser.items:
        release_date = prepare_search_item(item, kind=kind)
        if release_date is None:
            continue
        if oldest_date is None or release_date < oldest_date:
            oldest_date = release_date
        if release_date < cutoff_date:
            stats["older_than_window"] += 1
            continue
        stats["candidates"] += 1
        appid = item["appid"]
        review_count = item.get("review_count")
        if appid in seen or not isinstance(review_count, int) or review_count <= min_reviews:
            continue
        seen.add(appid)
        qualified.append(item)

    stats["qualified"] = len(qualified)
    stats["oldest_date"] = oldest_date
    return qualified, stats


def collect_potential_items(
    *,
    cc: str,
    lang: str,
    fetched_at: str,
    today: dt.date,
    cache: dict[str, Any],
    batch_size: int,
    target_count: int,
    days: int,
    min_reviews: int,
    min_reviews_demo: int,
    max_pages: int,
    log_lines: list[str],
) -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
    cutoff_date = today - dt.timedelta(days=days)
    log_lines.append(
        f"Potential target={target_count} min_reviews>{min_reviews} min_reviews_demo>{min_reviews_demo} "
        f"cutoff={cutoff_date.isoformat()} max_pages={max_pages}"
    )
    raw_items: list[dict[str, Any]] = []
    sources = {"game": 998, "demo": 10}
    seen_by_kind = {kind: set() for kind in sources}
    active = {kind: True for kind in sources}
    stats_by_kind = {
        kind: {"pages": 0, "candidates": 0, "qualified": 0, "older_than_window": 0, "rate_limited": 0}
        for kind in sources
    }

    for page in range(max_pages):
        for kind, category in sources.items():
            if not active[kind]:
                continue
            page_items, page_stats = parse_potential_search_page(
                kind=kind,
                category=category,
                cc=cc,
                lang=lang,
                page=page,
                cutoff_date=cutoff_date,
                min_reviews=min_reviews_demo if kind == "demo" else min_reviews,
                seen=seen_by_kind[kind],
                log_lines=log_lines,
            )
            stats_by_kind[kind]["pages"] += 1
            stats_by_kind[kind]["candidates"] += int(page_stats["candidates"])
            stats_by_kind[kind]["qualified"] += int(page_stats["qualified"])
            stats_by_kind[kind]["older_than_window"] += int(page_stats["older_than_window"])
            stats_by_kind[kind]["rate_limited"] += int(bool(page_stats.get("rate_limited")))
            raw_items.extend(page_items)

            oldest_date = page_stats["oldest_date"]
            if page_stats.get("rate_limited"):
                active[kind] = False
            elif not page_stats["found_rows"]:
                active[kind] = False
                log_lines.append(f"No potential rows found for {kind} page={page + 1}; stopping {kind}.")
            elif page_stats["older_than_window"]:
                active[kind] = False
                log_lines.append(f"Potential {kind} reached cutoff date {cutoff_date.isoformat()} on page={page + 1}.")
            elif (
                kind == "demo"
                and stats_by_kind[kind]["qualified"] == 0
                and stats_by_kind[kind]["pages"] >= DEFAULT_POTENTIAL_DEMO_EMPTY_PAGE_LIMIT
            ):
                active[kind] = False
                log_lines.append(
                    f"Potential {kind} found no qualified items in first {DEFAULT_POTENTIAL_DEMO_EMPTY_PAGE_LIMIT} pages; stopping {kind}."
                )
            elif len(raw_items) >= target_count:
                selected_so_far = newest_items(raw_items, target_count)
                oldest_selected = parse_release_date(selected_so_far[-1].get("release_date", "")) if selected_so_far else None
                if oldest_selected and isinstance(oldest_date, dt.date) and oldest_date < oldest_selected:
                    active[kind] = False
                    log_lines.append(
                        f"Potential {kind} page={page + 1} is older than current top {target_count}; stopping {kind}."
                    )

        if not any(active.values()):
            break
        time.sleep(REQUEST_DELAY_SECONDS)

    for kind, stats in stats_by_kind.items():
        log_lines.append(
            f"Potential {kind}: pages={stats['pages']} candidates={stats['candidates']} qualified={stats['qualified']}"
        )

    for index, item in enumerate(raw_items):
        item["source_order"] = index

    selected = newest_items(raw_items, target_count)
    enriched, detail_errors = enrich_all_apps_store_browse(
        selected,
        cc=cc,
        lang=lang,
        fetched_at=fetched_at,
        cache=cache,
        batch_size=batch_size,
        log_lines=log_lines,
    )
    potential_items = newest_items(enriched, target_count)
    meta = {
        "target_count": target_count,
        "count": len(potential_items),
        "days": days,
        "cutoff_date": cutoff_date.isoformat(),
        "min_reviews_exclusive": min_reviews,
        "min_reviews_demo_exclusive": min_reviews_demo,
        "games": sum(1 for item in potential_items if item.get("kind") == "game"),
        "demos": sum(1 for item in potential_items if item.get("kind") == "demo"),
        "pages": {kind: stats["pages"] for kind, stats in stats_by_kind.items()},
        "candidates": {kind: stats["candidates"] for kind, stats in stats_by_kind.items()},
        "rate_limited": any(stats["rate_limited"] for stats in stats_by_kind.values()),
    }
    log_lines.append(
        f"Potential selected={len(potential_items)} games={meta['games']} demos={meta['demos']} "
        f"raw_qualified={len(selected)}"
    )
    return potential_items, meta, detail_errors


def store_browse_url(appids: list[str], *, cc: str, lang: str) -> str:
    payload = {
        "ids": [{"appid": int(appid)} for appid in appids if str(appid).isdigit()],
        "context": {"country_code": cc, "language": lang},
        "data_request": {"include_assets": True, "include_basic_info": True},
    }
    query = urllib.parse.urlencode(
        {"input_json": json.dumps(payload, ensure_ascii=False, separators=(",", ":"))}
    )
    return f"{STORE_BROWSE_URL}?{query}"


def fetch_store_browse_items(appids: list[str], *, cc: str, lang: str) -> dict[str, dict[str, Any]]:
    if not appids:
        return {}
    payload = fetch_json(store_browse_url(appids, cc=cc, lang=lang), timeout=45)
    result: dict[str, dict[str, Any]] = {}
    for item in payload.get("response", {}).get("store_items", []):
        appid = str(item.get("appid") or item.get("id") or "")
        if appid:
            result[appid] = item
    return result


def build_store_asset_url(asset_url_format: str, filename: str) -> str:
    if not asset_url_format or not filename:
        return ""
    path = asset_url_format.replace("${FILENAME}", filename)
    if path.startswith(("http://", "https://")):
        return path
    return urllib.parse.urljoin(STORE_ASSET_CDN, path)


def best_store_asset_url(store_item: dict[str, Any]) -> tuple[str, str]:
    assets = store_item.get("assets") or {}
    asset_format = assets.get("asset_url_format") or ""
    for key in STORE_ASSET_PRIORITY:
        url = build_store_asset_url(asset_format, assets.get(key) or "")
        if url:
            return url, key
    return "", ""


def store_browse_genres(store_item: dict[str, Any]) -> list[str]:
    basic_info = store_item.get("basic_info") or {}
    genres: list[str] = []
    for genre in basic_info.get("genres") or []:
        if isinstance(genre, dict):
            name = genre.get("name") or genre.get("description")
        else:
            name = str(genre)
        name = clean_text(name or "")
        if name and name not in genres:
            genres.append(name)
    return genres


def store_browse_people(values: Any) -> list[str]:
    names: list[str] = []
    if not isinstance(values, list):
        return names
    for value in values:
        if isinstance(value, dict):
            name = value.get("name") or value.get("display_name")
        else:
            name = str(value)
        name = clean_text(name or "")
        if name and name not in names:
            names.append(name)
    return names


def apply_store_browse_detail(item: dict[str, Any], store_item: dict[str, Any]) -> bool:
    basic_info = store_item.get("basic_info") or {}
    changed = False

    store_type = store_item.get("type")
    if store_type == 1 and item.get("kind") != "demo":
        item["kind"] = "demo"
        changed = True
    elif store_type == 0 and item.get("kind") != "game":
        item["kind"] = "game"
        changed = True

    title = clean_text(store_item.get("name") or "")
    if title and title != item.get("title"):
        item["title"] = title
        changed = True

    image_url, asset_key = best_store_asset_url(store_item)
    if image_url:
        item["image"] = image_url
        item["header_image"] = image_url
        item["asset_source"] = f"store_browse:{asset_key}"
        changed = True

    short_description = clean_text(basic_info.get("short_description") or "")
    if short_description:
        if short_description != item.get("short_description"):
            changed = True
        item["short_description"] = short_description
        item["description_source"] = "store_browse_short"
    elif not item.get("description_source"):
        item["description_source"] = "none"

    developers = basic_info.get("developers")
    developer_names = store_browse_people(developers)
    if developer_names:
        item["developers"] = developer_names
        changed = True

    publishers = basic_info.get("publishers")
    publisher_names = store_browse_people(publishers)
    if publisher_names:
        item["publishers"] = publisher_names
        changed = True

    genres = store_browse_genres(store_item)
    if genres:
        item["genres"] = genres
        changed = True

    purchase = store_item.get("best_purchase_option") or {}
    formatted_price = clean_text(purchase.get("formatted_final_price") or "")
    if formatted_price and formatted_price != item.get("price_text"):
        item["price_text"] = formatted_price
        changed = True
    final_price = purchase.get("final_price_in_cents")
    if isinstance(final_price, str) and final_price.isdigit():
        item["price_final"] = int(final_price)
        item["price_value"] = int(final_price)
        changed = True
    elif isinstance(final_price, int):
        item["price_final"] = final_price
        item["price_value"] = final_price
        changed = True

    discount_pct = purchase.get("discount_pct")
    if isinstance(discount_pct, int) and discount_pct > 0:
        item["discount_text"] = f"-{discount_pct}%"
        item["is_discounted"] = True
        changed = True
    item["is_free"] = bool(item.get("is_free")) or normalize_price(item.get("price_text")) == 0
    item["detail_error"] = ""
    item["_detail_success"] = True
    return changed


def apply_cached_or_fallback_detail(
    item: dict[str, Any],
    *,
    cache: dict[str, Any],
    cc: str,
    lang: str,
    allow_meta_fetch: bool,
) -> tuple[dict[str, Any], str | None]:
    appid = str(item.get("appid") or "")
    cached = cache.get(appid)
    if cached:
        return apply_cached_detail(item, cached), f"{appid}: used cached detail"

    if allow_meta_fetch and not (item.get("short_description") or "").strip():
        try:
            description = fetch_store_short_description(item, cc=cc, lang=lang)
        except Exception as exc:
            item["description_source"] = item.get("description_source") or "none"
            return item, f"{appid}: store meta failed: {type(exc).__name__}: {exc}"
        if description:
            item["short_description"] = description
            item["description_source"] = "store_meta"
            remember_detail_cache(cache, item)
            return item, None

    item["detail_error"] = item.get("detail_error") or "详情暂不可用"
    item["header_image"] = item.get("header_image") or item.get("image") or ""
    item["genres"] = item.get("genres") or item.get("tag_names", [])
    item["is_free"] = bool(item.get("is_free")) or normalize_price(item.get("price_text")) == 0
    item["description_source"] = item.get("description_source") or "none"
    return item, None


def enrich_all_apps_store_browse(
    items: list[dict[str, Any]],
    *,
    cc: str,
    lang: str,
    fetched_at: str,
    cache: dict[str, Any],
    batch_size: int,
    log_lines: list[str],
    allow_meta_fetch: bool = True,
) -> tuple[list[dict[str, Any]], list[str]]:
    enriched_by_appid: dict[str, dict[str, Any]] = {}
    detail_errors: list[str] = []
    item_by_appid = {str(item["appid"]): item for item in items}
    appids = list(item_by_appid)
    batches = chunks([{"appid": appid} for appid in appids], batch_size)
    log_lines.append(f"Fetch StoreBrowse details batches={len(batches)} batch_size={max(1, batch_size)} items={len(items)}")

    for index, batch in enumerate(batches, start=1):
        batch_appids = [str(item["appid"]) for item in batch]
        log_lines.append(f"StoreBrowse batch {index}/{len(batches)} appids={','.join(batch_appids)}")
        try:
            store_items = fetch_store_browse_items(batch_appids, cc=cc, lang=lang)
        except Exception as exc:
            error = f"StoreBrowse batch {index}/{len(batches)} failed: {type(exc).__name__}: {exc}"
            detail_errors.append(error)
            log_lines.append(error)
            store_items = {}

        for appid in batch_appids:
            item = item_by_appid[appid]
            copy_cached_ai(item, cache.get(appid))
            store_item = store_items.get(appid)
            if store_item:
                changed = apply_store_browse_detail(item, store_item)
                if not (item.get("short_description") or "").strip() and allow_meta_fetch:
                    try:
                        description = fetch_store_short_description(item, cc=cc, lang=lang)
                    except Exception as exc:
                        description = ""
                        warning = f"{appid}: store meta failed: {type(exc).__name__}: {exc}"
                        detail_errors.append(warning)
                        log_lines.append(f"Detail warning: {warning}")
                    if description:
                        item["short_description"] = description
                        item["description_source"] = "store_meta"
                        changed = True
                item = finalize_item(item, fetched_at)
                if changed or item.get("short_description") or item.get("asset_source"):
                    remember_detail_cache(cache, item)
                enriched_by_appid[appid] = item
                continue

            warning = f"{appid}: StoreBrowse item missing"
            item, fallback_error = apply_cached_or_fallback_detail(
                item,
                cache=cache,
                cc=cc,
                lang=lang,
                allow_meta_fetch=allow_meta_fetch,
            )
            if fallback_error:
                warning = f"{warning}; {fallback_error}"
            detail_errors.append(warning)
            log_lines.append(f"Detail warning: {warning}")
            enriched_by_appid[appid] = finalize_item(item, fetched_at)

        time.sleep(REQUEST_DELAY_SECONDS)

    return [enriched_by_appid[item["appid"]] for item in items if item["appid"] in enriched_by_appid], detail_errors


def detail_cache_entry(item: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "title",
        "header_image",
        "image",
        "asset_source",
        "short_description",
        "description_source",
        "developers",
        "publishers",
        "genres",
        "is_free",
        "kind",
        "screenshots",
    ]
    entry = {key: item.get(key) for key in keys}
    if "ai_involved" in item:
        entry["ai_involved"] = bool(item.get("ai_involved"))
        entry["ai_disclosure"] = clean_text(str(item.get("ai_disclosure") or ""))[:800]
        entry["ai_checked_at"] = str(item.get("ai_checked_at") or "")
    return entry


def remember_detail_cache(cache: dict[str, Any], item: dict[str, Any]) -> None:
    """Merge item details into the cache without dropping a previous AI check."""
    appid = str(item.get("appid") or "")
    if not appid:
        return
    current = cache.get(appid)
    entry = dict(current) if isinstance(current, dict) else {}
    fresh = detail_cache_entry(item)
    if "ai_involved" not in item:
        fresh.pop("ai_involved", None)
        fresh.pop("ai_disclosure", None)
        fresh.pop("ai_checked_at", None)
    entry.update(fresh)
    cache[appid] = entry


def apply_cached_detail(item: dict[str, Any], cached: dict[str, Any]) -> dict[str, Any]:
    for key, value in cached.items():
        if value not in (None, "", []):
            item[key] = value
    if item.get("short_description"):
        item["description_source"] = "cache"
    item["detail_error"] = "详情接口暂不可用，已使用缓存"
    return item


def load_detail_cache(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            return payload
    except (OSError, json.JSONDecodeError):
        return {}
    return {}


def save_detail_cache(path: Path, cache: dict[str, Any], *, keep_appids: set[str] | None = None) -> None:
    if keep_appids is not None:
        cache = {appid: entry for appid, entry in cache.items() if appid in keep_appids}
    atomic_write(path, json.dumps(cache, ensure_ascii=False, separators=(",", ":")))


def report_appids(report: dict[str, Any]) -> set[str]:
    return {
        str(item.get("appid"))
        for key in ("items", "potential_items")
        for item in report.get(key) or []
        if isinstance(item, dict) and item.get("appid")
    }


def normalize_price(price_text: str | None) -> int | None:
    if not price_text:
        return None
    text = price_text.strip().lower()
    if text in {"免费", "free", "free to play"} or "免费" in text:
        return 0
    match = re.search(r"[\d,.]+", text)
    if not match:
        return None
    number = match.group(0).replace(",", "")
    try:
        return int(round(float(number) * 100))
    except ValueError:
        return None


def finalize_item(item: dict[str, Any], fetched_at: str) -> dict[str, Any]:
    item.pop("_detail_success", None)
    if item.get("kind") not in {"game", "demo"}:
        title = str(item.get("title") or "").lower()
        item["kind"] = "demo" if "demo" in title else "game"
    item["fetched_at"] = fetched_at
    item["discount_text"] = item.get("discount_text") or ""
    item["is_discounted"] = bool(item.get("discount_text"))
    item["price_value"] = item.get("price_final")
    if item["price_value"] is None:
        item["price_value"] = normalize_price(item.get("price_text"))
    if not item.get("genres"):
        item["genres"] = item.get("tag_names", [])
    return item


def release_sort_key(item: dict[str, Any]) -> tuple[str, int]:
    source_order = item.get("source_order")
    if not isinstance(source_order, int):
        source_order = 999_999_999
    return (str(item.get("release_date") or ""), -source_order)


def newest_items(items: list[dict[str, Any]], target_count: int) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in sorted(items, key=release_sort_key, reverse=True):
        appid = str(item.get("appid") or "")
        if appid in seen:
            continue
        seen.add(appid)
        selected.append(item)
        if len(selected) >= target_count:
            break
    return selected


def load_existing_report(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def backfill_potential_items(
    items: list[dict[str, Any]],
    *,
    previous_report: dict[str, Any],
    target_count: int,
    cutoff_date: str,
    min_reviews: int,
    min_reviews_demo: int,
    log_lines: list[str],
) -> tuple[list[dict[str, Any]], dict[str, int | bool]]:
    stats: dict[str, int | bool] = {
        "fresh_count": len(items),
        "backfilled_count": 0,
        "final_count": len(items),
        "used_previous": False,
    }
    if len(items) >= target_count:
        return items, stats

    previous_items = previous_report.get("potential_items") or []
    if not isinstance(previous_items, list) or not previous_items:
        log_lines.append("Potential backfill skipped: no previous potential_items available.")
        return items, stats

    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in [*items, *previous_items]:
        if not isinstance(item, dict):
            continue
        appid = str(item.get("appid") or "")
        review_count = item.get("review_count")
        release_date = str(item.get("release_date") or "")
        if not appid.isdigit() or appid in seen:
            continue
        threshold = min_reviews_demo if item.get("kind") == "demo" else min_reviews
        if not isinstance(review_count, int) or review_count <= threshold:
            continue
        if release_date < cutoff_date:
            continue
        seen.add(appid)
        merged.append(item)

    backfilled = newest_items(merged, target_count)
    added = max(0, len(backfilled) - len(items))
    stats["backfilled_count"] = added
    stats["final_count"] = len(backfilled)
    stats["used_previous"] = added > 0
    log_lines.append(
        f"Potential backfilled from previous report: before={len(items)} added={added} total={len(backfilled)}"
    )
    return backfilled, stats


def collect_report(args: argparse.Namespace, *, out_path: Path) -> dict[str, Any]:
    now = dt.datetime.now(TIMEZONE)
    fetched_at = now.isoformat(timespec="seconds")
    log_lines: list[str] = [f"Started at {fetched_at}", f"Target latest items: {args.target_count}"]
    previous_report = load_existing_report(out_path.with_suffix(".json"))

    raw_items = search_kind(
        kind="combined",
        category="998,10",
        cc=args.cc,
        lang=args.lang,
        max_pages=args.max_pages,
        log_lines=log_lines,
    )

    if not raw_items:
        raise RuntimeError("Search succeeded but no matching releases were found.")

    raw_items = newest_items(raw_items, args.target_count)
    log_lines.append(f"Selected newest items: {len(raw_items)} from search candidates")

    cache_path = out_path.parent / DETAIL_CACHE_NAME
    detail_cache = load_detail_cache(cache_path)
    enriched, detail_errors = enrich_all_apps_store_browse(
        raw_items,
        cc=args.cc,
        lang=args.lang,
        fetched_at=fetched_at,
        cache=detail_cache,
        batch_size=args.store_browse_batch_size,
        log_lines=log_lines,
    )

    potential_items, potential_meta, potential_detail_errors = collect_potential_items(
        cc=args.cc,
        lang=args.lang,
        fetched_at=fetched_at,
        today=now.date(),
        cache=detail_cache,
        batch_size=args.store_browse_batch_size,
        target_count=args.potential_count,
        days=args.potential_days,
        min_reviews=args.potential_min_reviews,
        min_reviews_demo=args.potential_min_reviews_demo,
        max_pages=args.potential_max_pages,
        log_lines=log_lines,
    )
    potential_items, potential_backfill_meta = backfill_potential_items(
        potential_items,
        previous_report=previous_report,
        target_count=args.potential_count,
        cutoff_date=potential_meta["cutoff_date"],
        min_reviews=args.potential_min_reviews,
        min_reviews_demo=args.potential_min_reviews_demo,
        log_lines=log_lines,
    )
    potential_meta["count"] = len(potential_items)
    potential_meta["games"] = sum(1 for item in potential_items if item.get("kind") == "game")
    potential_meta["demos"] = sum(1 for item in potential_items if item.get("kind") == "demo")
    potential_meta["fresh_count"] = potential_backfill_meta["fresh_count"]
    potential_meta["backfilled_count"] = potential_backfill_meta["backfilled_count"]
    potential_meta["used_previous"] = potential_backfill_meta["used_previous"]
    detail_errors.extend(potential_detail_errors)

    all_items = newest_items(enriched, args.target_count)
    games = [item for item in all_items if item["kind"] == "game"]
    demos = [item for item in all_items if item["kind"] == "demo"]

    keep_appids = {str(item.get("appid")) for item in [*all_items, *potential_items] if item.get("appid")}
    attach_ai_disclosures(
        [*all_items, *potential_items],
        cache=detail_cache,
        cc=args.cc,
        lang=args.lang,
        log_lines=log_lines,
        max_age_days=args.ai_cache_days,
        cache_path=cache_path,
    )
    save_detail_cache(cache_path, detail_cache, keep_appids=keep_appids)

    report = {
        "meta": {
            "generated_at": fetched_at,
            "timezone": "Asia/Hong_Kong",
            "cc": args.cc,
            "lang": args.lang,
            "target_count": args.target_count,
            "source_note": "Steam 公开搜索页提供发售日期而非小时级发布时间；本报告按发售日期从新到旧选取最新条目。",
            "counts": {
                "total": len(all_items),
                "games": len(games),
                "demos": len(demos),
                "free": sum(1 for item in all_items if item.get("is_free")),
                "discounted": sum(1 for item in all_items if item.get("is_discounted")),
            },
            "potential": potential_meta,
            "detail_errors": detail_errors,
        },
        "items": all_items,
        "potential_items": potential_items,
        "log": log_lines,
    }
    return report


def render_html(report: dict[str, Any]) -> str:
    meta = report["meta"]
    items_json = safe_script_json(report["items"])
    potential_items = report.get("potential_items") or []
    potential_items_json = safe_script_json(potential_items)
    counts = meta["counts"]
    potential_meta = meta.get("potential") or {}
    generated_label = format_datetime(meta["generated_at"])
    target_label = f"最新 {meta.get('target_count', counts['total'])} 款"
    potential_count = potential_meta.get("count", len(potential_items))
    ai_checked = sum(1 for item in report.get("items") or [] if isinstance(item, dict) and "ai_involved" in item)
    ai_count = sum(1 for item in report.get("items") or [] if isinstance(item, dict) and item.get("ai_involved"))
    ai_metric = str(ai_count) if ai_checked else "—"

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="icon" type="image/svg+xml" href="favicon.svg">
  <title>Steam 最新 300 款报告</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg: #0f1417;
      --panel: #161d22;
      --panel-2: #1d262d;
      --ink: #edf4f5;
      --muted: #98a8ad;
      --line: rgba(255,255,255,.1);
      --accent: #78ddb2;
      --accent-2: #77b7e8;
      --shadow: 0 18px 46px rgba(0,0,0,.32);
      font-family: Inter, "Segoe UI", "Microsoft YaHei", system-ui, sans-serif;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      background: linear-gradient(180deg, #142025 0%, var(--bg) 42%, #0d1113 100%);
      color: var(--ink);
    }}
    a {{ color: inherit; text-decoration: none; }}
    button, input, select {{ font: inherit; }}
    .shell {{ width: min(1320px, calc(100% - 32px)); margin: 0 auto; padding: 28px 0 44px; }}
    header {{ display: grid; gap: 16px; margin-bottom: 20px; }}
    .topbar {{ display: flex; align-items: end; justify-content: space-between; gap: 20px; }}
    h1 {{ margin: 0; font-size: clamp(30px, 4.5vw, 48px); line-height: 1.05; letter-spacing: 0; }}
    .stamp {{ color: var(--muted); text-align: right; min-width: 260px; line-height: 1.65; }}
    .stamp strong {{ color: var(--ink); font-weight: 700; }}
    .metrics {{ display: grid; grid-template-columns: repeat(7, minmax(0, 1fr)); gap: 10px; }}
    .metric {{ background: rgba(255,255,255,.052); border: 1px solid var(--line); border-radius: 8px; padding: 13px 14px; }}
    .metric span {{ display: block; color: var(--muted); font-size: 13px; }}
    .metric strong {{ display: block; margin-top: 5px; font-size: 26px; }}
    .account-bar {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      align-items: center;
      justify-content: flex-end;
      margin-top: 14px;
    }}
    .account-bar form {{ display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }}
    .account-bar input {{
      border: 1px solid var(--line);
      border-radius: 7px;
      background: #0f171d;
      color: var(--ink);
      padding: 10px 12px;
      outline: none;
      min-width: 0;
    }}
    .account-user {{ color: var(--muted); }}
    .account-msg {{ margin: 0; color: #ffb4a8; font-size: 13px; }}
    .controls {{
      position: sticky;
      top: 0;
      z-index: 4;
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      align-items: center;
      justify-content: flex-end;
      padding: 12px 14px;
      margin: 22px 0;
      background: rgba(16,20,24,.88);
      border: 1px solid var(--line);
      border-radius: 8px;
      backdrop-filter: blur(16px);
      box-shadow: var(--shadow);
    }}
    select {{
      width: min(240px, 100%);
      border: 1px solid var(--line);
      border-radius: 7px;
      background: #0f171d;
      color: var(--ink);
      padding: 11px 12px;
      outline: none;
    }}
    .toggle, .tab, .page-btn, .bulk-open {{
      border: 1px solid var(--line);
      border-radius: 7px;
      background: #131c23;
      color: var(--ink);
      padding: 10px 12px;
      cursor: pointer;
      white-space: nowrap;
    }}
    .toggle.active, .tab.active, .page-btn.active {{ border-color: rgba(120,221,178,.72); background: rgba(120,221,178,.12); color: #e7fff3; }}
    #continueBtn {{ margin-left: auto; }}
    .bulk-open {{ border-color: rgba(119,183,232,.42); background: rgba(119,183,232,.12); color: #d7ecff; }}
    .bulk-open:disabled {{ cursor: wait; opacity: .7; }}
    .page-btn:disabled {{ cursor: not-allowed; opacity: .42; }}
    .tabs {{ display: flex; gap: 8px; margin: 0 0 14px; flex-wrap: wrap; align-items: center; }}
    .section-title {{ display: flex; align-items: end; justify-content: space-between; gap: 16px; margin: 20px 0 14px; }}
    .section-title h2 {{ margin: 0; font-size: 22px; }}
    .section-title p {{ margin: 0; color: var(--muted); }}
    .grid {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 16px; align-items: stretch; }}
    .card {{
      display: flex;
      flex-direction: column;
      min-height: 100%;
      background: linear-gradient(180deg, rgba(255,255,255,.06), rgba(255,255,255,.035));
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
    }}
    .cover {{
      position: relative;
      aspect-ratio: 16 / 9;
      background: linear-gradient(135deg, #202b32, #10161a);
      overflow: hidden;
    }}
    .cover img {{ position: relative; z-index: 1; width: 100%; height: 100%; object-fit: contain; display: block; background: #10161a; }}
    .cover img.broken {{ display: none; }}
    .placeholder {{ position: absolute; z-index: 0; inset: 0; background: linear-gradient(135deg, #202a31, #11171b); }}
    .badge-row {{ position: absolute; z-index: 2; left: 10px; right: 10px; bottom: 10px; display: flex; gap: 7px; flex-wrap: wrap; }}
    .badge {{ border-radius: 999px; padding: 5px 8px; font-size: 12px; background: rgba(0,0,0,.62); border: 1px solid rgba(255,255,255,.18); }}
    .badge.demo {{ color: #d7ecff; }}
    .badge.discount {{ color: #fff0b8; }}
    .badge.ai {{
      color: #ffe7c2;
      background: rgba(92, 48, 8, .82);
      border-color: rgba(227, 161, 90, .85);
      padding: 2px 6px;
      font-size: 11px;
      line-height: 1.4;
      letter-spacing: .04em;
    }}
    .badge.seen {{ color: #d5dde0; }}
    .card.seen {{ opacity: .68; }}
    .mark {{
      border: 1px solid var(--line);
      border-radius: 7px;
      background: transparent;
      color: var(--muted);
      padding: 9px 12px;
      cursor: pointer;
    }}
    .content {{ display: flex; flex: 1; flex-direction: column; padding: 14px; gap: 10px; }}
    .title-row {{ display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 12px; align-items: start; }}
    .title-row h3 {{ margin: 0; min-width: 0; font-size: 18px; line-height: 1.35; overflow-wrap: anywhere; }}
    .price {{ text-align: right; white-space: nowrap; font-weight: 800; color: var(--accent); }}
    .original {{ display: block; margin-top: 2px; color: var(--muted); font-size: 12px; text-decoration: line-through; font-weight: 400; }}
    .desc {{ margin: 0; color: #c9d7de; line-height: 1.55; display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }}
    .meta {{ display: flex; gap: 7px; flex-wrap: wrap; color: var(--muted); font-size: 13px; }}
    .chip {{ border: 1px solid var(--line); border-radius: 999px; padding: 5px 8px; background: rgba(0,0,0,.16); }}
    .chip.review {{ color: #e7fff3; border-color: rgba(120,221,178,.34); background: rgba(120,221,178,.1); }}
    .footer {{ margin-top: auto; display: flex; align-items: center; justify-content: flex-end; gap: 10px; padding-top: 8px; }}
    .open {{ border-radius: 7px; padding: 9px 12px; background: linear-gradient(135deg, var(--accent), var(--accent-2)); color: #071013; font-weight: 800; }}
    .empty {{ border: 1px dashed var(--line); border-radius: 8px; padding: 40px; color: var(--muted); text-align: center; }}
    .pagination {{ display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-top: 22px; color: var(--muted); }}
    .pages {{ display: flex; gap: 7px; flex-wrap: wrap; justify-content: flex-end; }}
    .page-btn {{ min-width: 42px; padding: 8px 10px; }}
    .credit {{ margin-top: 14px; color: var(--muted); font-size: 13px; text-align: right; }}
    @media (max-width: 900px) {{
      .topbar {{ display: block; }}
      .stamp {{ text-align: left; margin-top: 12px; }}
      .metrics {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    }}
    @media (max-width: 560px) {{
      .shell {{ width: min(100% - 20px, 1440px); padding-top: 18px; }}
      .metrics {{ grid-template-columns: 1fr; }}
      .controls {{ position: static; }}
      .account-bar, .account-bar form {{ justify-content: flex-start; }}
      .account-bar input {{ flex: 1 1 140px; }}
      .grid {{ grid-template-columns: 1fr; }}
      .title-row {{ display: block; }}
      .price {{ text-align: left; margin-top: 8px; }}
      .pagination {{ display: block; }}
      .pages {{ justify-content: flex-start; margin-top: 12px; }}
    }}
  </style>
</head>
<body>
  <div class="shell">
    <header>
      <div class="topbar">
        <div>
          <h1>Steam 最新 300 款报告</h1>
        </div>
        <div class="stamp">
          <div>生成时间 <strong>{html.escape(generated_label)}</strong></div>
          <div>榜单范围 <strong>{html.escape(target_label)}</strong></div>
        </div>
      </div>
      <div class="metrics">
        <div class="metric"><span>全部条目</span><strong>{counts["total"]}</strong></div>
        <div class="metric"><span>普通游戏</span><strong>{counts["games"]}</strong></div>
        <div class="metric"><span>Demo</span><strong>{counts["demos"]}</strong></div>
        <div class="metric"><span>潜力游戏</span><strong>{potential_count}</strong></div>
        <div class="metric"><span>免费</span><strong>{counts["free"]}</strong></div>
        <div class="metric" title="来自 Steam 商店页的「AI 生成内容披露」。尚未检查时显示为横线。"><span>AI 参与</span><strong>{ai_metric}</strong></div>
        <div class="metric"><span>折扣中</span><strong>{counts["discounted"]}</strong></div>
      </div>
      <div id="accountBar" class="account-bar"></div>
    </header>

    <div class="controls" role="region" aria-label="筛选">
      <button id="paidToggle" class="toggle" type="button" aria-pressed="false">只看付费</button>
      <button id="hideAiToggle" class="toggle" type="button" aria-pressed="false">隐藏 AI 参与作品</button>
      <button id="unseenToggle" class="toggle" type="button" aria-pressed="false">只看未看</button>
      <select id="sort">
        <option value="release-desc">发售日期：新到旧</option>
        <option value="price-asc">价格：低到高</option>
        <option value="price-desc">价格：高到低</option>
        <option value="title-asc">标题：A 到 Z</option>
      </select>
    </div>

    <div class="tabs" aria-label="分组">
      <button class="tab active" type="button" data-kind="all">全部</button>
      <button class="tab" type="button" data-kind="game">普通游戏</button>
      <button class="tab" type="button" data-kind="demo">Demo</button>
      <button class="tab" type="button" data-kind="potential">潜力游戏</button>
      <button id="continueBtn" class="toggle" type="button">从上次继续</button>
      <button id="bulkOpen" class="bulk-open" type="button">打开本页游戏</button>
    </div>

    <main id="app"></main>
  </div>

  <script id="report-data" type="application/json">{items_json}</script>
  <script id="potential-report-data" type="application/json">{potential_items_json}</script>
  <script src="report.js"></script>
</body>
</html>
"""


def format_datetime(value: str) -> str:
    parsed = dt.datetime.fromisoformat(value)
    return parsed.strftime("%Y-%m-%d %H:%M:%S %Z")


def safe_script_json(value: Any) -> str:
    return (
        json.dumps(value, ensure_ascii=False)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def atomic_write(path: Path, content: str, *, encoding: str = "utf-8") -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(content, encoding=encoding)
    os.replace(tmp_path, path)


def write_outputs(report: dict[str, Any], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    json_path = out_path.with_suffix(".json")
    log_path = out_path.parent / "run.log"

    atomic_write(json_path, json.dumps(report, ensure_ascii=False, indent=2))
    atomic_write(out_path, render_html(report))

    log_lines = list(report.get("log", []))
    counts = report["meta"]["counts"]
    log_lines.append(
        f"Completed total={counts['total']} games={counts['games']} demos={counts['demos']} "
        f"free={counts['free']} discounted={counts['discounted']}"
    )
    potential = report["meta"].get("potential")
    if potential:
        log_lines.append(
            f"Potential total={potential['count']} games={potential['games']} demos={potential['demos']} "
            f"cutoff={potential['cutoff_date']} min_reviews>{potential['min_reviews_exclusive']} "
            f"min_reviews_demo>{potential.get('min_reviews_demo_exclusive', 'n/a')}"
        )
        log_lines.append(
            f"Potential freshness fresh={potential.get('fresh_count', potential['count'])} "
            f"backfilled={potential.get('backfilled_count', 0)} "
            f"used_previous={potential.get('used_previous', False)} "
            f"rate_limited={potential.get('rate_limited', False)}"
        )
    ai_checked = sum(1 for item in report.get("items") or [] if isinstance(item, dict) and "ai_involved" in item)
    ai_involved = sum(1 for item in report.get("items") or [] if isinstance(item, dict) and item.get("ai_involved"))
    log_lines.append(f"AI disclosure on latest list checked={ai_checked} involved={ai_involved}")
    if report["meta"].get("detail_errors"):
        log_lines.append("Detail errors:")
        log_lines.extend(report["meta"]["detail_errors"])
    atomic_write(log_path, "\n".join(log_lines) + "\n")
    ensure_index_symlink(out_path)


def ensure_index_symlink(out_path: Path) -> None:
    if out_path.name != "latest.html":
        return
    index_path = out_path.parent / "index.html"
    if index_path.exists() or index_path.is_symlink():
        return
    index_path.symlink_to(out_path.name)


def backfill_ai_disclosures(
    report: dict[str, Any],
    *,
    cc: str,
    lang: str,
    out_path: Path,
    max_age_days: int,
) -> dict[str, int | bool]:
    cache_path = out_path.parent / DETAIL_CACHE_NAME
    detail_cache = load_detail_cache(cache_path)
    log_lines = report.setdefault("log", [])
    items = [
        item
        for key in ("items", "potential_items")
        for item in report.get(key) or []
        if isinstance(item, dict)
    ]
    stats = attach_ai_disclosures(
        items,
        cache=detail_cache,
        cc=cc,
        lang=lang,
        log_lines=log_lines,
        max_age_days=max_age_days,
        cache_path=cache_path,
    )
    save_detail_cache(cache_path, detail_cache, keep_appids=report_appids(report))
    return stats


def backfill_short_descriptions(
    report: dict[str, Any],
    *,
    cc: str,
    lang: str,
    out_path: Path,
    limit: int,
) -> int:
    cache_path = out_path.parent / DETAIL_CACHE_NAME
    detail_cache = load_detail_cache(cache_path)
    filled = 0
    attempted = 0
    for item in report.get("items", []):
        if (item.get("short_description") or "").strip():
            continue
        cached = detail_cache.get(str(item.get("appid")), {})
        cached_description = (cached.get("short_description") or "").strip()
        if cached_description:
            item["short_description"] = cached_description
            item["description_source"] = "cache"
            filled += 1
            continue
        if limit and attempted >= limit:
            break
        attempted += 1
        try:
            description = fetch_store_short_description(item, cc=cc, lang=lang)
        except Exception:
            description = ""
        if description:
            item["short_description"] = description
            item["description_source"] = "store_meta"
            remember_detail_cache(detail_cache, item)
            filled += 1
            time.sleep(REQUEST_DELAY_SECONDS)
    save_detail_cache(cache_path, detail_cache, keep_appids=report_appids(report))
    report.setdefault("log", []).append(
        f"Backfilled short descriptions: {filled}; attempted={attempted} at "
        f"{dt.datetime.now(TIMEZONE).isoformat(timespec='seconds')}"
    )
    return filled


def backfill_assets(
    report: dict[str, Any],
    *,
    cc: str,
    lang: str,
    out_path: Path,
    batch_size: int,
) -> tuple[int, list[str]]:
    cache_path = out_path.parent / DETAIL_CACHE_NAME
    detail_cache = load_detail_cache(cache_path)
    items = report.get("items", [])
    fetched_at = report.get("meta", {}).get("generated_at") or dt.datetime.now(TIMEZONE).isoformat(timespec="seconds")
    log_lines = report.setdefault("log", [])
    before = {
        str(item.get("appid")): (
            item.get("image") or "",
            item.get("header_image") or "",
            item.get("short_description") or "",
        )
        for item in items
    }
    enriched, detail_errors = enrich_all_apps_store_browse(
        items,
        cc=cc,
        lang=lang,
        fetched_at=fetched_at,
        cache=detail_cache,
        batch_size=batch_size,
        log_lines=log_lines,
    )
    changed = 0
    for item in enriched:
        appid = str(item.get("appid"))
        after = (
            item.get("image") or "",
            item.get("header_image") or "",
            item.get("short_description") or "",
        )
        if before.get(appid) != after:
            changed += 1
    report["items"] = enriched
    save_detail_cache(cache_path, detail_cache, keep_appids=report_appids(report))
    report.setdefault("meta", {})["detail_errors"] = detail_errors
    log_lines.append(
        f"Backfilled StoreBrowse assets: changed={changed}; warnings={len(detail_errors)} at "
        f"{dt.datetime.now(TIMEZONE).isoformat(timespec='seconds')}"
    )
    return changed, detail_errors


def write_failure_log(out_path: Path, exc: BaseException) -> None:
    log_path = out_path.parent / "run.log"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    content = [
        f"FAILED at {dt.datetime.now(TIMEZONE).isoformat(timespec='seconds')}",
        f"{type(exc).__name__}: {exc}",
        traceback.format_exc(),
        "Existing latest.html was preserved.",
    ]
    atomic_write(log_path, "\n".join(content) + "\n")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a static Steam new releases HTML report.")
    parser.add_argument("--cc", default=DEFAULT_CC, help="Steam country code, default CN.")
    parser.add_argument("--lang", default=DEFAULT_LANG, help="Steam language, default schinese.")
    parser.add_argument(
        "--target-count",
        type=int,
        default=DEFAULT_TARGET_COUNT,
        help="Number of newest combined game/demo releases to keep, default 300.",
    )
    parser.add_argument("--out", default="latest.html", help="Output HTML path, default latest.html.")
    parser.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES, help="Search page safety cap per kind.")
    parser.add_argument(
        "--potential-count",
        type=int,
        default=DEFAULT_POTENTIAL_COUNT,
        help="Number of potential game/demo releases to keep, default 100.",
    )
    parser.add_argument(
        "--potential-days",
        type=int,
        default=DEFAULT_POTENTIAL_DAYS,
        help="Potential list release window in days, default 365.",
    )
    parser.add_argument(
        "--potential-min-reviews",
        type=int,
        default=DEFAULT_POTENTIAL_MIN_REVIEWS,
        help="Potential list requires review_count greater than this value, default 50.",
    )
    parser.add_argument(
        "--potential-min-reviews-demo",
        type=int,
        default=DEFAULT_POTENTIAL_MIN_REVIEWS_DEMO,
        help="Potential list requires demo review_count greater than this value, default 10.",
    )
    parser.add_argument(
        "--potential-max-pages",
        type=int,
        default=DEFAULT_POTENTIAL_MAX_PAGES,
        help="Search page safety cap per kind for the potential list, default 160.",
    )
    parser.add_argument(
        "--render-from",
        default="",
        help="Render HTML from an existing report JSON without fetching Steam.",
    )
    parser.add_argument(
        "--backfill-descriptions",
        action="store_true",
        help="When used with --render-from, fetch only missing Steam short/meta descriptions before rendering.",
    )
    parser.add_argument(
        "--backfill-assets",
        action="store_true",
        help="When used with --render-from, batch-fill high resolution Steam assets and short descriptions.",
    )
    parser.add_argument(
        "--backfill-limit",
        type=int,
        default=0,
        help="Maximum missing descriptions to fetch with --backfill-descriptions; 0 means no limit.",
    )
    parser.add_argument(
        "--store-browse-batch-size",
        type=int,
        default=DEFAULT_STORE_BROWSE_BATCH_SIZE,
        help="StoreBrowse appids per request, default 50.",
    )
    parser.add_argument(
        "--backfill-ai",
        action="store_true",
        help="When used with --render-from, fetch missing or stale AI disclosures before rendering.",
    )
    parser.add_argument(
        "--ai-cache-days",
        type=int,
        default=AI_CACHE_MAX_AGE_DAYS,
        help="Reuse a cached AI disclosure for this many days, default 7. 0 refetches every run.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    if args.target_count < 1:
        raise SystemExit("--target-count must be at least 1")
    if args.potential_count < 1:
        raise SystemExit("--potential-count must be at least 1")
    if args.potential_days < 1:
        raise SystemExit("--potential-days must be at least 1")
    if args.potential_min_reviews < 0:
        raise SystemExit("--potential-min-reviews must be at least 0")
    if args.potential_min_reviews_demo < 0:
        raise SystemExit("--potential-min-reviews-demo must be at least 0")
    if args.potential_max_pages < 1:
        raise SystemExit("--potential-max-pages must be at least 1")
    if args.ai_cache_days < 0:
        raise SystemExit("--ai-cache-days must be at least 0")
    out_path = Path(args.out).resolve()

    try:
        if args.render_from:
            report = json.loads(Path(args.render_from).read_text(encoding="utf-8"))
            report["log"] = [
                f"Rendered HTML from {Path(args.render_from).resolve()} at "
                f"{dt.datetime.now(TIMEZONE).isoformat(timespec='seconds')}"
            ]
            if args.backfill_assets:
                changed, detail_errors = backfill_assets(
                    report,
                    cc=args.cc,
                    lang=args.lang,
                    out_path=out_path,
                    batch_size=args.store_browse_batch_size,
                )
                print(f"Backfilled StoreBrowse assets: changed={changed} warnings={len(detail_errors)}")
            if args.backfill_descriptions:
                filled = backfill_short_descriptions(
                    report,
                    cc=args.cc,
                    lang=args.lang,
                    out_path=out_path,
                    limit=args.backfill_limit,
                )
                print(f"Backfilled short descriptions: {filled}")
            if args.backfill_ai:
                ai_stats = backfill_ai_disclosures(
                    report,
                    cc=args.cc,
                    lang=args.lang,
                    out_path=out_path,
                    max_age_days=args.ai_cache_days,
                )
                print(
                    "Backfilled AI disclosures: "
                    f"checked={ai_stats['checked']} cache_hits={ai_stats['cache_hits']} "
                    f"involved={ai_stats['involved']} failed={ai_stats['failed']}"
                )
        else:
            report = collect_report(args, out_path=out_path)
        write_outputs(report, out_path)
        counts = report["meta"]["counts"]
        print(f"Generated {out_path}")
        print(f"Items: total={counts['total']} games={counts['games']} demos={counts['demos']}")
        if report["meta"].get("detail_errors"):
            print(f"Detail warnings: {len(report['meta']['detail_errors'])}")
        return 0
    except (urllib.error.URLError, urllib.error.HTTPError, RuntimeError, TimeoutError, OSError) as exc:
        write_failure_log(out_path, exc)
        print(f"Failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
