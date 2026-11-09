"""레퍼런스 링크 점검.

ref_candidates.json과 radar/references.json의 링크를 열어 보고 상태 코드·페이지 제목을 refcheck.json에 적는다.
같은 링크는 7일 안에 다시 확인하지 않는다(지난 결과를 --prev로 넘김).

- robots.txt 를 먼저 보고, 자동 접근을 막은 주소는 열지 않는다 → status 403 · robots true
  (두 레이더 화면 모두 '자동 확인 불가'로 보임 — 예전 결과 꼴 그대로).
- 레이더 이름을 밝힌 User-Agent 를 쓴다(브라우저인 척하지 않음).
- 유튜브(youtube.com · youtu.be · yt:@핸들)는 점검하지 않는다. 레이더가 유튜브 API 클라이언트라
  API 밖(페이지 · 피드)으로 유튜브 정보를 받지 않는다. 유튜브 레퍼런스엔 점검 표시가 안 붙는다.
"""

import argparse
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import collect  # noqa: E402  (날짜 함수 재사용)

RECHECK_DAYS = 7
UA = "WON-Radar/1.0 (+https://github.com/choiwon990525-ai/won-radar)"
YOUTUBE_HOSTS = re.compile(r"(^|\.)(youtube\.com|youtu\.be|youtube-nocookie\.com)$", re.I)


def is_youtube(url):
    host = urllib.parse.urlsplit(url).hostname or ""
    return bool(YOUTUBE_HOSTS.search(host))


class Robots:
    """호스트마다 robots.txt 를 한 번만 읽는다. 읽다가 401·403 이면 그 사이트 전체를 막은 것으로 본다(파이썬 표준 동작)."""

    def __init__(self, opener=None):
        self.cache = {}
        self.opener = opener or urllib.request.urlopen

    def allowed(self, url):
        parts = urllib.parse.urlsplit(url)
        base = f"{parts.scheme}://{parts.netloc}"
        rp = self.cache.get(base)
        if rp is None:
            rp = urllib.robotparser.RobotFileParser()
            try:
                req = urllib.request.Request(base + "/robots.txt", headers={"User-Agent": UA})
                with self.opener(req, timeout=15) as resp:
                    rp.parse(resp.read(500_000).decode("utf-8", "replace").splitlines())
            except urllib.error.HTTPError as e:
                if e.code in (401, 403):
                    rp.disallow_all = True
                else:
                    rp.allow_all = True   # robots.txt 없음(404 등) = 막지 않음
            except Exception:
                rp.allow_all = True
            self.cache[base] = rp
        return rp.can_fetch(UA, url)


def check_url(url, robots, opener=None):
    if not robots.allowed(url):
        return {"status": 403, "robots": True, "final": url, "title": "", "error": "robots.txt 가 자동 점검을 막아요"}
    opener = opener or urllib.request.urlopen
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ko,en;q=0.8"})
    try:
        with opener(req, timeout=20) as resp:
            body = resp.read(400_000).decode("utf-8", "replace")
            m = re.search(r"<title[^>]*>(.*?)</title>", body, re.S | re.I)
            title = html.unescape(re.sub(r"\s+", " ", m.group(1))).strip()[:160] if m else ""
            return {"status": resp.status, "final": resp.geturl(), "title": title}
    except urllib.error.HTTPError as e:
        return {"status": e.code, "final": url, "title": ""}
    except Exception as e:  # DNS·타임아웃 등
        return {"status": 0, "final": url, "title": "", "error": f"{type(e).__name__}: {e}"[:160]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inputs", nargs="+", required=True, help="후보·레퍼런스 JSON 파일들")
    ap.add_argument("--prev", default="")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    prev = {}
    if args.prev and os.path.exists(args.prev):
        try:
            with open(args.prev, encoding="utf-8") as f:
                prev = json.load(f).get("results", {})
        except (OSError, json.JSONDecodeError):
            prev = {}

    targets = []
    for path in args.inputs:
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for r in data.get("items", []):
            url = r.get("url") or ""
            if r.get("youtube") or not url or is_youtube(url):
                continue   # 유튜브는 점검하지 않음 (위 설명)
            targets.append(("url:" + url, url))

    now = collect.now_utc()
    robots = Robots()
    results, checked = {}, 0
    last_host, seen = None, set()
    for key, url in targets:
        if key in seen:
            continue
        seen.add(key)
        old = prev.get(key)
        if old and old.get("checked") and (now - collect.parse_date(old["checked"])).days < RECHECK_DAYS \
                and old.get("by") == "v2":   # 예전 점검기(브라우저 UA · robots 안 봄) 결과는 한 번 새로 봄
            results[key] = old
            continue
        host = urllib.parse.urlsplit(url).netloc
        if host == last_host:
            time.sleep(1.5)
        last_host = host
        res = check_url(url, robots)
        res["checked"] = collect.iso(now)
        res["by"] = "v2"
        results[key] = res
        checked += 1

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({"generated_at": collect.iso(now), "results": results}, f, ensure_ascii=False, indent=1)
    ok = sum(1 for r in results.values() if 200 <= (r.get("status") or 0) < 400)
    blocked = sum(1 for r in results.values() if r.get("robots"))
    print(f"레퍼런스 점검: {len(results)}개 중 {ok}개 열림 · robots 로 안 연 것 {blocked}개 (이번에 새로 확인 {checked}개)")


if __name__ == "__main__":
    main()
