"""WON 레이더 꿀팁 수집 (유튜브 공식 API).

tips_sources.json 의 채널 새 영상과, 돌려 가며 쓰는 검색어로 찾은 발로란트 꿀팁 영상을 모아 tips.json 을 만든다.

- 영상 종류를 짐작해서 붙이지 않는다(유튜브 API 정책 — 콘텐츠 유형 추정 금지).
  붙는 건 '어디서 찾았나'(채널 · 검색어)와, 그 검색어에 코치가 정해 둔 '넣을 칸 제안'뿐이다.
- API 로 받은 정보는 7일마다 다시 받아 덮어쓰고, 29일 넘게 못 받은 영상은 지운다. 조회수 같은 통계는 받지 않는다.
- 매주(한국 시각 월요일에 바뀌는 주) '이번 주 후보'를 고르고 번호를 붙인다 (검색까지 한 실행에서 고르면 그 주 동안 그대로) →
  tips_week.json (번호 · 코드 · 영상 id · 넣을 칸 제안 · 찾은 곳만, 제목 없음 — Claude 는 oEmbed 로 제목을 다시 받는다).
- YOUTUBE_API_KEY 가 없으면 새로 모으지 않고, 지난 목록에서 기한이 지난 것만 지운다.
- 뺄 채널(RADAR_SKIP_CHANNELS: UC… 또는 @핸들, 쉼표로)·소속 낱말(RADAR_BLOCK_WORDS)은 GitHub Secret 으로만 받는다.
  tips.json 은 공개 파일이라, 뺄 채널의 id 와 걸러 낸 영상 id 는 그 실행 메모리에만 두고 파일에 쓰지 않는다.
- 지난 4주 후보(번호 · 코드 · 영상 id)도 남긴다 — 코치가 늦게 답해도 그 주 번호로 찾게.
"""

import argparse
import collections
import datetime as dt
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import youtube  # noqa: E402
from youtube import ApiError, VIDEO_ID  # noqa: E402

KST = dt.timezone(dt.timedelta(hours=9))
STOP_KINDS = ("quota", "key", "budget", "net")   # 이 오류면 그 실행에서 유튜브를 더 안 부른다


def iso(d):
    return d.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso(text):
    try:
        d = dt.datetime.fromisoformat(str(text).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)
    except (TypeError, ValueError):
        return None


def age_days(text, now):
    d = parse_iso(text)
    return (now - d).total_seconds() / 86400 if d else 10 ** 6


def week_of(now):
    """한국 시각 기준 ISO 주 → ('2026-W46', 그 주 월요일 'YYYY-MM-DD')."""
    k = now.astimezone(KST)
    y, w, _ = k.isocalendar()
    monday = (k - dt.timedelta(days=k.weekday())).date().isoformat()
    return f"{y}-W{w:02d}", monday


def split_secret(text):
    return [w.strip() for w in re.split(r"[,\n]", text or "") if w.strip()]


def load_json(path, default):
    if not path or not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, type(default)) else default
    except (OSError, json.JSONDecodeError):
        return default


class Order:
    """설정 차례: 검색어가 위에 있을수록 먼저(매뉴얼북 빈 곳 순서), 그다음 채널 차례."""

    def __init__(self, cfg):
        self.q = {q["id"]: i for i, q in enumerate(cfg["queries"])}
        self.qd = {q["id"]: q for q in cfg["queries"]}
        self.ch = {c["handle"]: i for i, c in enumerate(cfg["channels"])}
        self.chd = {c["handle"]: c for c in cfg["channels"]}

    def via_key(self, v):
        if v.startswith("q:"):
            return (0, self.q.get(v[2:], 10 ** 6))
        return (1, self.ch.get(v[3:], 10 ** 6))

    def first_query(self, it):
        qs = [v[2:] for v in it.get("via", []) if v.startswith("q:") and v[2:] in self.q]
        return min(qs, key=lambda x: self.q[x]) if qs else None


def derive(it, cfg, order, now):
    """찾은 곳에서 나오는 칸만 붙인다(내용을 보고 정한 게 아님): 언어 · 넣을 칸 제안 · 맵 · 예전 표시."""
    qid = order.first_query(it)
    q = order.qd.get(qid) if qid else None
    chs = [order.chd[v[3:]] for v in it.get("via", []) if v.startswith("ch:") and v[3:] in order.chd]
    it["lang"] = chs[0]["lang"] if chs else (q["lang"] if q else "")
    it["to"] = q["to"] if q else None
    it["map"] = q.get("map") if q else None
    old_cfg = cfg.get("old") or {}
    pub = parse_iso(it.get("p"))
    old = bool(pub) and (now - pub).days > old_cfg.get("days", 365)
    change = (old_cfg.get("map_changes") or {}).get(it["map"] or "")
    if pub and change and pub.date().isoformat() < change:
        old = True
    it["old"] = old


