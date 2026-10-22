"""WON 레이더 수집기.

sources.json에 적힌 RSS·Atom 피드를 읽어 발로란트·AI 소식을 하나의 data.json으로 모은다.
GitHub Actions가 3시간마다 실행하고, 결과는 gh-pages 브랜치로 배포된다.
표준 라이브러리만 쓴다. ANTHROPIC_API_KEY가 있으면 하루 한 번 한국어 브리핑을 만든다(선택).
"""

import argparse
import concurrent.futures
import datetime as dt
import email.utils
import hashlib
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

KST = dt.timezone(dt.timedelta(hours=9))
UA = "Mozilla/5.0 (compatible; WON-Radar/1.0; +https://github.com/choiwon990525-ai/won-radar)"
MAX_BYTES = 6 * 1024 * 1024

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "media": "http://search.yahoo.com/mrss/",
    "content": "http://purl.org/rss/1.0/modules/content/",
    "dc": "http://purl.org/dc/elements/1.1/",
    "rss1": "http://purl.org/rss/1.0/",
    "yt": "http://www.youtube.com/xml/schemas/2015",
}

# 제목·요약에 걸리면 붙는 태그. 앱의 필터 칩이 이 이름을 그대로 쓴다.
TAG_RULES = {
    "valorant": [
        ("패치", r"patch|패치|업데이트|hotfix|핫픽스|밸런스|balance"),
        ("대회", r"\bvct\b|champions|masters|챔피언스|마스터스|퍼시픽|pacific|\bemea\b|americas|kickoff|킥오프|playoff|플레이오프|결승|grand final|\bewc\b|challengers|챌린저스|game changers|스테이지|stage [12]"),
        ("코칭", r"coach|코치|코칭|강의|\btips?\b|guide|가이드|how to|꿀팁|공략|tutorial|튜토리얼|improve|rank ?up|랭크업|vod review|피드백|훈련|연습법"),
        ("라인업", r"line-?ups?|라인업|원웨이|one[- ]?way|setups?\b|셋업"),
        ("메타", r"\bmeta\b|메타|tier ?list|티어 ?리스트|pick ?rate|픽률|\bcomps?\b|조합|nerf|buff|너프|버프"),
        ("이적", r"roster|\bsigns?\b|signing|이적|영입|로스터|bench|방출|release[sd]? "),
    ],
    "ai": [
        ("Claude", r"claude|anthropic|클로드|앤트로픽"),
        ("OpenAI", r"openai|chatgpt|gpt-?\d|오픈ai|챗gpt|챗지피티"),
        ("Google", r"gemini|deepmind|제미나이|제미니"),
        ("에이전트", r"agent|에이전트|agentic|\bmcp\b|자동화|automation"),
        ("코딩", r"coding|\bcode\b|코딩|개발자|developer|cursor|copilot|코파일럿"),
        ("AI×게임", r"\bgames?\b|gaming|esports|e-?sports|게임|e스포츠|이스포츠|valorant|발로란트|riot|라이엇"),
        ("교육", r"education|교육|tutor|강의|학생|teacher|교사|coaching|코칭|학습법"),
    ],
}
SPAM = re.compile(r"파워볼|토토|카지노|바카라|먹튀|슬롯사이트|배당선|casino|betting odds", re.I)
PICK_TAGS = {"valorant": {"코칭", "메타", "라인업", "패치"}, "ai": {"Claude", "AI×게임", "교육", "에이전트"}}


def now_utc():
    return dt.datetime.now(dt.timezone.utc)


def iso(d):
    return d.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_date(text, naive_tz=dt.timezone.utc):
    """시간대가 없는 날짜는 naive_tz로 본다 (예: 국내 언론 RSS는 KST)."""
    if not text:
        return None
    text = text.strip()
    try:
        d = email.utils.parsedate_to_datetime(text)
        if d is not None:
            return d if d.tzinfo else d.replace(tzinfo=naive_tz)
    except (TypeError, ValueError, IndexError):
        pass
    try:
        d = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=naive_tz)
    except ValueError:
        return None


