import datetime as dt
import io
import json
import os
import re
import sys
import unittest
import urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts", "radar"))

import check_refs  # noqa: E402
import collect  # noqa: E402

NOW = dt.datetime(2026, 11, 10, 3, 0, tzinfo=dt.timezone.utc)

SITEMAP = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:xhtml="http://www.w3.org/1999/xhtml">
<url><loc>https://playvalorant.com/ko-kr/news/game-updates/valorant-patch-notes-1309/</loc><lastmod>2026-10-27T13:00:00.000Z</lastmod>
<xhtml:link rel="alternate" hreflang="en-us" href="https://playvalorant.com/en-us/news/game-updates/valorant-patch-notes-1309/"/></url>
<url><loc>https://playvalorant.com/ko-kr/news/game-updates/valorant-patch-notes-13-08/</loc><lastmod>2026-10-13T13:00:00.000Z</lastmod></url>
<url><loc>https://playvalorant.com/ko-kr/news/announcements/2026-11-pcb-event/</loc><lastmod>2026-10-30T00:00:00.000Z</lastmod></url>
</urlset>"""

RSS = """<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>t</title>
<item><title> 발로란트 2027 VCT 한국 예선, 25일 개막 </title><link>https://www.inven.co.kr/webzine/news/?news=1</link>
<pubDate>Mon, 09 Nov 2026 11:13:10 +0900</pubDate><description>본문 요약</description></item>
</channel></rss>""".encode("utf-8")


class CollectTest(unittest.TestCase):
    def setUp(self):
        self._fetch = collect.fetch

    def tearDown(self):
        collect.fetch = self._fetch

    def test_patch_notes_from_sitemap(self):
        collect.fetch = lambda url, retries=1: SITEMAP
        src = {"id": "pv-patch-ko", "name": "발로란트 공식 패치 노트", "cat": "valorant", "group": "공식·패치", "max": 3,
               "url": "https://playvalorant.com/ko-kr/sitemap_loc.xml",
               "sitemap": {"pattern": "/ko-kr/news/game-updates/valorant-patch-notes-(\\d+)(?:-(\\d+))?/?$", "title": "발로란트 패치 노트 {v}"}}
        got = collect.collect_source(src, NOW)
        self.assertEqual([g["t"] for g in got], ["발로란트 패치 노트 13.09", "발로란트 패치 노트 13.08"])
        self.assertEqual(got[0]["p"], "2026-10-27T13:00:00Z")
        self.assertIn("패치", got[0]["tags"])

    def test_rss_item_shape_matches_both_front_ends(self):
        collect.fetch = lambda url, retries=1: RSS
        src = {"id": "inven-val", "name": "인벤 발로란트 뉴스", "cat": "valorant", "group": "뉴스", "url": "https://www.inven.co.kr/x"}
        got = collect.collect_source(src, NOW)
        self.assertEqual(len(got), 1)
        it = got[0]
        # won-radar index.html 과 valorant-manual radar.html cleanItem 이 읽는 칸 (빠지면 안 됨)
        for k in ("id", "t", "u", "s", "c", "g", "p", "f", "d", "img", "tags"):
            self.assertIn(k, it)
        self.assertEqual(it["t"], "발로란트 2027 VCT 한국 예선, 25일 개막")
        self.assertEqual(it["p"], "2026-11-09T02:13:10Z")

    def test_youtube_sources_are_refused(self):
        with self.assertRaises(ValueError):
            collect.collect_source({"id": "x", "name": "x", "cat": "valorant", "group": "영상",
                                    "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCx"}, NOW)
        with self.assertRaises(ValueError):
            collect.collect_source({"id": "x", "name": "x", "cat": "valorant", "group": "영상", "youtube": "@x", "url": ""}, NOW)

    def test_merge_caps_by_category_and_keep_days_by_source(self):
        def item(i, cat, days, sid="s"):
            return {"id": f"{cat}{i}", "t": f"{cat} 제목 {i}", "c": cat, "sid": sid, "p": collect.iso(NOW - dt.timedelta(days=days)), "f": collect.iso(NOW)}
        fresh = [item(i, "ai", 0.1) for i in range(10)] + [item(i, "valorant", 0.2) for i in range(5)] + [item(99, "valorant", 30, "pv")]
        out = collect.merge(fresh, [], 14, 900, {"s", "pv"}, NOW, {"pv": 45}, {"ai": 3, "valorant": 10})
        cats = [x["c"] for x in out]
        self.assertEqual(cats.count("ai"), 3)
        self.assertEqual(cats.count("valorant"), 6)          # 5개 + 45일 보관 패치 노트 1개
        self.assertIn("valorant99", [x["id"] for x in out])
        out2 = collect.merge(fresh, [], 14, 900, {"s", "pv"}, NOW)
        self.assertNotIn("valorant99", [x["id"] for x in out2])   # 소스별 보관 일수가 없으면 14일

    def test_sources_json_has_no_youtube_or_blocked_paths(self):
        with open(os.path.join(ROOT, "scripts", "radar", "sources.json"), encoding="utf-8") as f:
            cfg = json.load(f)
        for s in cfg["sources"]:
            self.assertNotIn("youtube", s)
            self.assertNotRegex(s["url"], r"youtube\.com|youtu\.be")
        caps = cfg.get("max_items_by_cat", {})
        self.assertLessEqual(sum(caps.values()), cfg["max_items"])


class _Resp:
    def __init__(self, body, status=200, url=""):
        self._b, self.status, self._u = body, status, url

    def read(self, n=-1):
        return self._b

    def geturl(self):
        return self._u

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class CheckRefsTest(unittest.TestCase):
    def fake(self, robots_body=None, robots_code=200, page=b"<title>Hi</title>"):
        calls = []

        def opener(req, timeout=0):
            calls.append((req.full_url, req.get_header("User-agent")))
            if req.full_url.endswith("/robots.txt"):
                if robots_code != 200:
                    raise urllib.error.HTTPError(req.full_url, robots_code, "x", {}, io.BytesIO(b""))
                return _Resp(robots_body or b"")
            return _Resp(page, 200, req.full_url)
        return opener, calls

    def test_robots_disallowed_is_not_opened(self):
        opener, calls = self.fake(b"User-agent: *\nDisallow: /\n")
        res = check_refs.check_url("https://www.reddit.com/r/VALORANT/", check_refs.Robots(opener), opener)
        self.assertEqual(res["status"], 403)
        self.assertTrue(res["robots"])
        self.assertEqual([u for u, _ in calls], ["https://www.reddit.com/robots.txt"])

    def test_allowed_page_and_honest_user_agent(self):
        opener, calls = self.fake(b"User-agent: *\nAllow: /\n")
        res = check_refs.check_url("https://example.org/guide", check_refs.Robots(opener), opener)
        self.assertEqual(res["status"], 200)
        self.assertEqual(res["title"], "Hi")
        self.assertTrue(all(ua.startswith("WON-Radar/") for _, ua in calls))

    def test_robots_403_blocks_and_404_allows(self):
        opener, _ = self.fake(robots_code=403)
        self.assertTrue(check_refs.check_url("https://a.example/x", check_refs.Robots(opener), opener)["robots"])
        opener, _ = self.fake(robots_code=404)
        self.assertEqual(check_refs.check_url("https://b.example/x", check_refs.Robots(opener), opener)["status"], 200)

    def test_youtube_links_are_skipped(self):
        self.assertTrue(check_refs.is_youtube("https://www.youtube.com/@ThinkingMansValorant"))
        self.assertTrue(check_refs.is_youtube("https://youtu.be/abc"))
        self.assertFalse(check_refs.is_youtube("https://blitz.gg/valorant"))


class RulesTest(unittest.TestCase):
    """매뉴얼북 selfcheck 7 과 같은 생각: 유튜브의 막힌 주소 · 페이지를 읽는 코드가 없어야 함."""
    BLOCKED = re.compile(r"feeds/videos\.xml|youtube\.com/results|/youtubei/|get_video_info|watch_ajax|youtube\.com/[@]|BROWSER_UA|Chrome/\d")

    def test_no_blocked_youtube_paths_or_fake_browser(self):
        for root in ("scripts/radar", ".github/workflows"):
            for name in os.listdir(os.path.join(ROOT, root)):
                if not name.endswith((".py", ".yml")):   # 코드만 (레퍼런스 데이터의 유튜브 주소는 링크일 뿐)
                    continue
                with open(os.path.join(ROOT, root, name), encoding="utf-8") as f:
                    text = f.read()
                self.assertIsNone(self.BLOCKED.search(text), f"{root}/{name}")

    def test_no_api_key_shapes_in_repo(self):
        for base, dirs, files in os.walk(ROOT):
            if ".git" in base.split(os.sep) or "node_modules" in base:
                continue
            for name in files:
                if name.endswith((".png", ".jpg", ".webp", ".ico")):
                    continue
                path = os.path.join(base, name)
                try:
                    with open(path, encoding="utf-8") as f:
                        text = f.read()
                except (UnicodeDecodeError, OSError):
                    continue
                self.assertIsNone(re.search(r"AIza[0-9A-Za-z_-]{35}|sk-ant-[0-9A-Za-z_-]{20,}", text), path)


if __name__ == "__main__":
    unittest.main()