def past_weeks(prev_week, prev_past, wid, keep=4):
    """지난 주 후보 (번호 · 코드 · 영상 id · 넣을 칸 · 찾은 곳만) — 새 주로 넘어갈 때 앞에 하나 붙임."""
    out = [w for w in (prev_past or []) if isinstance(w, dict) and w.get("id") and w.get("id") != wid]
    if isinstance(prev_week, dict) and prev_week.get("id") and prev_week.get("id") != wid:
        slim = {"id": prev_week["id"], "start": prev_week.get("start", ""), "made": prev_week.get("made", ""),
                "items": [{k: e[k] for k in ("n", "code", "id", "to", "via") if k in e}
                          for e in prev_week.get("items") or [] if isinstance(e, dict)]}
        out = [slim] + [w for w in out if w["id"] != slim["id"]]
    return out[:keep]


def make_week(items, cfg, order, now, prev_week, can_seal=True):
    """그 주 후보를 고른다. 검색까지 한 실행(예약·손 실행 + 키 있음)에서 고른 목록은 그 주 동안 굳힌다(sealed).
    코드를 올릴 때 도는 push 실행(검색 안 함)이나 키 없이 고른 목록, 빈 목록은 다음 실행에서 다시 고른다."""
    wid, monday = week_of(now)
    if (isinstance(prev_week, dict) and prev_week.get("id") == wid
            and prev_week.get("sealed", True) and prev_week.get("items")):
        kept = []
        for e in prev_week.get("items") or []:
            e = dict(e)
            e.pop("gone", None)
            if e.get("id") not in items:   # 그 사이 지워졌거나 30일 규칙으로 빠진 영상 — 번호는 그대로 둠
                e["gone"] = True
            kept.append(e)
        return dict(prev_week, items=kept)
    for it in items.values():   # 굳히기 전 목록에 들었던 영상은 다시 후보로
        if it.get("o") == wid:
            it.pop("o", None)
    W = cfg["week"]
    long_sec = W.get("long_sec", 180)
    pool = [it for it in items.values() if not it.get("o")]   # 한 번 보낸 영상은 다시 안 보냄
    picked, per_q, per_ch = [], collections.Counter(), collections.Counter()

    def ts(it):
        d = parse_iso(it.get("p"))
        return d.timestamp() if d else 0

    hits = [it for it in pool if order.first_query(it)]
    hits.sort(key=lambda it: (order.q[order.first_query(it)], bool(it.get("old")),
                              0 if it.get("dur", 0) > long_sec else 1, -ts(it)))
    for it in hits:
        if len(picked) >= W.get("search_max", 12):
            break
        qid = order.first_query(it)
        if per_q[qid] >= W.get("per_query", 2) or per_ch[it.get("cid")] >= W.get("per_channel", 2):
            continue
        picked.append(it)
        per_q[qid] += 1
        per_ch[it.get("cid")] += 1

    since = now - dt.timedelta(days=W.get("channel_days", 14))
    chosen = {it["id"] for it in picked}

    def weekly_channel(it):
        return any(v.startswith("ch:") and order.chd.get(v[3:], {}).get("weekly") for v in it.get("via", []))

    fresh = [it for it in pool if it["id"] not in chosen and weekly_channel(it) and (parse_iso(it.get("p")) or since) > since]
    fresh.sort(key=lambda it: (bool(it.get("old")), 0 if it.get("dur", 0) > long_sec else 1,
                               min(order.via_key(v) for v in it.get("via", []) if v.startswith("ch:")), -ts(it)))
    for it in fresh:
        if len(picked) >= W.get("size", 15):
            break
        if per_ch[it.get("cid")] >= 1:   # 채널 새 영상은 채널마다 하나
            continue
        picked.append(it)
        per_ch[it.get("cid")] += 1

    entries = []
    for n, it in enumerate(picked, 1):
        it["o"] = wid
        via = sorted(it.get("via", []), key=order.via_key)
        entries.append({"n": n, "code": it["code"], "id": it["id"], "to": it.get("to"), "via": via[0] if via else ""})
    return {"id": wid, "start": monday, "made": iso(now), "sealed": bool(can_seal and entries), "items": entries}


