from __future__ import annotations

import argparse
import concurrent.futures
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
APPDETAILS_URL = f"{BASE_URL}/api/appdetails"
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
DEFAULT_POTENTIAL_MAX_PAGES = 160
DEFAULT_POTENTIAL_DEMO_EMPTY_PAGE_LIMIT = 12
DEFAULT_DETAIL_WORKERS = 3
DEFAULT_STORE_BROWSE_BATCH_SIZE = 50
PAGE_SIZE = 25
REQUEST_DELAY_SECONDS = 0.35
DETAIL_CACHE_NAME = "detail_cache.json"
HTTP_RETRIES = 3
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

        if tag == "a" and "search_result_row" in classes and attrs.get("data-ds-appid"):
            self._item = {
                "appid": attrs.get("data-ds-appid", "").strip(),
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


def fetch_text(url: str, *, timeout: int = 30, retries: int = HTTP_RETRIES) -> str:
    request = urllib.request.Request(url, headers=REQUEST_HEADERS)
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                return response.read().decode(charset, errors="replace")
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code not in {403, 429, 500, 502, 503, 504} or attempt >= retries:
                raise
        except urllib.error.URLError as exc:
            last_error = exc
            if attempt >= retries:
                raise
        time.sleep(min(18, 2 ** attempt * 2))
    assert last_error is not None
    raise last_error


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


def build_search_url(category: int, start: int, count: int, cc: str, lang: str) -> str:
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
    category: int,
    cc: str,
    lang: str,
    max_pages: int,
    log_lines: list[str],
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    seen: set[str] = set()

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
    max_pages: int,
    log_lines: list[str],
) -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
    cutoff_date = today - dt.timedelta(days=days)
    log_lines.append(
        f"Potential target={target_count} min_reviews>{min_reviews} cutoff={cutoff_date.isoformat()} max_pages={max_pages}"
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
                min_reviews=min_reviews,
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


def enrich_app(item: dict[str, Any], *, cc: str, lang: str) -> tuple[dict[str, Any], str | None]:
    appid = item["appid"]
    params = {"appids": appid, "cc": cc, "l": lang}
    url = f"{APPDETAILS_URL}?{urllib.parse.urlencode(params)}"
    try:
        payload = fetch_json(url)
        app_payload = payload.get(str(appid), {})
        if not app_payload.get("success"):
            item["detail_error"] = "详情接口返回失败"
            return item, f"{appid}: appdetails success=false"
        data = app_payload.get("data", {})
        item["title"] = data.get("name") or item.get("title") or ""
        item["header_image"] = data.get("header_image") or item.get("image") or ""
        item["image"] = data.get("capsule_image") or item.get("image") or data.get("header_image") or ""
        item["short_description"] = clean_text(data.get("short_description") or "")
        item["description_source"] = "appdetails_short" if item["short_description"] else "none"
        if not item["short_description"]:
            try:
                item["short_description"] = fetch_store_short_description(item, cc=cc, lang=lang)
                item["description_source"] = "store_meta" if item["short_description"] else "none"
            except Exception:
                item["description_source"] = "none"
        item["developers"] = data.get("developers") or []
        item["publishers"] = data.get("publishers") or []
        item["genres"] = [genre.get("description", "") for genre in data.get("genres", []) if genre.get("description")]
        item["is_free"] = bool(data.get("is_free")) or normalize_price(item.get("price_text")) == 0
        item["screenshots"] = [
            shot.get("path_thumbnail")
            for shot in data.get("screenshots", [])[:3]
            if shot.get("path_thumbnail")
        ]
        item["_detail_success"] = True
        item["detail_error"] = ""
        return item, None
    except Exception as exc:  # Keep the report useful when a single app fails.
        item["header_image"] = item.get("image") or ""
        try:
            item["short_description"] = fetch_store_short_description(item, cc=cc, lang=lang)
            item["description_source"] = "store_meta" if item["short_description"] else "none"
        except Exception:
            item["short_description"] = ""
            item["description_source"] = "none"
        item["developers"] = []
        item["publishers"] = []
        item["genres"] = item.get("tag_names", [])
        item["is_free"] = normalize_price(item.get("price_text")) == 0
        item["screenshots"] = []
        item["detail_error"] = "详情暂不可用"
        return item, f"{appid}: {type(exc).__name__}: {exc}"


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
            cache[appid] = detail_cache_entry(item)
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
                    cache[appid] = detail_cache_entry(item)
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


def enrich_all_apps(
    items: list[dict[str, Any]],
    *,
    cc: str,
    lang: str,
    fetched_at: str,
    workers: int,
    cache: dict[str, Any],
    log_lines: list[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    enriched_by_appid: dict[str, dict[str, Any]] = {}
    detail_errors: list[str] = []
    workers = max(1, min(workers, 12))
    log_lines.append(f"Fetch details with workers={workers} items={len(items)}")

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        future_map = {
            executor.submit(enrich_app, item, cc=cc, lang=lang): item["appid"]
            for item in items
        }
        completed = 0
        for future in concurrent.futures.as_completed(future_map):
            appid = future_map[future]
            completed += 1
            try:
                enriched_item, error = future.result()
            except Exception as exc:
                fallback = next(item for item in items if item["appid"] == appid)
                fallback["detail_error"] = "详情暂不可用"
                fallback["header_image"] = fallback.get("image") or ""
                fallback["short_description"] = ""
                fallback["description_source"] = "none"
                fallback["developers"] = []
                fallback["publishers"] = []
                fallback["genres"] = fallback.get("tag_names", [])
                fallback["is_free"] = normalize_price(fallback.get("price_text")) == 0
                fallback["screenshots"] = []
                enriched_item = fallback
                error = f"{appid}: {type(exc).__name__}: {exc}"
            if error:
                cached = cache.get(appid)
                if cached:
                    enriched_item = apply_cached_detail(enriched_item, cached)
                    error = f"{error}; used cached detail"
                elif enriched_item.get("short_description"):
                    cache[appid] = detail_cache_entry(enriched_item)
                detail_errors.append(error)
                log_lines.append(f"Detail warning: {error}")
            elif enriched_item.get("_detail_success"):
                cache[appid] = detail_cache_entry(enriched_item)
            if completed % 25 == 0 or completed == len(items):
                log_lines.append(f"Detail progress {completed}/{len(items)}")
            enriched_item.pop("_detail_success", None)
            enriched_by_appid[appid] = finalize_item(enriched_item, fetched_at)

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
        "screenshots",
    ]
    return {key: item.get(key) for key in keys}


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


def save_detail_cache(path: Path, cache: dict[str, Any]) -> None:
    atomic_write(path, json.dumps(cache, ensure_ascii=False, indent=2))


def detail_platforms(platforms: dict[str, Any]) -> list[str]:
    names: list[str] = []
    if platforms.get("windows"):
        names.append("Windows")
    if platforms.get("mac"):
        names.append("macOS")
    if platforms.get("linux"):
        names.append("Linux")
    return names


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
    item["fetched_at"] = fetched_at
    item["discount_text"] = item.get("discount_text") or ""
    item["is_discounted"] = bool(item.get("discount_text"))
    item["price_value"] = item.get("price_final")
    if item["price_value"] is None:
        item["price_value"] = normalize_price(item.get("price_text"))
    if not item.get("genres"):
        item["genres"] = item.get("tag_names", [])
    return item


def release_sort_key(item: dict[str, Any]) -> tuple[str, str]:
    return (str(item.get("release_date") or ""), str(item.get("title") or ""))


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


def collect_report(args: argparse.Namespace, *, out_path: Path) -> dict[str, Any]:
    now = dt.datetime.now(TIMEZONE)
    fetched_at = now.isoformat(timespec="seconds")
    log_lines: list[str] = [f"Started at {fetched_at}", f"Target latest items: {args.target_count}"]

    raw_items: list[dict[str, Any]] = []
    raw_items.extend(
        search_kind(
            kind="game",
            category=998,
            cc=args.cc,
            lang=args.lang,
            max_pages=args.max_pages,
            log_lines=log_lines,
        )
    )
    raw_items.extend(
        search_kind(
            kind="demo",
            category=10,
            cc=args.cc,
            lang=args.lang,
            max_pages=args.max_pages,
            log_lines=log_lines,
        )
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
        max_pages=args.potential_max_pages,
        log_lines=log_lines,
    )
    detail_errors.extend(potential_detail_errors)
    save_detail_cache(cache_path, detail_cache)

    all_items = newest_items(enriched, args.target_count)
    games = [item for item in all_items if item["kind"] == "game"]
    demos = [item for item in all_items if item["kind"] == "demo"]

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

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
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
    .metrics {{ display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 10px; }}
    .metric {{ background: rgba(255,255,255,.052); border: 1px solid var(--line); border-radius: 8px; padding: 13px 14px; }}
    .metric span {{ display: block; color: var(--muted); font-size: 13px; }}
    .metric strong {{ display: block; margin-top: 5px; font-size: 26px; }}
    .controls {{
      position: sticky;
      top: 0;
      z-index: 4;
      display: grid;
      grid-template-columns: auto minmax(190px, 240px);
      gap: 10px;
      align-items: center;
      justify-content: end;
      padding: 12px 14px;
      margin: 22px 0;
      background: rgba(16,20,24,.88);
      border: 1px solid var(--line);
      border-radius: 8px;
      backdrop-filter: blur(16px);
      box-shadow: var(--shadow);
    }}
    select {{
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 7px;
      background: #0f171d;
      color: var(--ink);
      padding: 11px 12px;
      outline: none;
    }}
    .toggle, .tab, .page-btn {{
      border: 1px solid var(--line);
      border-radius: 7px;
      background: #131c23;
      color: var(--ink);
      padding: 10px 12px;
      cursor: pointer;
      white-space: nowrap;
    }}
    .toggle.active, .tab.active, .page-btn.active {{ border-color: rgba(120,221,178,.72); background: rgba(120,221,178,.12); color: #e7fff3; }}
    .page-btn:disabled {{ cursor: not-allowed; opacity: .42; }}
    .tabs {{ display: flex; gap: 8px; margin: 0 0 14px; flex-wrap: wrap; }}
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
    @media (max-width: 900px) {{
      .topbar {{ display: block; }}
      .stamp {{ text-align: left; margin-top: 12px; }}
      .metrics {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .controls {{ grid-template-columns: 1fr 1fr; }}
      .grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    }}
    @media (max-width: 560px) {{
      .shell {{ width: min(100% - 20px, 1440px); padding-top: 18px; }}
      .metrics {{ grid-template-columns: 1fr; }}
      .controls {{ position: static; grid-template-columns: 1fr; }}
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
        <div class="metric"><span>折扣中</span><strong>{counts["discounted"]}</strong></div>
      </div>
    </header>

    <div class="controls" role="region" aria-label="筛选">
      <button id="paidToggle" class="toggle" type="button">只看付费</button>
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
    </div>

    <main id="app"></main>
  </div>

  <script id="report-data" type="application/json">{items_json}</script>
  <script id="potential-report-data" type="application/json">{potential_items_json}</script>
  <script>
    const items = JSON.parse(document.getElementById('report-data').textContent);
    const potentialItems = JSON.parse(document.getElementById('potential-report-data').textContent || '[]');
    const pageSize = 30;
    const state = {{ kind: 'all', paidOnly: false, sort: 'release-desc', page: 1 }};
    const app = document.getElementById('app');
    const collator = new Intl.Collator('zh-CN', {{ numeric: true, sensitivity: 'base' }});

    function esc(value) {{
      return String(value ?? '').replace(/[&<>"']/g, ch => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[ch]));
    }}

    function currentItems() {{
      const source = state.kind === 'potential' ? potentialItems : items;
      return source.filter(item => {{
        if (!['all', 'potential'].includes(state.kind) && item.kind !== state.kind) return false;
        if (state.paidOnly && item.is_free && item.kind !== 'demo') return false;
        return true;
      }}).sort((a, b) => {{
        if (state.sort === 'price-asc') return (a.price_value ?? 999999999) - (b.price_value ?? 999999999);
        if (state.sort === 'price-desc') return (b.price_value ?? -1) - (a.price_value ?? -1);
        if (state.sort === 'title-asc') return collator.compare(a.title || '', b.title || '');
        return String(b.release_date || '').localeCompare(String(a.release_date || '')) || collator.compare(a.title || '', b.title || '');
      }});
    }}

    function resetPage() {{
      state.page = 1;
    }}

    function card(item) {{
      const kindLabel = item.kind === 'demo' ? 'Demo' : '普通游戏';
      const genreChips = (item.genres && item.genres.length ? item.genres : item.tag_names || []).slice(0, 4);
      const desc = (item.short_description || '').trim();
      const descHtml = desc ? `<p class="desc">${{esc(desc)}}</p>` : '';
      const original = item.original_price_text && item.original_price_text !== item.price_text ? `<span class="original">${{esc(item.original_price_text)}}</span>` : '';
      const discount = item.discount_text ? `<span class="badge discount">${{esc(item.discount_text)}}</span>` : '';
      const review = item.review_count ? `<span class="chip review">${{Number(item.review_count).toLocaleString('zh-CN')}} 篇 · ${{item.review_positive_percent ?? '?'}}% 好评</span>` : '';
      return `
        <article class="card" data-appid="${{esc(item.appid)}}">
          <div class="cover">
            <div class="placeholder"></div>
            <img src="${{esc(item.header_image || item.image)}}" alt="${{esc(item.title)}} 封面" loading="lazy" onerror="this.classList.add('broken')">
            <div class="badge-row">
              <span class="badge ${{item.kind === 'demo' ? 'demo' : ''}}">${{kindLabel}}</span>
              ${{discount}}
            </div>
          </div>
          <div class="content">
            <div class="title-row">
              <h3>${{esc(item.title)}}</h3>
              <div class="price">${{esc(item.price_text || '价格未显示')}}${{original}}</div>
            </div>
            ${{descHtml}}
            <div class="meta">
              <span class="chip">${{esc(item.release_date || item.release_date_text || '日期未知')}}</span>
              ${{review}}
              ${{genreChips.map(value => `<span class="chip">${{esc(value)}}</span>`).join('')}}
            </div>
            <div class="footer">
              <a class="open" href="${{esc(item.steam_url || `https://store.steampowered.com/app/${{item.appid}}/`)}}" target="_blank" rel="noreferrer">打开 Steam</a>
            </div>
          </div>
        </article>
      `;
    }}

    function pageNumbers(current, total) {{
      return Array.from({{ length: total }}, (_, index) => index + 1);
    }}

    function renderPagination(totalItems, totalPages) {{
      if (totalItems === 0) return '';
      const buttons = pageNumbers(state.page, totalPages).map(page => (
        `<button class="page-btn ${{page === state.page ? 'active' : ''}}" type="button" data-page="${{page}}">${{page}}</button>`
      )).join('');
      return `
        <div class="pagination">
          <div>第 ${{state.page}} / ${{totalPages}} 页，共 ${{totalItems}} 个结果</div>
          <div class="pages">
            <button class="page-btn" type="button" data-page="prev" ${{state.page === 1 ? 'disabled' : ''}}>上一页</button>
            ${{buttons}}
            <button class="page-btn" type="button" data-page="next" ${{state.page === totalPages ? 'disabled' : ''}}>下一页</button>
          </div>
        </div>
      `;
    }}

    function render() {{
      const titleMap = {{ all: '全部新品', game: '普通游戏', demo: 'Demo', potential: '潜力游戏' }};
      const list = currentItems();
      const totalPages = Math.max(1, Math.ceil(list.length / pageSize));
      state.page = Math.min(state.page, totalPages);
      const start = (state.page - 1) * pageSize;
      const pageItems = list.slice(start, start + pageSize);
      const body = pageItems.length ? `<div class="grid">${{pageItems.map(card).join('')}}</div>` : `<div class="empty">当前筛选下没有结果。</div>`;
      return `
        <section>
          <div class="section-title">
            <h2>${{titleMap[state.kind]}}</h2>
            <p>${{list.length}} 个结果 · 每页 ${{pageSize}} 个</p>
          </div>
          ${{body}}
          ${{renderPagination(list.length, totalPages)}}
        </section>
      `;
    }}

    function update() {{
      app.innerHTML = render();
    }}

    document.getElementById('sort').addEventListener('change', event => {{ state.sort = event.target.value; resetPage(); update(); }});
    document.getElementById('paidToggle').addEventListener('click', event => {{
      state.paidOnly = !state.paidOnly;
      event.currentTarget.classList.toggle('active', state.paidOnly);
      resetPage();
      update();
    }});
    document.querySelectorAll('.tab').forEach(button => {{
      button.addEventListener('click', () => {{
        state.kind = button.dataset.kind;
        document.querySelectorAll('.tab').forEach(tab => tab.classList.toggle('active', tab === button));
        resetPage();
        update();
      }});
    }});
    app.addEventListener('click', event => {{
      const button = event.target.closest('[data-page]');
      if (!button) return;
      const list = currentItems();
      const totalPages = Math.max(1, Math.ceil(list.length / pageSize));
      const target = button.dataset.page;
      if (target === 'prev') state.page = Math.max(1, state.page - 1);
      else if (target === 'next') state.page = Math.min(totalPages, state.page + 1);
      else state.page = Number(target);
      update();
      window.scrollTo({{ top: 0, behavior: 'smooth' }});
    }});
    update();
  </script>
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
            f"cutoff={potential['cutoff_date']} min_reviews>{potential['min_reviews_exclusive']}"
        )
    if report["meta"].get("detail_errors"):
        log_lines.append("Detail errors:")
        log_lines.extend(report["meta"]["detail_errors"])
    atomic_write(log_path, "\n".join(log_lines) + "\n")


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
            detail_cache[str(item["appid"])] = detail_cache_entry(item)
            filled += 1
            time.sleep(REQUEST_DELAY_SECONDS)
    save_detail_cache(cache_path, detail_cache)
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
    save_detail_cache(cache_path, detail_cache)
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
        "--detail-workers",
        type=int,
        default=DEFAULT_DETAIL_WORKERS,
        help="Legacy appdetails worker setting kept for compatibility; StoreBrowse is used by default.",
    )
    parser.add_argument(
        "--store-browse-batch-size",
        type=int,
        default=DEFAULT_STORE_BROWSE_BATCH_SIZE,
        help="StoreBrowse appids per request, default 50.",
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
    if args.potential_max_pages < 1:
        raise SystemExit("--potential-max-pages must be at least 1")
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
