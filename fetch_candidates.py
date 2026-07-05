#!/usr/bin/env python3
"""
KDT 야간 모니터링 — RSS 수집 및 링크 결정론적 처리 [v4 국가 확장]
출력: candidates.json
규칙: link 필드는 항상 클릭 가능한 URL. "검색 요망" 절대 출력 안 함.

[v4 구조]
  - 전 매체 "동포 키워드 + site:" 통일 (한국 본토 중복 방지)
  - 신규 국가는 현지 한인매체 site 고정 (한국어 직수신) + 현지어 광역
  - 범동포 매체(월드코리안·세계한인신문)로 매체 약한 나라 보조
  - "국가코드 + 한국어 광역"은 한국 본토 중복이라 전면 제거
  - when:7d 최근 7일 수집, STALE_DAYS=4로 fresh 판정
  - 0건 헛피드(파리지성·KMNEWS·마닐라서울·한마당) 정리
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
# =============================================================================
KW_KO = "한인 OR 동포 OR 교민 OR 재외"
KW_EN = '"Korean American" OR "Korean diaspora" OR "Korean community" OR "Korean Canadian" OR "Korean Australian" OR "Korean immigrant"'
KW_JA = "在日韓国 OR 韓国系 OR 在日コリアン OR 韓人"
KW_DE = '"Koreaner in Deutschland" OR "koreanische Gemeinde" OR koreanischstämmig'

# 수집 시간 윈도 — 최근 N일 (매일 발송 + 신규성 감쇠 7일과 정합)
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
    # 미국 (한인 밀도 최대)
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

    # 일본
    {"country": "일본", "media": "朝日新聞",
     "url": site_kw_url(KW_JA, "asahi.com", "ja", "JP", "JP:ja")},
    {"country": "일본", "media": "産経ニュース",
     "url": site_kw_url(KW_JA, "sankei.com", "ja", "JP", "JP:ja")},
    {"country": "일본", "media": "일본 광역",
     "url": broad_kw_url(KW_JA, "ja", "JP", "JP:ja")},
    {"country": "일본", "media": "일본 한국어 광역",
     "url": broad_kw_url(KW_KO, "ko", "JP", "JP:ko")},

    # 캐나다
    {"country": "캐나다", "media": "밴쿠버 중앙일보",
     "url": site_kw_url(KW_KO, "vanchosun.com", "ko", "CA", "CA:ko")},
    {"country": "캐나다", "media": "캐나다 영문 광역",
     "url": broad_kw_url(KW_EN, "en", "CA", "CA:en")},

    # 호주
    {"country": "호주", "media": "호주 톱디지털",
     "url": site_kw_url(KW_KO, "topdigital.com.au", "ko", "AU", "AU:ko")},
    {"country": "호주", "media": "호주 영문 광역",
     "url": broad_kw_url(KW_EN, "en", "AU", "AU:en")},

    # 베트남
    {"country": "베트남", "media": "인사이드비나",
     "url": site_kw_url(KW_KO, "insidevina.com", "ko", "VN", "VN:ko")},

    # 싱가포르
    {"country": "싱가포르", "media": "싱가포르 영문 광역",
     "url": broad_kw_url(KW_EN, "en", "SG", "SG:en")},

    # 독일 (재유럽 한인 최대)
    {"country": "독일", "media": "베를린리포트",
     "url": site_kw_url(KW_KO, "berlinreport.com", "ko", "DE", "DE:ko")},
    {"country": "독일", "media": "교포신문",
     "url": site_kw_url(KW_KO, "kyoposhinmun.de", "ko", "DE", "DE:ko")},
    {"country": "독일", "media": "구텐탁코리아",
     "url": site_kw_url(KW_KO, "gutentagkorea.com", "ko", "DE", "DE:ko")},
    {"country": "독일", "media": "독일어 광역",
     "url": broad_kw_url(KW_DE, "de", "DE", "DE:de")},

    # 영국 (뉴몰든 한인타운)
    {"country": "영국", "media": "코리안위클리",
     "url": site_kw_url(KW_KO, "koweekly.co.uk", "ko", "GB", "GB:ko")},
    {"country": "영국", "media": "영국 영문 광역",
     "url": broad_kw_url(KW_EN, "en", "GB", "GB:en")},

    # 브라질 (상파울루)
    {"country": "브라질", "media": "좋은아침뉴스",
     "url": site_kw_url(KW_KO, "bomdianews.com.br", "ko", "BR", "BR:ko")},
    {"country": "브라질", "media": "브라질투데이",
     "url": site_kw_url(KW_KO, "hanintoday.com.br", "ko", "BR", "BR:ko")},
    {"country": "브라질", "media": "브라질 포르투갈어 광역",
     "url": broad_kw_url("coreano OR coreana OR sul-coreano OR comunidade coreana", "pt-BR", "BR", "BR:pt-419")},

    # 멕시코
    {"country": "멕시코", "media": "멕시코한인신문",
     "url": site_kw_url(KW_KO, "haninsinmun.com", "ko", "MX", "MX:ko")},
    {"country": "멕시코", "media": "멕시코 스페인어 광역",
     "url": broad_kw_url("coreano OR coreana OR comunidad coreana OR surcoreano", "es-419", "MX", "MX:es-419")},

    # 프랑스 (파리)
    {"country": "프랑스", "media": "프랑스존",
     "url": site_kw_url(KW_KO, "francezone.com", "ko", "FR", "FR:ko")},
    {"country": "프랑스", "media": "프랑스어 광역",
     "url": broad_kw_url('"Coréens en France" OR "communauté coréenne" OR sud-coréen', "fr", "FR", "FR:fr")},

    # 뉴질랜드 (오클랜드)
    {"country": "뉴질랜드", "media": "위클리코리아",
     "url": site_kw_url(KW_KO, "weeklykoreanz.com", "ko", "NZ", "NZ:ko")},
    {"country": "뉴질랜드", "media": "코리아포스트",
     "url": site_kw_url(KW_KO, "nzkoreapost.com", "ko", "NZ", "NZ:ko")},
    {"country": "뉴질랜드", "media": "코리아리뷰",
     "url": site_kw_url(KW_KO, "koreareview.co.nz", "ko", "NZ", "NZ:ko")},
    {"country": "뉴질랜드", "media": "뉴질랜드 영문 광역",
     "url": broad_kw_url(KW_EN, "en", "NZ", "NZ:en")},

    # 필리핀 (마닐라)
    {"country": "필리핀", "media": "필리핀 영문 광역",
     "url": broad_kw_url(KW_EN, "en", "PH", "PH:en")},

    # 인도네시아 (자카르타 — 교민 3만+)
    {"country": "인도네시아", "media": "한인포스트",
     "url": site_kw_url(KW_KO, "haninpost.com", "ko", "ID", "ID:ko")},
    {"country": "인도네시아", "media": "자카르타경제신문",
     "url": site_kw_url(KW_KO, "pagi.co.id", "ko", "ID", "ID:ko")},
    {"country": "인도네시아", "media": "인니 영문 광역",
     "url": broad_kw_url(KW_EN, "en", "ID", "ID:en")},

    # 태국 (방콕)
    {"country": "태국", "media": "한아시아",
     "url": site_kw_url(KW_KO, "hanasia.com", "ko", "TH", "TH:ko")},
    {"country": "태국", "media": "교민잡지",
     "url": site_kw_url(KW_KO, "kyominthai.com", "ko", "TH", "TH:ko")},

    # 말레이시아 (쿠알라룸푸르)
    {"country": "말레이시아", "media": "말레이시아 영문 광역",
     "url": broad_kw_url(KW_EN, "en", "MY", "MY:en")},

    # 범동포 (전 세계 한인 뉴스 — 매체 약한 나라 보조 그물)
    {"country": "범동포", "media": "월드코리안뉴스",
     "url": site_kw_url(KW_KO, "worldkorean.net", "ko", "KR", "KR:ko")},
    {"country": "범동포", "media": "세계한인신문",
     "url": site_kw_url(KW_KO, "oktimes.co.kr", "ko", "KR", "KR:ko")},

    # 한국 (동포 키워드 — 보조. 메인 아님)
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

def _decode_gnews_token(token: str):
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

def check_date(pub_str: str, today: datetime.date):
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

def fetch_xml(url: str):
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        return r.content
    except Exception as e:
        print(f"  [WARN] fetch failed: {e}", file=sys.stderr)
        return None


def parse_items(xml_bytes: bytes, feed_meta: dict, today: datetime.date):
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
            "feed_media": feed_meta["media"],
        })
    return results


# =============================================================================
# 메인
# =============================================================================

def main():
    today = datetime.date.today()
    print(f"KDT RSS 수집 [v4] — {today} ({len(RSS_FEEDS)}개 피드)")

    all_items = []
    per_feed_count = {}

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

    from collections import Counter
    cc = Counter(c["country"] for c in all_items)
    print(f"\n[국가별 수집]")
    for country, cnt in sorted(cc.items(), key=lambda x: -x[1]):
        print(f"  {country}: {cnt}건")

    print(f"\n[피드별 수집] 0건 = 동포기사 없거나 색인 약함")
    for label, cnt in per_feed_count.items():
        mark = "  " if cnt > 0 else "! "
        print(f"  {mark}{label}: {cnt}건")


if __name__ == "__main__":
    main()