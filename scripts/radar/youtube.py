"""유튜브 데이터 API v3 — WON 레이더 전용 키(YOUTUBE_API_KEY)로만 부른다.

- 키는 X-Goog-Api-Key 머리로만 보낸다. 주소에 키가 없으니 오류 글·로그·data 파일에 키가 남지 않는다.
- 쓰는 것: channels.list(forHandle · id) · playlistItems.list · search.list · videos.list.
  조회수 같은 통계(statistics)는 받지 않는다(유튜브 정책: 통계는 30일 넘게 두지 않음 · 점수 만들지 않음).
- 할당량(2026-06 기준 공식 문서): search.list 는 하루 100번 따로 세는 몫(1번 = 1),
  나머지는 하루 10,000단위(부를 때마다 1 이상). 하루는 태평양 시각 자정에 바뀐다.
  레이더는 tips_sources.json 의 한도(검색·단위)를 넘기 전에 스스로 멈춘다.
- 유튜브 HTML 페이지(채널 페이지 · 검색 결과 · 채널 피드)는 열지 않는다(API 약관 · robots.txt).
"""

import datetime as dt
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

try:
    from zoneinfo import ZoneInfo
    PACIFIC = ZoneInfo("America/Los_Angeles")
except Exception:  # tzdata 가 없는 곳: 겨울 시각으로 어림
    PACIFIC = dt.timezone(dt.timedelta(hours=-8))

API = "https://www.googleapis.com/youtube/v3/"
UA = "WON-Radar/1.0 (+https://github.com/choiwon990525-ai/won-radar)"
VIDEO_ID = re.compile(r"^[\w-]{11}$")
CHANNEL_ID = re.compile(r"^UC[\w-]{22}$")


class ApiError(Exception):
    """kind: 'quota'(그날 한도) · 'key'(키·설정 문제) · 'http'(그 밖 HTTP) · 'net'(연결) · 'budget'(레이더가 정한 한도)."""

    def __init__(self, kind, status=0, reason=""):
        super().__init__(f"{kind}: HTTP {status} {reason}".strip())
        self.kind = kind
        self.status = status
        self.reason = reason


def pacific_day(now):
    return now.astimezone(PACIFIC).date().isoformat()


class Budget:
    """그날(태평양 날짜) 쓴 검색 수·단위를 센다. 이전 실행 값은 tips.json 의 usage 로 이어받는다."""

    def __init__(self, prev, now, search_day_max, units_day_max, search_run_max):
        day = pacific_day(now)
        prev = prev if isinstance(prev, dict) else {}
        same = prev.get("day") == day
        self.day = day
        self.search = int(prev.get("search", 0)) if same else 0
        self.units = int(prev.get("units", 0)) if same else 0
        self.search_day_max = search_day_max
        self.units_day_max = units_day_max
        self.search_run_max = search_run_max
        self.search_run = 0
        self.stopped = {}   # 몫 이름 → 까닭 ('quota' 등) — 그 실행에선 더 안 부름

    def can_search(self):
        return ("search" not in self.stopped and self.search < self.search_day_max
                and self.search_run < self.search_run_max)

    def can_spend(self, units=1):
        return "units" not in self.stopped and self.units + units <= self.units_day_max

    def charge(self, bucket, n=1):
        if bucket == "search":
            self.search += n
            self.search_run += n
        else:
            self.units += n

    def as_dict(self):
        return {"day": self.day, "search": self.search, "units": self.units,
                "search_max": self.search_day_max, "units_max": self.units_day_max}


