import datetime as dt
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import generate_report as report


AI_PAGE = """
<html><head><title>Sample</title></head><body>
<div class="glance_ctn" id="appHubAppName">Sample</div>
<div id="game_area_content_descriptors" class="game_area_description">
  <h2>成人内容描述</h2>
  <p>开发者对内容描述如下：</p>
  <p><i>暴力</i></p>
</div>
<div id="game_area_content_descriptors" class="game_area_description">
  <h2>AI 生成内容披露</h2>
  <p>开发者对其游戏如何使用 AI 生成内容的描述如下：</p>
  <p><i>画面和部分对白使用了 AI 生成。</i></p>
</div>
</body></html>
"""

ENGLISH_PAGE = """
<div id="appHubAppName">Sample</div>
<div id="game_area_content_descriptors">
  <h2>AI Generated Content Disclosure</h2>
  <p>The developers describe how their game uses AI Generated Content like this:</p>
  <p><i>Voice lines were synthesized.</i></p>
</div>
"""

CLEAN_PAGE = """
<div id="appHubAppName">Plain</div>
<div class="game_area_description"><p>A normal game about robots, not a disclosure.</p></div>
"""

AGE_PAGE = """
<form id="agegate_box"><select name="ageYear"></select></form>
"""


class AiDisclosureTests(unittest.TestCase):
    def test_chinese_heading_and_ignores_mature_content(self) -> None:
        found, text = report.parse_ai_disclosure(AI_PAGE)
        self.assertTrue(found)
        self.assertIn("画面和部分对白", text)
        self.assertNotIn("暴力", text)
        self.assertNotIn("描述如下", text)

    def test_english_heading(self) -> None:
        found, text = report.parse_ai_disclosure(ENGLISH_PAGE)
        self.assertTrue(found)
        self.assertEqual(text, "Voice lines were synthesized.")

    def test_page_without_disclosure_is_not_involved(self) -> None:
        found, text = report.parse_ai_disclosure(CLEAN_PAGE)
        self.assertFalse(found)
        self.assertEqual(text, "")

    def test_age_gate_is_not_cached_as_clean(self) -> None:
        self.assertEqual(report.classify_store_page(AGE_PAGE, "https://store.steampowered.com/agecheck/app/1/"), "agegate")
        self.assertEqual(report.classify_store_page(CLEAN_PAGE, "https://store.steampowered.com/app/1/"), "app")

    def test_cache_preserves_negative_result_and_expires(self) -> None:
        now = dt.datetime(2026, 10, 20, tzinfo=dt.timezone.utc)
        fresh = {"ai_involved": False, "ai_disclosure": "", "ai_checked_at": "2026-10-18T00:00:00+00:00"}
        stale = {"ai_involved": False, "ai_disclosure": "", "ai_checked_at": "2026-09-01T00:00:00+00:00"}
        self.assertTrue(report.ai_cache_is_fresh(fresh, now, 7))
        self.assertFalse(report.ai_cache_is_fresh(stale, now, 7))
        self.assertFalse(report.ai_cache_is_fresh(fresh, now, 0))

    def test_detail_cache_merge_keeps_existing_ai_check(self) -> None:
        cache: dict = {"10": {"title": "Old", "ai_involved": True, "ai_disclosure": "voices", "ai_checked_at": "2026-10-01T00:00:00+08:00"}}
        report.remember_detail_cache(cache, {"appid": "10", "title": "New", "short_description": "hello"})
        self.assertEqual(cache["10"]["title"], "New")
        self.assertTrue(cache["10"]["ai_involved"])
        self.assertEqual(cache["10"]["ai_disclosure"], "voices")

    def test_render_includes_filter_and_script(self) -> None:
        html = report.render_html({
            "meta": {
                "generated_at": "2026-10-20T12:00:00+08:00",
                "target_count": 1,
                "counts": {"total": 1, "games": 1, "demos": 0, "free": 0, "discounted": 0},
                "potential": {"count": 0},
            },
            "items": [{
                "appid": "1",
                "title": "Demo",
                "kind": "game",
                "ai_involved": True,
                "ai_disclosure": "art",
            }],
            "potential_items": [],
        })
        self.assertIn("隐藏 AI 参与作品", html)
        self.assertIn("只看未看", html)
        self.assertIn("从上次继续", html)
        self.assertIn('src="report.js"', html)
        self.assertIn('"ai_involved": true', html)
        self.assertIn(">1<", html)


if __name__ == "__main__":
    unittest.main()