def clean_text(raw, limit=220):
    if not raw:
        return ""
    text = html.unescape(html.unescape(raw))
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def safe_url(url):
    url = (url or "").strip()
    return url if re.match(r"^https?://", url, re.I) else ""


def fetch(url, retries=1):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*"})
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            return resp.read(MAX_BYTES)
    except urllib.error.HTTPError as e:
        if e.code in (429, 503) and retries > 0:  # 요청 한도: 잠시 쉬고 한 번만 다시
            time.sleep(8)
            return fetch(url, retries - 1)
        raise


def resolve_youtube(handle, cache):
    """'@핸들'을 채널 ID(UC…)로 바꾼다. 한 번 찾은 ID는 data.json에 저장해 다시 찾지 않는다."""
    if handle in cache:
        return cache[handle]
    page = fetch("https://www.youtube.com/" + urllib.parse.quote(handle)).decode("utf-8", "replace")
    m = (re.search(r'<link rel="canonical" href="https://www\.youtube\.com/channel/(UC[\w-]{22})"', page)
         or re.search(r'"externalId":"(UC[\w-]{22})"', page)
         or re.search(r'"channelId":"(UC[\w-]{22})"', page))
    if not m:
        raise ValueError("채널 ID를 찾지 못함: " + handle)
    cache[handle] = m.group(1)
    return cache[handle]


def first(el, paths):
    for p in paths:
        found = el.find(p, NS)
        if found is not None and (found.text or "").strip():
            return found.text
    return ""


def parse_feed(body):
    """RSS 2.0 · RSS 1.0(RDF) · Atom을 같은 모양의 dict 목록으로 바꾼다."""
    root = ET.fromstring(body)
    entries = []
    tag = root.tag.lower()
    if tag.endswith("feed"):  # Atom
        for e in root.findall("atom:entry", NS):
            link = ""
            for l in e.findall("atom:link", NS):
                if l.get("rel", "alternate") == "alternate" and l.get("href"):
                    link = l.get("href")
                    break
            img = ""
            thumb = e.find("media:group/media:thumbnail", NS)
            if thumb is not None:
                img = thumb.get("url", "")
            cat = e.find("atom:category", NS)
            entries.append({
                "title": first(e, ["atom:title"]),
                "link": link,
                "date": first(e, ["atom:published", "atom:updated"]),
                "summary": first(e, ["atom:summary", "media:group/media:description", "atom:content"]),
                "img": img,
                "publisher": "",
                "category": (cat.get("label") or cat.get("term") or "") if cat is not None else "",
            })
        return entries
    items = root.findall("channel/item") or root.findall("rss1:item", NS) or root.findall("item")
    for it in items:
        img = ""
        for p in ("media:thumbnail", "media:content"):
            m = it.find(p, NS)
            if m is not None and m.get("url"):
                img = m.get("url")
                break
        if not img:
            enc = it.find("enclosure")
            if enc is not None and (enc.get("type") or "").startswith("image"):
                img = enc.get("url", "")
        src = it.find("source")
        entries.append({
            "title": first(it, ["title", "rss1:title"]),
            "link": first(it, ["link", "rss1:link"]),
            "date": first(it, ["pubDate", "dc:date"]),
            "summary": first(it, ["description", "rss1:description", "content:encoded"]),
            "img": img,
            "publisher": (src.text or "").strip() if src is not None and src.text else "",
        })
    return entries


def tags_for(cat, text):
    low = text.lower()
    return [name for name, pat in TAG_RULES.get(cat, []) if re.search(pat, low)]


def title_key(title):
    t = re.sub(r"[\W_]+", "", title.lower())
    return t[:48]


