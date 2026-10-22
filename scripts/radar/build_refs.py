"""radar/references.json 만들기.

노션 레퍼런스 DB에서 내보낸 행(notion_refs_export.json)이 원본이고,
ref_candidates.json은 링크 점검(refcheck.json)을 통과한 것만 보탠다.
노션에 아직 없는 후보는 --new-out 파일로 따로 적어 노션에 추가할 수 있게 한다.

공개 저장소라 '용도(use)'·'반(classes)' 칸은 넣지 않는다 — 들어온 파일에 이 칸이 있으면 멈춘다.
이름·메모 속 반 이름은 환경 변수 RADAR_HIDE_WORDS(쉼표로 나눈 반 이름 — 저장소엔 안 적음)가 있으면 지운다.
--check 파일… : 공개할 JSON 에 용도·반 칸이나 반 이름이 남았는지 보고, 있으면 1로 끝남 (워크플로가 배포 전에 돌림).
"""

import argparse
import datetime as dt
import json
import os
import re
import sys


def norm(url):
    u = (url or "").strip().lower()
    u = re.sub(r"^https?://", "", u)
    u = re.sub(r"^www\.", "", u)
    return u.rstrip("/")


HIDDEN_KEYS = ("use", "classes")   # 공개하지 않는 칸
PARTICLE = "(?:에서|으로|부터|까지|마다|끼리|은|는|이|가|을|를|에|의|용|과|와|도|만|로|별)"


def hide_rx():
    words = [w.strip() for w in os.environ.get("RADAR_HIDE_WORDS", "").split(",") if w.strip()]
    if not words:
        return None
    word = "(?:" + "|".join(re.escape(w) for w in sorted(set(words), key=len, reverse=True)) + ")"
    # 'A 반' · 'A반에' · 'A·B 반' · 'A/B 반용' — 앞이 한글이면 아님 ('반드시' 같은 낱말은 반 이름이 앞에 없어 안 걸림)
    return re.compile(r"(?<![가-힣])" + word + r"(?:\s?[·,/]\s?" + word + r")*\s?반" + PARTICLE + r"{0,3}(?![가-힣])")


HIDE_RX = hide_rx()


def no_class(text):
    if not HIDE_RX:
        return text or ""
    s = HIDE_RX.sub("", text or "")
    s = re.sub(r"\(\s*\)", "", s)
    s = re.sub(r"\s+([,·)])", r"\1", s)
    s = re.sub(r"(^|\s)[·,/](?=\s|$)", r"\1", s)   # 지운 자리에 남은 나눔표
    s = re.sub(r"^[\s·,/]+|[\s·,/]+$", "", s)
    return re.sub(r"\s{2,}", " ", s).strip()


def hidden_keys(items, where):
    bad = [i for i, r in enumerate(items) if isinstance(r, dict) and any(k in r for k in HIDDEN_KEYS)]
    if bad:
        sys.exit(f"{where}: {len(bad)}개 행에 공개하지 않는 칸({'·'.join(HIDDEN_KEYS)})이 있어요 — 노션에서 내보낼 때 이 칸은 빼 주세요 (처음: {bad[0]}번째 행)")


def check(paths):
    """공개할 JSON 검사: 용도·반 칸 · 반 이름(RADAR_HIDE_WORDS 가 있을 때). 문제가 있으면 1."""
    bad = 0
    for p in paths:
        text = open(p, encoding="utf-8").read()
        data = json.loads(text)
        items = data.get("items", []) if isinstance(data, dict) else []
        n = sum(1 for r in items if isinstance(r, dict) and any(k in r for k in HIDDEN_KEYS))
        if n:
            print(f"✗ {p}: 공개하지 않는 칸이 {n}개 행에", file=sys.stderr); bad += 1
        if HIDE_RX:
            hits = HIDE_RX.findall(text)
            if hits:
                print(f"✗ {p}: 반 이름 {len(hits)}곳", file=sys.stderr); bad += 1
    if not HIDE_RX:
        print("RADAR_HIDE_WORDS 가 없어 반 이름 검사는 건너뜀 (칸 검사만 함)")
    print("공개 전 확인: " + ("문제 있음" if bad else "통과") + f" ({len(paths)}개 파일)")
    return 1 if bad else 0


