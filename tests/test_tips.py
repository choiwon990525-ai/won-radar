import copy
import datetime as dt
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts", "radar"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tips  # noqa: E402
from fakeyt import FakeYT, KEY, cid, vid  # noqa: E402

NOW = dt.datetime(2026, 11, 10, 3, 0, tzinfo=dt.timezone.utc)   # 한국 11/10(화) 정오
CFG = {
    "limits": {"search_per_day": 10, "search_per_run": 3, "units_per_day": 3000, "videos_calls_per_run": 12,
               "channel_new_per_run": 8, "channel_first": 15, "search_results": 15, "search_days": 730,
               "refresh_days": 7, "expire_days": 29, "max_items": 1500},
    "week": {"size": 15, "search_max": 12, "per_query": 2, "per_channel": 2, "channel_days": 14, "long_sec": 180},
    "old": {"days": 365, "map_changes": {"corrode": "2026-10-14"}},
    "exclude_title": "하이라이트|버그|\\b(highlights?|bugs?)\\b",
    "names": {"maps": {"bind": "바인드", "corrode": "코로드"}, "agents": {"fade": "페이드"}},
    "channels": [
        {"handle": "@tipch", "name": "꿀팁 채널", "kind": "꿀팁·강의", "lang": "ko", "weekly": True},
        {"handle": "@metach", "name": "메타 채널", "kind": "대회·메타", "lang": "en", "weekly": False},
    ],
    "queries": [
        {"id": "bind-off-ko", "q": "발로란트 바인드 오프앵글", "lang": "ko", "label": "바인드 · 오프앵글", "to": "mg:bind:offrefs", "map": "bind"},
        {"id": "corrode-wall-ko", "q": "발로란트 코로드 월샷", "lang": "ko", "label": "코로드 · 월뱅", "to": "mg:corrode:wallrefs", "map": "corrode"},
        {"id": "fade-tip-en", "q": "valorant Fade tips", "lang": "en", "label": "페이드 · 꿀팁", "to": "agent:fade"},
        {"id": "extra-ko", "q": "발로란트 여분", "lang": "ko", "label": "여분", "to": "mg:bind:tiprefs", "map": "bind"},
    ],
}


def iso(d):
    return tips.iso(d)


def world():
    f = FakeYT()
    f.channels["@tipch"] = (cid(1), "꿀팁 채널(유튜브 이름)")
    f.channels["@metach"] = (cid(2), "Meta Channel")
    f.uploads["UU" + cid(1)[2:]] = [vid(1), vid(2), vid(3)]
    f.uploads["UU" + cid(2)[2:]] = [vid(4)]
    f.search_hits["바인드"] = [vid(10), vid(11), vid(12)]
    f.search_hits["코로드"] = [vid(20), vid(21)]
    f.search_hits["Fade"] = [vid(30)]
    recent = iso(NOW - dt.timedelta(days=3))
    f.videos.update({
        vid(1): {"t": "어센트 꿀팁 10분 강의", "cid": cid(1), "p": recent, "dur": 600},
        vid(2): {"t": "짧은 꿀팁", "cid": cid(1), "p": recent, "dur": 40},
        vid(3): {"t": "방송 하이라이트", "cid": cid(1), "p": recent, "dur": 500},           # 제목 거르기
        vid(4): {"t": "Pro meta talk", "cid": cid(2), "p": recent, "dur": 900},
        vid(10): {"t": "바인드 오프앵글 모음", "cid": cid(5), "p": iso(NOW - dt.timedelta(days=30)), "dur": 420},
        vid(11): {"t": "바인드 오프앵글 2", "cid": cid(6), "p": iso(NOW - dt.timedelta(days=400)), "dur": 300},  # 1년 넘음 → 예전
        vid(12): {"t": "바인드 비공개", "cid": cid(7), "p": recent, "dur": 300, "public": False},
        vid(20): {"t": "코로드 월샷", "cid": cid(8), "p": "2026-09-01T00:00:00Z", "dur": 200},   # 13.08 전 → 예전
        vid(21): {"t": "코로드 월샷 아동용", "cid": cid(8), "p": recent, "dur": 200, "kids": True},
        vid(30): {"t": "Fade tips", "cid": cid(9), "p": recent, "dur": 0, "live": "upcoming"},  # 예정 방송
    })
    return f


