"""레퍼런스 링크 점검.

ref_candidates.json과 radar/references.json의 링크를 실제로 열어 보고
상태 코드·페이지 제목·(유튜브면) 최근 업로드 날짜를 refcheck.json에 적는다.
같은 링크는 7일 안에 다시 확인하지 않는다(지난 결과를 --prev로 넘김).
"""

import argparse
import datetime as dt
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import collect  # noqa: E402  (fetch·피드 파서·유튜브 핸들 해석 재사용)

RECHECK_DAYS = 7
BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


def check_url(url):
    req = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA, "Accept-Language": "ko,en;q=0.8"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read(400_000).decode("utf-8", "replace")
            m = re.search(r"<title[^>]*>(.*?)</title>", body, re.S | re.I)
            title = html.unescape(re.sub(r"\s+", " ", m.group(1))).strip()[:160] if m else ""
            return {"status": resp.status, "final": resp.geturl(), "title": title}
    except urllib.error.HTTPError as e:
        return {"status": e.code, "final": url, "title": ""}
    except Exception as e:  # DNS·타임아웃 등
        return {"status": 0, "final": url, "title": "", "error": f"{type(e).__name__}: {e}"[:160]}


def check_youtube(handle, yt_cache):
    out = {"handle": handle}
    try:
        cid = collect.resolve_youtube(handle, yt_cache)
        out["channel_id"] = cid
        out["url"] = "https://www.youtube.com/channel/" + cid
        entries = collect.parse_feed(collect.fetch("https://www.youtube.com/feeds/videos.xml?channel_id=" + cid))
        dates = sorted((collect.parse_date(e["date"]) for e in entries if e.get("date")), reverse=True)
        out["status"] = 200
        out["videos_in_feed"] = len(entries)
        if dates:
            out["latest_upload"] = dates[0].date().isoformat()
            out["latest_title"] = collect.clean_text(entries[0]["title"], 120)
    except Exception as e:
        out["status"] = 0
        out["error"] = f"{type(e).__name__}: {e}"[:160]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inputs", nargs="+", required=True, help="후보·레퍼런스 JSON 파일들")
    ap.add_argument("--prev", default="")
    ap.add_argument("--yt-cache", default="", help="data.json (유튜브 채널 ID 캐시)")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    prev = {}
    if args.prev and os.path.exists(args.prev):
        try:
            with open(args.prev, encoding="utf-8") as f:
                prev = json.load(f).get("results", {})
        except (OSError, json.JSONDecodeError):
            prev = {}
    yt_cache = {}
    if args.yt_cache and os.path.exists(args.yt_cache):
        try:
            with open(args.yt_cache, encoding="utf-8") as f:
                yt_cache = json.load(f).get("yt_ids", {}) or {}
        except (OSError, json.JSONDecodeError):
            yt_cache = {}

    targets = []
    for path in args.inputs:
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for r in data.get("items", []):
            if r.get("youtube"):
                targets.append(("yt:" + r["youtube"], r["youtube"]))
            elif r.get("url"):
                targets.append(("url:" + r["url"], r["url"]))

    now = collect.now_utc()
    results, checked = {}, 0
    last_host, seen = None, set()
    for key, value in targets:
        if key in seen:
            continue
        seen.add(key)
        old = prev.get(key)
        if old and old.get("checked") and (now - collect.parse_date(old["checked"])).days < RECHECK_DAYS:
            results[key] = old
            continue
        host = "youtube" if key.startswith("yt:") else urllib.parse.urlsplit(value).netloc
        if host == last_host:
            time.sleep(1.5)
        last_host = host
        res = check_youtube(value, yt_cache) if key.startswith("yt:") else check_url(value)
        res["checked"] = collect.iso(now)
        results[key] = res
        checked += 1

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({"generated_at": collect.iso(now), "results": results}, f, ensure_ascii=False, indent=1)
    ok = sum(1 for r in results.values() if 200 <= (r.get("status") or 0) < 400)
    print(f"레퍼런스 점검: {len(results)}개 중 {ok}개 열림 (이번에 새로 확인 {checked}개)")


if __name__ == "__main__":
    main()