def alive(chk):
    st = (chk or {}).get("status") or 0
    return 200 <= st < 400 or st in (401, 403, 429)  # 403·429는 봇 차단일 뿐 사이트는 살아 있음


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", nargs="+", metavar="JSON", help="공개할 JSON 검사만 (만들지 않음)")
    ap.add_argument("--notion")
    ap.add_argument("--candidates")
    ap.add_argument("--refcheck")
    ap.add_argument("--out")
    ap.add_argument("--new-out")
    args = ap.parse_args()
    if args.check:
        sys.exit(check(args.check))
    if not all([args.notion, args.candidates, args.refcheck, args.out, args.new_out]):
        ap.error("--notion --candidates --refcheck --out --new-out 이 모두 필요해요 (또는 --check)")

    notion = json.load(open(args.notion, encoding="utf-8"))["items"]
    cands = json.load(open(args.candidates, encoding="utf-8"))["items"]
    checks = json.load(open(args.refcheck, encoding="utf-8"))["results"]
    hidden_keys(notion, args.notion)
    hidden_keys(cands, args.candidates)
    if not HIDE_RX:
        print("RADAR_HIDE_WORDS 가 없어 이름·메모 속 반 이름은 안 지움", file=sys.stderr)

    out, seen = [], set()
    for r in notion:
        key = norm(r.get("url"))
        if not key or key in seen:
            continue
        seen.add(key)
        out.append({
            "name": no_class(r.get("name", "")), "url": r.get("url", ""), "cat": r.get("cat") or [], "format": r.get("format", ""),
            "maps": r.get("maps") or [], "agents": r.get("agents") or [], "lang": r.get("lang", ""),
            "use_level": r.get("use_level", ""),
            "price": r.get("price", ""), "freshness": r.get("freshness", ""), "memo": no_class(r.get("memo", "")), "source": "notion",
        })

    new_rows = []
    for c in cands:
        chk = checks.get("yt:" + c["youtube"]) if c.get("youtube") else checks.get("url:" + c.get("url", ""))
        if not alive(chk):
            continue
        url = chk.get("url") if c.get("youtube") else c["url"]
        key = norm(url)
        if c.get("youtube"):
            # 같은 채널이 노션에 /@핸들 주소로 들어가 있을 수 있음
            alt = norm("https://www.youtube.com/" + c["youtube"])
            if alt in seen:
                continue
            seen.add(alt)
        if key in seen:
            continue
        seen.add(key)
        freshness = c.get("freshness") or ("가끔" if c.get("format") == "문서·가이드" else "매 패치·실시간")
        memo = c.get("memo", "")
        if c.get("youtube") and chk.get("latest_upload"):
            days = (dt.date.today() - dt.date.fromisoformat(chk["latest_upload"])).days
            freshness = "매 패치·실시간" if days <= 30 else "가끔" if days <= 180 else "멈춤·오래됨"
            memo = (memo + " · " if memo else "") + "최근 업로드 " + chk["latest_upload"]
        if chk.get("status") in (401, 403, 429):
            memo = (memo + " · " if memo else "") + "자동 점검 차단(브라우저로는 열림)"
        row = {
            "name": no_class(c["name"]), "url": url, "youtube": c.get("youtube", ""), "cat": c.get("cat") or [], "format": c.get("format", ""),
            "maps": c.get("maps") or ["전체"], "agents": c.get("agents") or ["전체"], "lang": c.get("lang", ""),
            "use_level": c.get("use_level", ""),
            "price": c.get("price", ""), "freshness": freshness, "memo": no_class(memo), "source": "candidate",
        }
        out.append(row)
        new_rows.append(row)

    json.dump({"updated": dt.date.today().isoformat(), "items": out}, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump({"items": new_rows}, open(args.new_out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"references.json {len(out)}개 (노션 {len(out) - len(new_rows)} + 새 후보 {len(new_rows)})")


if __name__ == "__main__":
    main()
