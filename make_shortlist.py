#!/usr/bin/env python3
"""
KDT 야간 모니터링 — shortlist 생성 (큐레이션 전처리)

candidates.json → shortlist.json
  1. old=false(신규)만 추출
  2. 제목 기준 근접중복 제거
  3. 국가당 최대 30건 상한
  4. 목표 80~100건 (신규 30건 미만이면 old=true 보충)
"""

import json
import re
import unicodedata
from collections import defaultdict

CANDIDATES_PATH = "candidates.json"
SHORTLIST_PATH  = "shortlist.json"

COUNTRY_CAP     = 30   # 국가당 최대 건수
TARGET_MAX      = 100  # shortlist 목표 상한
FRESH_MIN       = 30   # 신규가 이 미만이면 old=true 보충


# ── 제목 정규화 (중복 비교용) ─────────────────────────────────────────────────

def _normalize(title: str) -> str:
    """구두점·공백·조사 제거 후 소문자화 — 근접중복 감지용."""
    t = unicodedata.normalize("NFC", title)
    t = re.sub(r"[^\w\s]", "", t, flags=re.UNICODE)
    t = re.sub(r"\s+", " ", t).strip().lower()
    return t


def _sim(a: str, b: str) -> float:
    """두 정규화 제목의 자카드 유사도 (어절 집합 기준)."""
    sa = set(a.split())
    sb = set(b.split())
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def dedup(items: list[dict], threshold: float = 0.6) -> list[dict]:
    """근접중복 제거 — 먼저 나온 항목 우선 보존."""
    kept: list[dict] = []
    norms: list[str] = []
    for item in items:
        n = _normalize(item["title"])
        duplicate = any(_sim(n, prev) >= threshold for prev in norms)
        if not duplicate:
            kept.append(item)
            norms.append(n)
    return kept


# ── 국가당 상한 적용 ──────────────────────────────────────────────────────────

def apply_country_cap(items: list[dict], cap: int) -> list[dict]:
    buckets: dict[str, list[dict]] = defaultdict(list)
    for item in items:
        buckets[item["country"]].append(item)

    result: list[dict] = []
    for country, group in buckets.items():
        result.extend(group[:cap])
    return result


# ── 보충 (old=true, 최근순) ───────────────────────────────────────────────────

def _pub_sort_key(item: dict):
    """pubDate 문자열을 정렬 키로 변환 (내림차순 → 최신이 앞)."""
    pub = item.get("pubDate") or ""
    return pub  # ISO·RFC2822 모두 문자열 사전순이 날짜순과 대체로 일치


def supplement(shortlist: list[dict], all_items: list[dict], target: int) -> list[dict]:
    """shortlist가 target 미만이면 old=true 중 최신순으로 보충."""
    needed = target - len(shortlist)
    if needed <= 0:
        return shortlist

    shortlist_ids = {id(x) for x in shortlist}
    stale = [x for x in all_items if x.get("old") and id(x) not in shortlist_ids]

    # 최근 발행일 순
    stale_sorted = sorted(stale, key=_pub_sort_key, reverse=True)

    # 중복 제거 후 보충
    existing_norms = [_normalize(x["title"]) for x in shortlist]
    added = 0
    for item in stale_sorted:
        if added >= needed:
            break
        n = _normalize(item["title"])
        if any(_sim(n, prev) >= 0.6 for prev in existing_norms):
            continue
        shortlist.append(item)
        existing_norms.append(n)
        added += 1

    return shortlist


# ── 메인 ─────────────────────────────────────────────────────────────────────

def main():
    with open(CANDIDATES_PATH, encoding="utf-8") as f:
        all_items: list[dict] = json.load(f)

    print(f"candidates.json: {len(all_items)}건")

    # 1. 신규만
    fresh = [x for x in all_items if not x.get("old")]
    print(f"  old=false(신규): {len(fresh)}건")

    # 2. 근접중복 제거
    fresh = dedup(fresh)
    print(f"  중복 제거 후: {len(fresh)}건")

    # 3. 국가당 상한
    fresh = apply_country_cap(fresh, COUNTRY_CAP)
    print(f"  국가 캡({COUNTRY_CAP}건) 적용 후: {len(fresh)}건")

    # 4. 신규 빈약 시 보충
    if len(fresh) < FRESH_MIN:
        print(f"  신규 {len(fresh)}건 < {FRESH_MIN} → old=true 보충")
        fresh = supplement(fresh, all_items, FRESH_MIN)
        print(f"  보충 후: {len(fresh)}건")

    # TARGET_MAX 초과 시 절삭 (국가 균형 고려해 이미 캡 적용됐으므로 단순 슬라이스)
    if len(fresh) > TARGET_MAX:
        fresh = fresh[:TARGET_MAX]
        print(f"  {TARGET_MAX}건으로 절삭")

    # shortlist에 원본 인덱스(candidates.json 기준) 부여
    idx_map = {id(x): i for i, x in enumerate(all_items)}
    for item in fresh:
        item["_orig_id"] = idx_map.get(id(item), -1)

    # 저장
    with open(SHORTLIST_PATH, "w", encoding="utf-8") as f:
        json.dump(fresh, f, ensure_ascii=False, indent=2)

    # 국가별 분포 출력
    from collections import Counter
    cc = Counter(x["country"] for x in fresh)
    print(f"\nshortlist.json: {len(fresh)}건 저장 완료")
    print("국가별 분포:")
    for country, cnt in sorted(cc.items(), key=lambda x: -x[1]):
        print(f"  {country}: {cnt}건")


if __name__ == "__main__":
    main()