def iso_duration(text):
    """'PT1H2M3S' · 'P1DT2H' → 초. 못 읽으면 0."""
    m = re.fullmatch(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", text or "")
    if not m:
        return 0
    d, h, mi, s = (int(x) if x else 0 for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + s


class YouTube:
    def __init__(self, key, budget, opener=None, sleep=time.sleep):
        self.key = key
        self.budget = budget
        self.opener = opener or urllib.request.urlopen   # 시험은 가짜 opener 를 넣는다
        self.sleep = sleep

    # ---------- 낮은 단계 ----------
    def _get(self, method, params, bucket="units"):
        if bucket == "search":
            if not self.budget.can_search():
                raise ApiError("budget", 0, "search")
        elif not self.budget.can_spend(1):
            raise ApiError("budget", 0, "units")
        url = API + method + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"X-Goog-Api-Key": self.key, "User-Agent": UA, "Accept": "application/json"})
        last = None
        for attempt in range(2):
            self.budget.charge(bucket)   # 잘못된 요청도 1 이상 든다
            try:
                with self.opener(req, timeout=20) as resp:
                    return json.loads(resp.read(4 * 1024 * 1024).decode("utf-8"))
            except urllib.error.HTTPError as e:
                reason, message = "", ""
                try:
                    err = json.loads(e.read(65536).decode("utf-8", "replace")).get("error", {})
                    message = str(err.get("message", ""))
                    reason = str(((err.get("errors") or [{}])[0]).get("reason", "") or err.get("status", ""))
                except Exception:
                    pass
                low = (reason + " " + message).lower()
                if "quota" in low or "dailylimit" in low or "ratelimit" in low:
                    self.budget.stopped[bucket] = "quota"   # 이 실행에선 그 몫을 더 안 부름
                    raise ApiError("quota", e.code, reason)
                if e.code in (400, 401, 403) and ("api key" in low or "keyinvalid" in low or "keyexpired" in low
                                                  or "api_key" in low or "accessnotconfigured" in low
                                                  or "has not been used" in low or "is disabled" in low):
                    self.budget.stopped["search"] = self.budget.stopped["units"] = "key"
                    raise ApiError("key", e.code, reason)
                if e.code >= 500 and attempt == 0 and self.budget.can_spend(1):
                    self.sleep(3)
                    continue
                raise ApiError("http", e.code, reason)
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last = e
                if attempt == 0:
                    self.sleep(3)
                    continue
        raise ApiError("net", 0, type(last).__name__ if last else "")

    # ---------- 메서드 ----------
    def channel_by_handle(self, handle):
        """'@핸들' → {id, title, uploads} (없으면 None) · 1단위."""
        data = self._get("channels", {"part": "snippet,contentDetails", "forHandle": handle})
        items = data.get("items") or []
        return self._channel(items[0]) if items else None

    def channels_by_id(self, ids):
        """UC… 여러 개(50개까지) → {id: {id, title, uploads}} · 1단위."""
        ids = [i for i in ids if CHANNEL_ID.match(i)][:50]
        if not ids:
            return {}
        data = self._get("channels", {"part": "snippet,contentDetails", "id": ",".join(ids), "maxResults": 50})
        out = {}
        for it in data.get("items") or []:
            ch = self._channel(it)
            if ch:
                out[ch["id"]] = ch
        return out

    @staticmethod
    def _channel(it):
        cid = it.get("id", "")
        uploads = (((it.get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads")) or ""
        if not CHANNEL_ID.match(cid) or not uploads:
            return None
        return {"id": cid, "title": str((it.get("snippet") or {}).get("title", ""))[:120], "uploads": uploads}

    def uploads(self, playlist_id, max_results=10):
        """채널 업로드 목록 첫 쪽 → [영상 id] (새것부터) · 1단위."""
        data = self._get("playlistItems", {"part": "contentDetails", "playlistId": playlist_id,
                                           "maxResults": max(1, min(50, max_results))})
        out = []
        for it in data.get("items") or []:
            vid = ((it.get("contentDetails") or {}).get("videoId")) or ""
            if VIDEO_ID.match(vid):
                out.append(vid)
        return out

    def search(self, q, lang, region, published_after, max_results=15):
        """검색 첫 쪽만 → [영상 id] · 검색 몫 1번."""
        params = {"part": "snippet", "type": "video", "q": q, "maxResults": max(1, min(50, max_results)),
                  "order": "relevance", "relevanceLanguage": lang, "publishedAfter": published_after}
        if region:
            params["regionCode"] = region
        data = self._get("search", params, bucket="search")
        out = []
        for it in data.get("items") or []:
            vid = ((it.get("id") or {}).get("videoId")) or ""
            if VIDEO_ID.match(vid):
                out.append(vid)
        return out

    def videos(self, ids):
        """영상 50개까지 → {id: 정보} · 1단위. 없는(지워진·비공개) 영상은 결과에 안 나온다."""
        ids = [i for i in ids if VIDEO_ID.match(i)][:50]
        if not ids:
            return {}
        data = self._get("videos", {"part": "snippet,contentDetails,status", "id": ",".join(ids), "maxResults": 50})
        out = {}
        for it in data.get("items") or []:
            vid = it.get("id", "")
            if not VIDEO_ID.match(vid):
                continue
            sn = it.get("snippet") or {}
            st = it.get("status") or {}
            thumbs = sn.get("thumbnails") or {}
            img = ((thumbs.get("medium") or thumbs.get("high") or thumbs.get("default") or {}).get("url")) or ""
            out[vid] = {
                "t": str(sn.get("title", ""))[:200],
                "ch": str(sn.get("channelTitle", ""))[:120],
                "cid": str(sn.get("channelId", "")),
                "p": str(sn.get("publishedAt", "")),
                "d": str(sn.get("description", ""))[:400],   # 거르기에만 쓰고 저장하지 않음
                "dur": iso_duration((it.get("contentDetails") or {}).get("duration", "")),
                "img": img if img.startswith("https://") else "",
                "live": str(sn.get("liveBroadcastContent", "none")),
                "public": st.get("privacyStatus") == "public",
                "kids": bool(st.get("madeForKids")),
            }
        return out