def collect_source(src, fetched_at, yt_cache):
    if src.get("youtube"):
        src["url"] = "https://www.youtube.com/feeds/videos.xml?channel_id=" + resolve_youtube(src["youtube"], yt_cache)
    body = fetch(src["url"])
    entries = parse_feed(body)
    include = re.compile(src["include"]) if src.get("include") else None
    exclude = re.compile(src["exclude"]) if src.get("exclude") else None
    naive_tz = KST if src.get("tz") == "KST" else dt.timezone.utc
    out = []
    for e in entries:
        title = clean_text(e["title"], 200)
        link = safe_url(e["link"])
        if not title or not link:
            continue
        publisher = e.get("publisher") or ""
        if src.get("publisher_from_category") and e.get("category"):
            publisher = e["category"] if e["category"].startswith("r/") else "r/" + e["category"]
        if publisher and src["url"].startswith("https://news.google.com/"):
            suffix = " - " + publisher
            if title.endswith(suffix):
                title = title[: -len(suffix)].rstrip()
        summary = clean_text(e["summary"])
        if src["url"].startswith("https://news.google.com/"):
            summary = ""  # 구글 뉴스 요약은 제목 반복이라 버린다
        if include and not include.search(title + " " + summary):
            continue
        if SPAM.search(title) or (exclude and exclude.search(title + " " + summary)):
            continue
        published = parse_date(e["date"], naive_tz) or fetched_at
        if published > fetched_at + dt.timedelta(minutes=10):  # 미래 날짜는 시간대 오류로 보고 수집 시각으로
            published = fetched_at
        out.append({
            "id": hashlib.sha1(link.encode("utf-8")).hexdigest()[:16],
            "t": title,
            "u": link,
            "s": publisher or src["name"],
            "sid": src["id"],
            "c": src["cat"],
            "g": src["group"],
            "p": iso(published),
            "f": iso(fetched_at),
            "d": summary,
            "img": safe_url(e.get("img")),
            "tags": tags_for(src["cat"], title + " " + summary),
        })
        if len(out) >= src.get("max", 20):
            break
    return out


def merge(fresh, previous, keep_days, max_items, source_ids, fetched_at):
    cutoff = fetched_at - dt.timedelta(days=keep_days)
    by_id = {}
    for it in previous:
        if it.get("sid") in source_ids:
            by_id[it["id"]] = it
    for it in fresh:
        old = by_id.get(it["id"])
        if old:
            it["f"] = old.get("f", it["f"])  # 처음 본 시각은 유지 → 앱의 '새 글' 판단
        by_id[it["id"]] = it
    items = [it for it in by_id.values() if (parse_date(it["p"]) or fetched_at) >= cutoff]
    items.sort(key=lambda it: it["p"], reverse=True)
    seen, deduped = set(), []
    for it in items:
        key = (it["c"], title_key(it["t"]))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(it)
    return deduped[:max_items]


