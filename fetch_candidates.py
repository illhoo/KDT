#!/usr/bin/env python3
"""
KDT 야간 모니터링 — RSS 수집 및 링크 결정론적 처리 [v3 동포 키워드 통일]
출력: candidates.json
규칙: link 필드는 항상 클릭 가능한 URL. "검색 요망" 절대 출력 안 함.

[v3 구조 전환]
  기존: 매체 고정(site:도메인 단독) → 매체 전체 기사 유입 → 야구·연예·증시 노이즈
  변경: 전 매체 "동포 키워드 + site:" 통일 → 소스 단계에서 동포 기사만 수집
        + 신규 국가는 도메인 안 묶고 "국가 구글뉴스 + 동포 키워드" 광역 검색
        + 국가·매체 확대 (독일·영국·동남아 등)

  핵심: 재외동포신문(국내 동포매체) 의존 탈피 → 해외 현지 매체에서 직접 동포 기사 수집.
"""

import base64
import datetime
import json
import re
import sys
import urllib.parse
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

import requests

# =============================================================================
# 동포 키워드 세트 (언어권별) — 전 매체 공통 적용
#   "현지 동포"를 가리키되 "한국 본토"는 안 가리키는 선.
# =============================================================================
KW_KO = "한인 OR 동포 OR 교민 OR 재외"
KW_EN = '"Korean American" OR "Korean diaspora" OR "Korean community" OR "Korean Canadian" OR "Korean Australian" OR "Korean immigrant"'
KW_JA = "在日韓国 OR 韓国系 OR 在日コリアン OR 韓人"
KW_DE = '"Koreaner in Deutschland" OR "koreanische Gemeinde" OR koreanischstämmig'  # 독일어
# 동남아/기타 한국어권은 KW_KO 재사용 (현지 한인 매체가 한국어)


# =============================================================================
# 수집 시간 윈도 — 최근 N일만 (구글 뉴스 when: 연산자)
#   매일 발송 + 신규성 감쇠(seen.json) 7일과 정합.
# =============================================================================
WHEN_WINDOW = "7d"


def gnews_search_url(query: str, hl: str, gl: str, ceid: str) -> str:
    """구글 뉴스 RSS 검색 URL 생성 (when:Nd 날짜 제한 + URL 인코딩)."""
    q = urllib.parse.quote(f"{query} when:{WHEN_WINDOW}")
    return f"https://news.google.com/rss/search?q={q}&hl={hl}&gl={gl}&ceid={ceid}"


def site_kw_url(keywords: str, domain: str, hl: str, gl: str, ceid: str) -> str:
    """동포 키워드 + 특정 매체 도메인 검색 URL."""
    return gnews_search_url(f"({keywords}) site:{domain}", hl, gl, ceid)


def broad_kw_url(keywords: str, hl: str, gl: str, ceid: str) -> str:
    """도메인 안 묶고 해당 국가 구글뉴스에서 동포 키워드 광역 검색."""
    return gnews_search_url(f"({keywords})", hl, gl, ceid)