class TipsTest(unittest.TestCase):
    def run_once(self, fake, prev=None, now=NOW, event="schedule", key=KEY, cfg=CFG, skip=(), block=()):
        return tips.run(copy.deepcopy(cfg), prev or {}, now, key, event, skip, block, opener=fake, sleep=lambda s: None)

    def test_first_run_collects_with_provenance_only(self):
        f = world()
        data, week = self.run_once(f)
        ids = {it["id"] for it in data["items"]}
        self.assertEqual(ids, {vid(1), vid(2), vid(4), vid(10), vid(11), vid(20)})
        it = next(x for x in data["items"] if x["id"] == vid(10))
        self.assertEqual(it["via"], ["q:bind-off-ko"])
        self.assertEqual(it["to"], "mg:bind:offrefs")
        self.assertEqual(it["map"], "bind")
        self.assertEqual(it["lang"], "ko")
        self.assertFalse(it["old"])
        self.assertTrue(it["code"].startswith("T"))
        self.assertTrue(it["u"].endswith(vid(10)))
        self.assertNotIn("d", it, "설명은 거르기에만 쓰고 저장하지 않아요")
        self.assertNotIn("kind", it)
        ch_item = next(x for x in data["items"] if x["id"] == vid(1))
        self.assertIsNone(ch_item["to"], "채널에서 찾은 영상은 넣을 칸을 짐작하지 않아요")
        self.assertTrue(next(x for x in data["items"] if x["id"] == vid(11))["old"])   # 1년 넘음
        self.assertTrue(next(x for x in data["items"] if x["id"] == vid(20))["old"])   # 코로드 13.08 전
        self.assertEqual(data["status"]["error"], "")
        self.assertEqual(f.count("search"), 3)
        self.assertEqual(data["usage"]["search"], 3)
        self.assertEqual(data["state"]["qi"], 3)

    def test_week_list_numbers_and_file_has_no_titles(self):
        data, week = self.run_once(world())
        self.assertEqual(week["week"], "2026-W46")
        self.assertEqual(week["start"], "2026-11-09")
        ns = [e["n"] for e in week["items"]]
        self.assertEqual(ns, list(range(1, len(ns) + 1)))
        self.assertLessEqual(len(ns), 15)
        self.assertEqual(week["items"][0]["id"], vid(10))          # 빈 곳 검색어 먼저 · 예전 아닌 것 먼저
        self.assertIn(vid(1), [e["id"] for e in week["items"]])    # 꿀팁 채널 새 긴 영상
        self.assertNotIn(vid(4), [e["id"] for e in week["items"]])  # 메타 채널은 이번 주 후보에 안 넣음
        for e in week["items"]:
            self.assertEqual(set(e) - {"gone"}, {"n", "code", "id", "to", "via"})
        self.assertTrue(week["sealed"])
        text = json.dumps(week, ensure_ascii=False)
        self.assertNotIn("오프앵글 모음", text)
        offered = [it for it in data["items"] if it.get("o") == "2026-W46"]
        self.assertEqual(len(offered), len(week["items"]))

    def test_same_week_keeps_list_and_next_week_skips_offered(self):
        data, week = self.run_once(world())
        later = NOW + dt.timedelta(days=2)
        data2, week2 = self.run_once(world(), prev=data, now=later)
        self.assertEqual(week2["items"], week["items"])
        next_week = NOW + dt.timedelta(days=7)
        data3, week3 = self.run_once(world(), prev=data2, now=next_week)
        self.assertEqual(week3["week"], "2026-W47")
        self.assertFalse({e["id"] for e in week3["items"]} & {e["id"] for e in week["items"]})

    def test_channel_shorts_stay_out_of_week(self):
        """채널 새 영상 중 1분 이하(쇼츠)는 이번 주 후보에 안 넣고 목록에만 둬요. 검색으로 찾은 짧은 영상은 후보 그대로."""
        cfg = copy.deepcopy(CFG)
        cfg["channels"].append({"handle": "@shortch", "name": "쇼츠 채널", "kind": "셋업·스킬", "lang": "ko", "weekly": True})
        f = world()
        f.channels["@shortch"] = (cid(3), "쇼츠 채널")
        f.uploads["UU" + cid(3)[2:]] = [vid(40)]
        f.search_hits["바인드"] = [vid(41)] + f.search_hits["바인드"]
        recent = iso(NOW - dt.timedelta(days=1))
        f.videos[vid(40)] = {"t": "라인업 한 장", "cid": cid(3), "p": recent, "dur": 30}
        f.videos[vid(41)] = {"t": "바인드 오프앵글 쇼츠", "cid": cid(4), "p": recent, "dur": 45}
        data, week = self.run_once(f, cfg=cfg)
        picked = {e["id"] for e in week["items"]}
        self.assertIn(vid(40), {it["id"] for it in data["items"]}, "목록(전체)에는 있어요")
        self.assertNotIn(vid(40), picked)
        self.assertIn(vid(41), picked, "검색어로 찾은 짧은 영상은 후보")

    def test_push_or_keyless_week_is_provisional(self):
        """push 실행(검색 안 함)에서 고른 목록은 굳히지 않고, 다음 예약 실행에서 검색 결과까지 넣어 다시 골라요."""
        data, week = self.run_once(world(), event="push")
        self.assertFalse(week["sealed"])
        self.assertTrue(week["items"])
        self.assertTrue(all(e["via"].startswith("ch:") for e in week["items"]))
        data2, week2 = self.run_once(world(), prev=data, now=NOW + dt.timedelta(hours=3))
        self.assertTrue(week2["sealed"])
        self.assertEqual(week2["items"][0]["id"], vid(10), "검색 결과(빈 곳 먼저)가 앞에 와요")
        offered = {it["id"] for it in data2["items"] if it.get("o") == "2026-W46"}
        self.assertEqual(offered, {e["id"] for e in week2["items"]}, "다시 고를 때 빠진 영상은 다음에 다시 후보")
        data3, week3 = self.run_once(world(), prev=data2, now=NOW + dt.timedelta(hours=6), event="push")
        self.assertEqual(week3["items"], week2["items"], "굳힌 목록은 push 실행에도 그대로")
        empty, wk0 = self.run_once(FakeYT(), key="")
        self.assertFalse(wk0["sealed"])
        self.assertEqual(wk0["items"], [])
        _, wk1 = self.run_once(world(), prev=empty, now=NOW + dt.timedelta(hours=3))
        self.assertTrue(wk1["items"], "키 없이 만든 빈 목록은 키가 생기면 다시 골라요")

    def test_push_event_does_not_search(self):
        f = world()
        data, _ = self.run_once(f, event="push")
        self.assertEqual(f.count("search"), 0)
        self.assertEqual(data["usage"]["search"], 0)

    def test_search_day_budget(self):
        f = world()
        prev = {"usage": {"day": "2026-11-09", "search": 9, "units": 10}}   # 태평양 11/9 = 한국 11/10 정오
        data, _ = self.run_once(f, prev=prev)
        self.assertEqual(f.count("search"), 1)
        self.assertEqual(data["usage"]["search"], 10)

    def test_filters_and_skip(self):
        f = world()
        data, _ = self.run_once(f, skip=(cid(5),), block=("어센트",))
        ids = {it["id"] for it in data["items"]}
        self.assertNotIn(vid(10), ids)   # 뺄 채널
        self.assertNotIn(vid(1), ids)    # 소속 낱말(시험용)
        self.assertNotIn(vid(3), ids)    # 제목 거르기
        self.assertNotIn(vid(12), ids)   # 비공개
        self.assertNotIn(vid(21), ids)   # 아동용
        self.assertNotIn(vid(30), ids)   # 예정 방송
        data2, _ = self.run_once(f, prev=data, now=NOW + dt.timedelta(hours=6), skip=(cid(5),), block=("어센트",))
        self.assertNotIn(vid(10), {it["id"] for it in data2["items"]})

    def test_public_file_has_no_skip_list(self):
        """tips.json 은 공개 — 뺄 채널(핸들·id)과 걸러 낸 영상 id 가 파일에 남으면 Secret 이 새요."""
        f = world()
        f.channels["@hidden-academy"] = (cid(5), "숨길 채널")
        data, week = self.run_once(f, skip=("@hidden-academy", "@no-such-handle"), block=("어센트",))
        ids = {it["id"] for it in data["items"]}
        self.assertNotIn(vid(10), ids, "@핸들로 적은 뺄 채널도 걸러요")
        text = json.dumps([data, week], ensure_ascii=False)
        for secret in ("hidden-academy", "no-such-handle", cid(5), vid(10), vid(1), vid(3), "어센트"):
            self.assertNotIn(secret, text)
        row = next(r for r in data["sources"] if r["id"] == "skip")
        self.assertFalse(row["ok"])
        self.assertIn("1개", row["error"])
        self.assertEqual(set(data["state"]), {"next_code", "qi", "channels", "past"})

    def test_past_weeks_are_kept_for_late_replies(self):
        data, week = self.run_once(world())
        data2, week2 = self.run_once(world(), prev=data, now=NOW + dt.timedelta(days=7))
        self.assertEqual([w["id"] for w in week2["past"]], ["2026-W46"])
        self.assertEqual(week2["past"][0]["items"], week["items"])
        prev = data2
        for i in range(2, 7):
            prev, wk = self.run_once(world(), prev=prev, now=NOW + dt.timedelta(days=7 * i))
        self.assertEqual(len(wk["past"]), 4)
        self.assertEqual(wk["past"][0]["id"], "2026-W51")
        self.assertNotIn("오프앵글", json.dumps(wk, ensure_ascii=False))

    def test_refresh_overwrites_and_missing_is_removed(self):
        data, _ = self.run_once(world())
        later = NOW + dt.timedelta(days=8)
        f = world()
        f.videos[vid(10)]["t"] = "바인드 오프앵글 모음 (제목 바뀜)"
        del f.videos[vid(2)]   # 지워진 영상
        data2, _ = self.run_once(f, prev=data, now=later)
        it = next(x for x in data2["items"] if x["id"] == vid(10))
        self.assertEqual(it["t"], "바인드 오프앵글 모음 (제목 바뀜)")
        self.assertEqual(it["code"], next(x for x in data["items"] if x["id"] == vid(10))["code"])
        self.assertNotIn(vid(2), {x["id"] for x in data2["items"]})

    def test_expiry_without_key(self):
        data, _ = self.run_once(world())
        later = NOW + dt.timedelta(days=30)
        data2, week2 = self.run_once(FakeYT(), prev=data, now=later, key="")
        self.assertEqual(data2["items"], [])
        self.assertEqual(data2["status"]["error"], "nokey")
        self.assertEqual(data2["state"]["channels"], {}, "채널 정보도 29일 넘으면 지워요")

    def test_quota_error_keeps_previous(self):
        data, _ = self.run_once(world())
        f = world()
        f.fail["search"] = (403, "quotaExceeded", "The request cannot be completed because you have exceeded your quota.")
        data2, _ = self.run_once(f, prev=data, now=NOW + dt.timedelta(hours=6))
        self.assertEqual(data2["status"]["error"], "quota")
        self.assertEqual({x["id"] for x in data2["items"]}, {x["id"] for x in data["items"]})

    def test_bad_key_stops_everything(self):
        f = world()
        f.fail["channels"] = (400, "badRequest", "API key not valid. Please pass a valid API key.")
        data, _ = self.run_once(f)
        self.assertEqual(data["status"]["error"], "key")
        self.assertEqual(f.count("search"), 0)
        self.assertEqual(f.count("videos"), 0)
        self.assertEqual(data["items"], [])

    def test_real_config_is_consistent(self):
        with open(os.path.join(ROOT, "scripts", "radar", "tips_sources.json"), encoding="utf-8") as fh:
            cfg = json.load(fh)
        ids = [q["id"] for q in cfg["queries"]]
        self.assertEqual(len(ids), len(set(ids)))
        maps, agents = cfg["names"]["maps"], cfg["names"]["agents"]
        for q in cfg["queries"]:
            self.assertIn(q["lang"], ("ko", "en"))
            kind, *rest = q["to"].split(":")
            self.assertIn(kind, ("mg", "lane", "agent"))
            if kind == "mg":
                self.assertIn(rest[0], maps)
                self.assertIn(rest[1], ("tiprefs", "offrefs", "wallrefs"))
            elif kind == "lane":
                self.assertIn(rest[0], agents)
                self.assertIn(rest[1], maps)
            else:
                self.assertIn(rest[0], agents)
            if q.get("map"):
                self.assertIn(q["map"], maps)
            self.assertTrue("발로란트" in q["q"] or "valorant" in q["q"].lower(), q["q"])
        handles = [c["handle"] for c in cfg["channels"]]
        self.assertEqual(len(handles), len(set(handles)))
        for c in cfg["channels"]:
            self.assertTrue(c["handle"].startswith("@"))
            self.assertIn(c["lang"], ("ko", "en"))
        L = cfg["limits"]
        self.assertLessEqual(L["search_per_day"], 50)   # 프로젝트 몫 하루 100번의 절반 아래
        self.assertLessEqual(L["units_per_day"], 8000)
        self.assertLessEqual(L["expire_days"], 29)


if __name__ == "__main__":
    unittest.main()