def run(cfg, prev, now, key, event="schedule", skip_channels=(), block_words=(), opener=None, sleep=time.sleep):
    L = cfg["limits"]
    order = Order(cfg)
    state = prev.get("state") if isinstance(prev.get("state"), dict) else {}
    ch_state = {h: v for h, v in (state.get("channels") or {}).items()
                if isinstance(v, dict) and age_days(v.get("r"), now) <= L["expire_days"]}
    skip_handles = {}  # 뺄 @핸들 → 채널 id — 실행마다 다시 찾고 파일에 안 씀 (걸러 낸 영상도 안 적음 — 위 설명)
    next_code = int(state.get("next_code", 1) or 1)
    qi = int(state.get("qi", 0) or 0)
    items = {}
    for it in prev.get("items") or []:
        if isinstance(it, dict) and VIDEO_ID.match(str(it.get("id", ""))) and it.get("code"):
            items[it["id"]] = it
    budget = youtube.Budget(prev.get("usage"), now, L["search_per_day"], L["units_per_day"], L["search_per_run"])
    status = {"key": bool(key), "error": ""}
    rows = []
    exclude = re.compile(cfg.get("exclude_title") or r"(?!x)x", re.I)
    blocks = [w.lower() for w in block_words if w]
    skip_set = {s for s in skip_channels if s.startswith("UC")}
    found = collections.defaultdict(set)

    def stop(e):
        status["error"] = status["error"] or e.kind

    if key:
        yt = youtube.YouTube(key, budget, opener=opener, sleep=sleep)
        try:
            # 1) 채널 핸들 → 채널 id · 업로드 목록 (7일마다 다시 받음) · 뺄 채널 @핸들은 실행마다
            missed = 0
            for h in [s for s in skip_channels if s.startswith("@")]:
                try:
                    info = yt.channel_by_handle(h)
                except ApiError as e:
                    if e.kind in STOP_KINDS:
                        raise
                    info = None
                if info:
                    skip_handles[h] = info["id"]
                else:
                    missed += 1
            if missed:   # 어느 핸들인지는 적지 않음 (공개 파일)
                rows.append({"id": "skip", "name": "뺄 채널 목록", "ok": False, "count": 0,
                             "error": f"뺄 채널 {missed}개를 못 찾았어요 — Secret RADAR_SKIP_CHANNELS 확인"})
            for ch in cfg["channels"]:
                h = ch["handle"]
                st = ch_state.get(h)
                if st and age_days(st.get("r"), now) < L["refresh_days"]:
                    continue
                try:
                    info = yt.channel_by_handle(h)
                except ApiError as e:
                    if e.kind in STOP_KINDS:
                        raise
                    info = None
                if info:
                    ch_state[h] = dict(info, r=iso(now), seen=bool(st and st.get("seen")))
                elif not st:
                    rows.append({"id": "ch:" + h, "name": ch["name"], "ok": False, "count": 0, "error": "채널을 못 찾음"})
            # 2) 채널 새 영상
            for ch in cfg["channels"]:
                h = ch["handle"]
                st = ch_state.get(h)
                if not st:
                    continue
                n = L["channel_new_per_run"] if st.get("seen") else L["channel_first"]
                try:
                    ids = yt.uploads(st["uploads"], n)
                except ApiError as e:
                    if e.kind in STOP_KINDS:
                        raise
                    rows.append({"id": "ch:" + h, "name": ch["name"], "ok": False, "count": 0, "error": str(e)})
                    continue
                st["seen"] = True
                for v in ids:
                    found[v].add("ch:" + h)
                rows.append({"id": "ch:" + h, "name": ch["name"], "ok": True, "count": len(ids)})
            # 3) 검색 — 예약·손 실행에서만 (코드를 올릴 때 도는 push 실행은 검색 안 함)
            queries = cfg["queries"]
            if event != "push" and queries:
                tried = 0
                while budget.can_search() and tried < len(queries):
                    q = queries[qi % len(queries)]
                    tried += 1
                    after = iso(now - dt.timedelta(days=L["search_days"]))
                    ids = yt.search(q["q"], q["lang"], "KR" if q["lang"] == "ko" else "", after, L["search_results"])
                    qi = (qi + 1) % len(queries)
                    for v in ids:
                        found[v].add("q:" + q["id"])
                    rows.append({"id": "q:" + q["id"], "name": q["label"], "ok": True, "count": len(ids)})
        except ApiError as e:
            if e.kind != "budget":
                stop(e)

        # 4) 상세: 새 영상 + 다시 받을 때가 된 영상 (50개씩)
        if status["error"] not in ("quota", "key"):
            new_ids = [v for v in found if v not in items]
            due = sorted((it for it in items.values() if age_days(it.get("r"), now) >= L["refresh_days"]),
                         key=lambda it: it.get("r", ""))
            want = new_ids + [it["id"] for it in due]
            asked, got, calls = set(), {}, 0
            for i in range(0, len(want), 50):
                if calls >= L["videos_calls_per_run"]:
                    break
                batch = want[i:i + 50]
                try:
                    res = yt.videos(batch)
                except ApiError as e:
                    if e.kind in STOP_KINDS:
                        if e.kind != "budget":
                            stop(e)
                        break
                    continue
                calls += 1
                asked.update(batch)
                got.update(res)
            skip_all = skip_set | set(skip_handles.values())
            for vid in asked:
                info = got.get(vid)
                usable = bool(info) and info["public"] and not info["kids"] and info["live"] == "none" and info["dur"] > 0
                if usable:
                    hay = (info["t"] + " " + info["ch"] + " " + info["d"]).lower()
                    if info["cid"] in skip_all or exclude.search(info["t"]) or any(w in hay for w in blocks):
                        usable = False
                if not usable:
                    items.pop(vid, None)
                    continue
                it = items.get(vid)
                if it is None:
                    it = {"id": vid, "code": f"T{next_code:04d}", "f": iso(now), "via": []}
                    next_code += 1
                it.update({"u": "https://www.youtube.com/watch?v=" + vid, "t": info["t"], "ch": info["ch"],
                           "cid": info["cid"], "p": info["p"], "dur": info["dur"], "img": info["img"], "r": iso(now)})
                items[vid] = it
        for vid, vias in found.items():
            if vid in items:
                items[vid]["via"] = sorted(set(items[vid].get("via") or []) | vias, key=order.via_key)[:4]
    else:
        status["error"] = "nokey"

    # 5) 기한 — 키가 없어도: 29일 넘게 다시 받지 못한 영상은 지운다 (유튜브 API 30일 규칙)
    items = {k: v for k, v in items.items() if age_days(v.get("r"), now) <= L["expire_days"] and v.get("via")}
    for it in items.values():
        derive(it, cfg, order, now)

    # 6) 개수 한도 — 이번 주 후보에 든 영상은 남기고, 나머지는 최근 영상부터
    wid, _ = week_of(now)
    if len(items) > L["max_items"]:
        keep = sorted(items.values(), key=lambda it: (it.get("o") != wid, -(parse_iso(it.get("p")) or now).timestamp()))
        items = {it["id"]: it for it in keep[:L["max_items"]]}

    week = make_week(items, cfg, order, now, prev.get("week"), can_seal=bool(key) and event != "push")
    past = past_weeks(prev.get("week"), state.get("past"), week["id"])
    ordered = sorted(items.values(), key=lambda it: it.get("p", ""), reverse=True)
    data = {
        "v": 1,
        "generated_at": iso(now),
        "next_hours": 8,
        "status": status,
        "usage": budget.as_dict(),
        "state": {"next_code": next_code, "qi": qi, "channels": ch_state, "past": past},
        "sources": rows,
        "channels": [{"handle": c["handle"], "name": c["name"], "kind": c["kind"], "lang": c["lang"]} for c in cfg["channels"]],
        "queries": [{"id": q["id"], "label": q["label"], "lang": q["lang"], "to": q["to"]} for q in cfg["queries"]],
        "names": cfg.get("names", {}),
        "week": week,
        "items": ordered,
    }
    week_file = {"v": 1, "week": week["id"], "start": week["start"], "made": week["made"], "sealed": week["sealed"],
                 "items": week["items"], "past": past}
    return data, week_file


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--prev", default="")
    ap.add_argument("--out", required=True)
    ap.add_argument("--week-out", required=True)
    ap.add_argument("--event", default="schedule", help="github.event_name (push 면 검색 안 함)")
    args = ap.parse_args()
    cfg = load_json(args.config, {})
    prev = load_json(args.prev, {})
    now = dt.datetime.now(dt.timezone.utc)
    data, week = run(cfg, prev, now, os.environ.get("YOUTUBE_API_KEY", "").strip(), args.event,
                     split_secret(os.environ.get("RADAR_SKIP_CHANNELS")), split_secret(os.environ.get("RADAR_BLOCK_WORDS")))
    for path, obj in ((args.out, data), (args.week_out, week)):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    u = data["usage"]
    note = {"": "", "nokey": " · 키 없음(새로 안 모음)", "quota": " · 오늘 한도 참", "key": " · 키 확인 필요", "net": " · 연결 오류"}
    print(f"꿀팁 {len(data['items'])}개 · 이번 주 {data['week']['id']} {len(week['items'])}개 · "
          f"검색 {u['search']}/{u['search_max']} · 단위 {u['units']}/{u['units_max']}{note.get(data['status']['error'], '')}")


if __name__ == "__main__":
    main()