# =============================================================================
# RSS 피드 정의 — 전부 동포 키워드 적용
# =============================================================================
RSS_FEEDS = [
    # ── 미국 (한인 밀도 최대) ────────────────────────────────────────────────
    {"country": "미국", "media": "라디오코리아",
     "url": site_kw_url(KW_KO, "radiokorea.com", "ko", "US", "US:ko")},
    {"country": "미국", "media": "미주중앙일보",
     "url": site_kw_url(KW_KO, "koreadaily.com", "ko", "US", "US:ko")},
    {"country": "미국", "media": "미주한국일보",
     "url": site_kw_url(KW_KO, "koreatimes.com", "ko", "US", "US:ko")},
    {"country": "미국", "media": "한국일보 애틀랜타",
     "url": site_kw_url(KW_KO, "higoodday.com", "ko", "US", "US:ko")},
    {"country": "미국", "media": "미주 영문(NYT/WSJ)",
     "url": gnews_search_url(f"({KW_EN}) (site:nytimes.com OR site:wsj.com)", "en", "US", "US:en")},
    {"country": "미국", "media": "미국 광역",
     "url": broad_kw_url(KW_KO, "ko", "US", "US:ko")},

    # ── 일본 ─────────────────────────────────────────────────────────────────
    {"country": "일본", "media": "朝日新聞",
     "url": site_kw_url(KW_JA, "asahi.com", "ja", "JP", "JP:ja")},
    {"country": "일본", "media": "産経ニュース",
     "url": site_kw_url(KW_JA, "sankei.com", "ja", "JP", "JP:ja")},
    {"country": "일본", "media": "일본 광역",
     "url": broad_kw_url(KW_JA, "ja", "JP", "JP:ja")},
    {"country": "일본", "media": "일본 한국어 광역",
     "url": broad_kw_url(KW_KO, "ko", "JP", "JP:ko")},

    # ── 캐나다 ───────────────────────────────────────────────────────────────
    {"country": "캐나다", "media": "밴쿠버 중앙일보",
     "url": site_kw_url(KW_KO, "vanchosun.com", "ko", "CA", "CA:ko")},
    {"country": "캐나다", "media": "캐나다 영문 광역",
     "url": broad_kw_url(KW_EN, "en", "CA", "CA:en")},
    # 캐나다 한국어 광역 제거 — 한국 본토 중복

    # ── 호주 ─────────────────────────────────────────────────────────────────
    {"country": "호주", "media": "호주 톱디지털",
     "url": site_kw_url(KW_KO, "topdigital.com.au", "ko", "AU", "AU:ko")},
    {"country": "호주", "media": "호주 영문 광역",
     "url": broad_kw_url(KW_EN, "en", "AU", "AU:en")},
    # 호주 한국어 광역 제거 — 한국 본토 중복

    # ── 베트남 ───────────────────────────────────────────────────────────────
    {"country": "베트남", "media": "인사이드비나",
     "url": site_kw_url(KW_KO, "insidevina.com", "ko", "VN", "VN:ko")},
    {"country": "베트남", "media": "베트남코리아타임스",
     "url": site_kw_url(KW_KO, "vietnamkoreatimes.com", "ko", "VN", "VN:ko")},
    # 베트남 한국어 광역 제거 — 한국 본토 중복

    # ══ 신규 국가 (2단계 확대) — 도메인 안 묶고 광역 검색 ═══════════════════════
    # ── 독일 (유럽 한인 밀도 높음) ──────────────────────────────────────────
    {"country": "독일", "media": "독일 한국어 광역",
     "url": broad_kw_url(KW_DE, "de", "DE", "DE:de")},
    # 독일 한국어 광역 제거 — 한국 본토 중복 확인됨 (gl=DE+ko는 국가코드 무시)

    # ── 영국 ─────────────────────────────────────────────────────────────────
    {"country": "영국", "media": "영국 영문 광역",
     "url": broad_kw_url(KW_EN, "en", "GB", "GB:en")},
    # 영국 한국어 광역 제거 — 한국 본토 중복

    # ── 싱가포르/동남아 ──────────────────────────────────────────────────────
    {"country": "싱가포르", "media": "싱가포르 영문 광역",
     "url": broad_kw_url(KW_EN, "en", "SG", "SG:en")},
    # 싱가포르 한국어 광역 제거 — 한국 본토 중복

    # ── 중국 (재중동포 밀도 최대) ───────────────────────────────────────────
    # 중국 한국어 광역 제거 — 한국 본토 중복. 현지 한인매체 리서치 후 site 추가 예정.

    # ══ 1순위 신규 5개국 — 현지 한인매체 site 고정 (한국어 직수신) + 현지어 광역 ═══
    #   한국어 광역은 전부 한국 본토 중복이라 제거. 현지 매체 색인 여부는 실측 검증.
    # ── 브라질 (상파울루) ───────────────────────────────────────────────────
    {"country": "브라질", "media": "좋은아침뉴스",
     "url": site_kw_url(KW_KO, "bomdianews.com.br", "ko", "BR", "BR:ko")},
    {"country": "브라질", "media": "브라질투데이",
     "url": site_kw_url(KW_KO, "hanintoday.com.br", "ko", "BR", "BR:ko")},
    {"country": "브라질", "media": "브라질 포르투갈어 광역",
     "url": broad_kw_url("coreano OR coreana OR sul-coreano OR comunidade coreana", "pt-BR", "BR", "BR:pt-419")},

    # ── 멕시코 ───────────────────────────────────────────────────────────────
    {"country": "멕시코", "media": "멕시코한인신문",
     "url": site_kw_url(KW_KO, "haninsinmun.com", "ko", "MX", "MX:ko")},
    {"country": "멕시코", "media": "KMNEWS",
     "url": site_kw_url(KW_KO, "kmnews.info", "ko", "MX", "MX:ko")},
    {"country": "멕시코", "media": "멕시코 스페인어 광역",
     "url": broad_kw_url("coreano OR coreana OR comunidad coreana OR surcoreano", "es-419", "MX", "MX:es-419")},

    # ── 프랑스 (파리) ────────────────────────────────────────────────────────
    {"country": "프랑스", "media": "파리지성",
     "url": site_kw_url(KW_KO, "parisjisung.com", "ko", "FR", "FR:ko")},
    {"country": "프랑스", "media": "프랑스존",
     "url": site_kw_url(KW_KO, "francezone.com", "ko", "FR", "FR:ko")},
    {"country": "프랑스", "media": "프랑스어 광역",
     "url": broad_kw_url('"Coréens en France" OR "communauté coréenne" OR sud-coréen', "fr", "FR", "FR:fr")},

    # ── 뉴질랜드 (오클랜드) ─────────────────────────────────────────────────
    {"country": "뉴질랜드", "media": "위클리코리아",
     "url": site_kw_url(KW_KO, "weeklykoreanz.com", "ko", "NZ", "NZ:ko")},
    {"country": "뉴질랜드", "media": "코리아포스트",
     "url": site_kw_url(KW_KO, "nzkoreapost.com", "ko", "NZ", "NZ:ko")},
    {"country": "뉴질랜드", "media": "코리아리뷰",
     "url": site_kw_url(KW_KO, "koreareview.co.nz", "ko", "NZ", "NZ:ko")},
    {"country": "뉴질랜드", "media": "뉴질랜드 영문 광역",
     "url": broad_kw_url(KW_EN, "en", "NZ", "NZ:en")},

    # ── 필리핀 (마닐라) ─────────────────────────────────────────────────────
    {"country": "필리핀", "media": "마닐라서울",
     "url": site_kw_url(KW_KO, "manilaseoul.co.kr", "ko", "PH", "PH:ko")},
    {"country": "필리핀", "media": "필리핀 영문 광역",
     "url": broad_kw_url(KW_EN, "en", "PH", "PH:en")},

    # ── 한국 (동포 키워드 — 보조. 메인 아님) ────────────────────────────────
    {"country": "한국", "media": "연합뉴스",
     "url": site_kw_url("재외동포 OR 교민 OR 재외국민", "yna.co.kr", "ko", "KR", "KR:ko")},
    {"country": "한국", "media": "재외동포신문 외",
     "url": gnews_search_url(
         "재외동포 (site:dongponews.net OR site:newskorea.com OR site:koreapost.com)",
         "ko", "KR", "KR:ko")},
]

