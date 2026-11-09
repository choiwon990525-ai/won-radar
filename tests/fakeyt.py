"""가짜 유튜브 데이터 API — urllib.request.urlopen 대신 넣는 opener."""
import io
import json
import urllib.error
import urllib.parse

KEY = "TESTKEY-not-a-real-key"


def cid(n):
    return "UC" + ("%022d" % n)


def vid(n):
    return "v%010d" % n


class FakeYT:
    def __init__(self):
        self.calls = []            # (method, params)
        self.channels = {}         # '@handle' → (channel id, title)
        self.uploads = {}          # uploads playlist id → [video ids]
        self.search_hits = {}      # 검색어 안 낱말 → [video ids]
        self.videos = {}           # video id → dict(t, ch, cid, p, dur, public, kids, live, d)
        self.fail = {}             # method → (code, reason, message)

    def __call__(self, req, timeout=0):
        url = req.full_url
        assert KEY not in url, "키가 주소에 들어가면 안 돼요"
        assert req.get_header("X-goog-api-key") == KEY, "키는 X-Goog-Api-Key 머리로"
        path, _, qs = url.partition("?")
        method = path.rsplit("/", 1)[-1]
        params = dict(urllib.parse.parse_qsl(qs))
        self.calls.append((method, params))
        if method in self.fail:
            code, reason, message = self.fail[method]
            body = json.dumps({"error": {"code": code, "message": message, "errors": [{"reason": reason}]}}).encode()
            raise urllib.error.HTTPError(url, code, message, {}, io.BytesIO(body))
        return _Resp(getattr(self, "_" + method)(params))

    def _channels(self, p):
        if "forHandle" in p:
            c = self.channels.get(p["forHandle"])
            if not c:
                return {"items": []}
            return {"items": [{"id": c[0], "snippet": {"title": c[1]},
                               "contentDetails": {"relatedPlaylists": {"uploads": "UU" + c[0][2:]}}}]}
        return {"items": []}

    def _playlistItems(self, p):
        ids = self.uploads.get(p["playlistId"], [])[: int(p.get("maxResults", 5))]
        return {"items": [{"contentDetails": {"videoId": v}} for v in ids]}

    def _search(self, p):
        out = []
        for word, ids in self.search_hits.items():
            if word in p["q"]:
                out.extend(ids)
        return {"items": [{"id": {"kind": "youtube#video", "videoId": v}} for v in out[: int(p.get("maxResults", 5))]]}

    def _videos(self, p):
        out = []
        for v in p["id"].split(","):
            d = self.videos.get(v)
            if not d:
                continue
            m, s = divmod(d.get("dur", 300), 60)
            out.append({"id": v,
                        "snippet": {"title": d["t"], "channelTitle": d.get("ch", "채널"), "channelId": d.get("cid", cid(1)),
                                    "publishedAt": d["p"], "description": d.get("d", ""), "liveBroadcastContent": d.get("live", "none"),
                                    "thumbnails": {"medium": {"url": "https://i.ytimg.com/vi/%s/mqdefault.jpg" % v}}},
                        "contentDetails": {"duration": "PT%dM%dS" % (m, s) if d.get("dur", 300) else "P0D"},
                        "status": {"privacyStatus": "public" if d.get("public", True) else "private", "madeForKids": d.get("kids", False)}})
        return {"items": out}

    def count(self, method):
        return sum(1 for m, _ in self.calls if m == method)


class _Resp:
    def __init__(self, data):
        self._b = json.dumps(data).encode()

    def read(self, n=-1):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False