def make_briefing(items, previous_briefing, fetched_at):
    """ANTHROPIC_API_KEY가 있으면 KST 날짜당 한 번, 최근 소식으로 한국어 브리핑을 만든다."""
    today = fetched_at.astimezone(KST).date().isoformat()
    if previous_briefing and previous_briefing.get("date") == today:
        return previous_briefing
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return previous_briefing
    try:
        import anthropic
    except ImportError:
        print("anthropic 패키지가 없어 브리핑을 건너뜀", file=sys.stderr)
        return previous_briefing

    since = fetched_at - dt.timedelta(hours=36)
    recent = [it for it in items if (parse_date(it["p"]) or fetched_at) >= since]
    lines = []
    for cat in ("valorant", "ai"):
        for it in [x for x in recent if x["c"] == cat][:40]:
            tag = ",".join(it["tags"])
            lines.append(f"[{cat}] {it['t']} ({it['s']}{' · ' + tag if tag else ''})")
    if not lines:
        return previous_briefing

    prompt = (
        "아래는 지난 36시간 동안 모은 발로란트·AI 소식 제목 목록입니다. 읽는 사람은 "
        "프로 지망 청소년에게 발로란트를 가르치는 코치이고, AI 도구(특히 Claude)로 수업 준비를 자동화하는 데 관심이 많습니다.\n"
        "제목에 있는 사실만 쓰고 추측은 하지 마세요. 각 줄은 한국어 한 문장, 60자 이내로 쓰세요.\n"
        "- valorant: 코치가 알아야 할 발로란트 소식 3개 (패치·대회·메타·코칭 우선)\n"
        "- ai: 알아야 할 AI 소식 3개 (Claude·에이전트·교육·게임 관련 우선)\n"
        "- idea: 오늘 소식에서 수업이나 업무에 바로 써 볼 만한 것 한 문장\n\n" + "\n".join(lines)
    )
    schema = {
        "type": "object",
        "properties": {
            "valorant": {"type": "array", "items": {"type": "string"}},
            "ai": {"type": "array", "items": {"type": "string"}},
            "idea": {"type": "string"},
        },
        "required": ["valorant", "ai", "idea"],
        "additionalProperties": False,
    }
    client = anthropic.Anthropic()
    try:
        response = client.beta.messages.create(
            model="claude-opus-5-5",
            max_tokens=4000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.RateLimitError:
        print("브리핑: 요청 한도 초과, 다음 실행에서 다시 시도", file=sys.stderr)
        return previous_briefing
    except anthropic.APIStatusError as e:
        print(f"브리핑: API 오류 {e.status_code} {e.message}", file=sys.stderr)
        return previous_briefing
    except anthropic.APIConnectionError:
        print("브리핑: 네트워크 오류", file=sys.stderr)
        return previous_briefing
    if response.stop_reason != "end_turn":
        print(f"브리핑: stop_reason={response.stop_reason}, 건너뜀", file=sys.stderr)
        return previous_briefing
    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return previous_briefing
    data["date"] = today
    data["at"] = iso(fetched_at)
    return data


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sources", required=True)
    ap.add_argument("--prev", default="")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    with open(args.sources, encoding="utf-8") as f:
        config = json.load(f)
    previous = {}
    if args.prev and os.path.exists(args.prev):
        try:
            with open(args.prev, encoding="utf-8") as f:
                previous = json.load(f)
        except (OSError, json.JSONDecodeError):
            previous = {}

    fetched_at = now_utc()
    sources = config["sources"]
    yt_cache = dict(previous.get("yt_ids") or {})

    def run_host(group):
        # 같은 사이트는 한 줄로 천천히: 레딧처럼 동시 요청을 막는 곳 대비
        results = []
        for i, s in enumerate(group):
            if i:
                time.sleep(2)
            try:
                results.append((s, collect_source(s, fetched_at, yt_cache), None))
            except Exception as e:  # 한 소스가 죽어도 나머지는 계속
                results.append((s, [], f"{type(e).__name__}: {e}"[:160]))
        return results

    by_host = {}
    for s in sources:
        host = "www.youtube.com" if s.get("youtube") else urllib.parse.urlsplit(s["url"]).netloc
        by_host.setdefault(host, []).append(s)
    fresh, status = [], []
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        for results in pool.map(run_host, by_host.values()):
            for s, got, err in results:
                fresh.extend(got)
                row = {"id": s["id"], "name": s["name"], "cat": s["cat"], "ok": err is None, "count": len(got)}
                if err:
                    row["error"] = err
                    print(f"[실패] {s['id']}: {err}", file=sys.stderr)
                status.append(row)
    order = {s["id"]: i for i, s in enumerate(sources)}
    status.sort(key=lambda x: order[x["id"]])

    items = merge(fresh, previous.get("items", []), config.get("keep_days", 14), config.get("max_items", 900),
                  set(order), fetched_at)
    for it in items:
        it["pick"] = bool(set(it["tags"]) & PICK_TAGS.get(it["c"], set()))

    data = {
        "generated_at": iso(fetched_at),
        "next_hours": 3,
        "sources": status,
        "briefing": make_briefing(items, previous.get("briefing"), fetched_at),
        "yt_ids": yt_cache,
        "items": items,
    }
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    ok = sum(1 for s in status if s["ok"])
    print(f"소스 {ok}/{len(status)} 정상 · 새로 읽음 {len(fresh)} · 저장 {len(items)}")


if __name__ == "__main__":
    main()