STALE_DAYS = 4

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; KDT-RSS/1.0)",
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
}


# =============================================================================
# Google News 리다이렉트 URL 디코딩
# =============================================================================

def _decode_gnews_token(token: str) -> str | None:
    rem = len(token) % 4
    if rem:
        token += "=" * (4 - rem)
    try:
        raw = base64.urlsafe_b64decode(token)
    except Exception:
        return None
    matches = re.findall(rb'https?://[^\x00-\x1f\x7f\s]{12,}', raw)
    for m in matches:
        url = m.decode("utf-8", errors="ignore").rstrip("\x00\x01")
        if "google.com" not in url and "." in url:
            return url
    return None


def resolve_link(raw_link: str) -> str:
    if not raw_link:
        return ""
    if "news.google.com/rss/articles/" not in raw_link:
        return raw_link
    m = re.search(r"/rss/articles/([A-Za-z0-9_-]+)", raw_link)
    if not m:
        return raw_link
    decoded = _decode_gnews_token(m.group(1))
    return decoded if decoded else raw_link


# =============================================================================
# 날짜 검증
# =============================================================================

def check_date(pub_str: str, today: datetime.date) -> tuple[bool, bool]:
    if not pub_str:
        return False, True
    for parser in (parsedate_to_datetime,
                   lambda s: datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))):
        try:
            dt = parser(pub_str)
            delta = today - dt.date()
            return delta.days > STALE_DAYS, False
        except Exception:
            continue
    return False, True


# =============================================================================
# RSS fetch & parse
# =============================================================================

def fetch_xml(url: str) -> bytes | None:
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        return r.content
    except Exception as e:
        print(f"  [WARN] fetch failed: {e}", file=sys.stderr)
        return None


def parse_items(xml_bytes: bytes, feed_meta: dict, today: datetime.date) -> list[dict]:
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        print(f"  [WARN] XML parse error: {e}", file=sys.stderr)
        return []

    results = []
    for item in root.iter("item"):
        def text(tag):
            el = item.find(tag)
            return (el.text or "").strip() if el is not None else ""

        title = text("title")
        raw_link = text("link")
        pub_str = text("pubDate")

        src_el = item.find("source")
        media = (src_el.text or "").strip() if src_el is not None else ""
        if not media:
            media = feed_meta["media"]

        link = resolve_link(raw_link)
        old, date_unverified = check_date(pub_str, today)

        results.append({
            "country": feed_meta["country"],
            "title": title,
            "media": media,
            "link": link,
            "pubDate": pub_str,
            "old": old,
            "date_unverified": date_unverified,
            "feed_media": feed_meta["media"],   # 어느 피드에서 왔는지 (진단용)
        })
    return results


# =============================================================================
# 메인
# =============================================================================

def main():
    today = datetime.date.today()
    print(f"KDT RSS 수집 [v3] — {today} ({len(RSS_FEEDS)}개 피드)")

    all_items: list[dict] = []
    per_feed_count: dict[str, int] = {}

    for feed in RSS_FEEDS:
        label = f"[{feed['country']}] {feed['media']}"
        print(f"\n{label}")
        xml_bytes = fetch_xml(feed["url"])
        if xml_bytes is None:
            print("  → 0건")
            per_feed_count[label] = 0
            continue
        items = parse_items(xml_bytes, feed, today)
        print(f"  → {len(items)}건")
        per_feed_count[label] = len(items)
        all_items.extend(items)

    out = "candidates.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(all_items, f, ensure_ascii=False, indent=2)

    print(f"\n{'='*60}")
    print(f"총 {len(all_items)}건 → {out} 저장 완료")
    print(f"{'='*60}")

    # ── 국가별 집계 ──────────────────────────────────────────────────────────
    from collections import Counter
    cc = Counter(c["country"] for c in all_items)
    print(f"\n── 국가별 수집 ──")
    for country, cnt in sorted(cc.items(), key=lambda x: -x[1]):
        print(f"  {country}: {cnt}건")

    # ── 피드별 수집 건수 (어느 매체가 동포 기사를 잘 무는지 진단) ──────────────
    print(f"\n── 피드별 수집 (0건 피드 = 동포 기사 없거나 색인 약함) ──")
    for label, cnt in per_feed_count.items():
        mark = "  " if cnt > 0 else "⚠ "
        print(f"  {mark}{label}: {cnt}건")


if __name__ == "__main__":
    main()
