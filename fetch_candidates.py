#!/usr/bin/env python3
"""
KDT 야간 모니터링 — RSS 수집 및 링크 결정론적 처리
출력: candidates.json
규칙: link 필드는 항상 클릭 가능한 URL. "검색 요망" 절대 출력 안 함.
"""

import base64
import datetime
import json
import re
import sys
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

import requests

# =============================================================================
# RSS 피드 정의 (국가 태그·매체명)
# =============================================================================
RSS_FEEDS = [
    # ── 미국 ─────────────────────────────────────────────────────────────────
    {
        "country": "미국",
        "media": "라디오코리아",
        "url": "https://news.google.com/rss/search?q=site:radiokorea.com&hl=ko&gl=US&ceid=US:ko",
    },
    {
        "country": "미국",
        "media": "미주중앙일보",
        "url": "https://news.google.com/rss/search?q=site:koreadaily.com&hl=ko&gl=US&ceid=US:ko",
    },
    {
        "country": "미국",
        "media": "미주한국일보",
        # site:koreatimes.com 단독은 구글뉴스 색인이 약함 → 동포 키워드 병행으로 색인 강화
        # (연합뉴스 활성 피드와 동일한 키워드+site 패턴 차용)
        "url": (
            "https://news.google.com/rss/search"
            "?q=(%ED%95%9C%EC%9D%B8+OR+%EB%8F%99%ED%8F%AC+OR+%EA%B5%90%EB%AF%BC)+site:koreatimes.com"
            "&hl=ko&gl=US&ceid=US:ko"
        ),
    },
    {
        "country": "미국",
        "media": "한국일보 애틀랜타",
        # 동남부 한인 커버 — LA 편중 완화 (신규 추가, 내일 RSS 실측으로 검증 필요)
        "url": "https://news.google.com/rss/search?q=site:higoodday.com&hl=ko&gl=US&ceid=US:ko",
    },
    {
        "country": "미국",
        "media": "NYT/WSJ",
        "url": (
            "https://news.google.com/rss/search"
            "?q=Korean+American+(site:nytimes.com+OR+site:wsj.com)"
            "&hl=en&gl=US&ceid=US:en"
        ),
    },
    # ── 일본 ─────────────────────────────────────────────────────────────────
    {
        "country": "일본",
        "media": "朝日新聞",
        "url": (
            "https://news.google.com/rss/search"
            "?q=%E9%9F%93%E5%9B%BD+%E5%9C%A8%E6%97%A5+site:asahi.com"
            "&hl=ja&gl=JP&ceid=JP:ja"
        ),
    },
    {
        "country": "일본",
        "media": "産経ニュース",
        "url": (
            "https://news.google.com/rss/search"
            "?q=%E9%9F%93%E5%9B%BD+%E5%9C%A8%E6%97%A5+site:sankei.com"
            "&hl=ja&gl=JP&ceid=JP:ja"
        ),
    },
    {
        "country": "일본",
        "media": "Yahoo Japan",
        "url": (
            "https://news.google.com/rss/search"
            "?q=%E5%9C%A8%E6%97%A5%E9%9F%93%E5%9B%BD%E4%BA%BA+%E6%95%99%E8%82%B2"
            "&hl=ja&gl=JP&ceid=JP:ja"
        ),
    },
    # ── 캐나다 ───────────────────────────────────────────────────────────────
    {
        "country": "캐나다",
        "media": "밴쿠버 중앙일보",
        "url": "https://news.google.com/rss/search?q=site:vanchosun.com&hl=ko&gl=CA&ceid=CA:ko",
    },
    {
        "country": "캐나다",
        "media": "Globe and Mail",
        "url": (
            "https://news.google.com/rss/search"
            "?q=Korean+site:theglobeandmail.com"
            "&hl=en&gl=CA&ceid=CA:en"
        ),
    },
    {
        "country": "캐나다",
        "media": "CBC",
        "url": "https://news.google.com/rss/search?q=Korean+site:cbc.ca&hl=en&gl=CA&ceid=CA:en",
    },
    # ── 호주 ─────────────────────────────────────────────────────────────────
    {
        "country": "호주",
        "media": "호주 톱디지털",
        "url": "https://news.google.com/rss/search?q=site:topdigital.com.au&hl=ko&gl=AU&ceid=AU:ko",
    },
    # ── 베트남 ───────────────────────────────────────────────────────────────
    {
        "country": "베트남",
        "media": "인사이드비나",
        "url": "https://news.google.com/rss/search?q=site:insidevina.com&hl=ko&gl=VN&ceid=VN:ko",
    },
    {
        "country": "베트남",
        "media": "베트남코리아타임스",
        "url": "https://news.google.com/rss/search?q=site:vietnamkoreatimes.com&hl=ko&gl=VN&ceid=VN:ko",
    },
    # ── 한국 ─────────────────────────────────────────────────────────────────
    {
        "country": "한국",
        "media": "연합뉴스",
        "url": (
            "https://news.google.com/rss/search"
            "?q=%EC%9E%AC%EC%99%B8%EB%8F%99%ED%8F%AC+%EA%B5%90%EB%AF%BC+site:yna.co.kr"
            "&hl=ko&gl=KR&ceid=KR:ko"
        ),
    },
    {
        "country": "한국",
        "media": "재외동포신문/뉴스코리아/코리아포스트",
        "url": (
            "https://news.google.com/rss/search"
            "?q=%EC%9E%AC%EC%99%B8%EB%8F%99%ED%8F%AC"
            "+(site:dongponews.net+OR+site:newskorea.com+OR+site:koreapost.com)"
            "&hl=ko&gl=KR&ceid=KR:ko"
        ),
    },
]

STALE_DAYS = 2

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; KDT-RSS/1.0)",
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
}


# =============================================================================
# Google News 리다이렉트 URL 디코딩 (실패 시 원본 URL 반환 — 절대 검색 요망 없음)
# =============================================================================

def _decode_gnews_token(token: str) -> str | None:
    """base64url 토큰에서 https:// URL 추출 시도. 실패하면 None."""
    # 패딩 보정
    rem = len(token) % 4
    if rem:
        token += "=" * (4 - rem)
    try:
        raw = base64.urlsafe_b64decode(token)
    except Exception:
        return None
    # 바이트 스트림에서 https:// URL 패턴 추출
    matches = re.findall(rb'https?://[^\x00-\x1f\x7f\s]{12,}', raw)
    for m in matches:
        url = m.decode("utf-8", errors="ignore").rstrip("\x00\x01")
        # 구글 도메인이 아닌 실제 기사 URL 선택
        if "google.com" not in url and "." in url:
            return url
    return None


def resolve_link(raw_link: str) -> str:
    """
    항상 클릭 가능한 URL을 반환.
    - 구글 뉴스 리다이렉트가 아니면 그대로.
    - 구글 뉴스면 디코딩 시도 → 성공 시 실제 URL, 실패 시 리다이렉트 URL 그대로.
    - 빈 링크면 빈 문자열 (발생 빈도 낮음).
    """
    if not raw_link:
        return ""
    if "news.google.com/rss/articles/" not in raw_link:
        return raw_link

    m = re.search(r"/rss/articles/([A-Za-z0-9_-]+)", raw_link)
    if not m:
        return raw_link  # 파싱 불가 → 리다이렉트 URL 그대로

    decoded = _decode_gnews_token(m.group(1))
    return decoded if decoded else raw_link  # 실패해도 리다이렉트 URL 반환


# =============================================================================
# 날짜 검증
# =============================================================================

def check_date(pub_str: str, today: datetime.date) -> tuple[bool, bool]:
    """(old, date_unverified) 반환."""
    if not pub_str:
        return False, True
    for parser in (parsedate_to_datetime, lambda s: datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))):
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

        # Google News RSS <source> 태그에서 실제 매체명 추출 시도
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
        })
    return results


# =============================================================================
# 메인
# =============================================================================

def main():
    today = datetime.date.today()
    print(f"KDT RSS 수집 — {today} ({len(RSS_FEEDS)}개 피드)")

    all_items: list[dict] = []

    for feed in RSS_FEEDS:
        print(f"\n[{feed['country']}] {feed['media']}")
        xml_bytes = fetch_xml(feed["url"])
        if xml_bytes is None:
            print("  → 0건")
            continue
        items = parse_items(xml_bytes, feed, today)
        print(f"  → {len(items)}건")
        all_items.extend(items)

    out = "candidates.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(all_items, f, ensure_ascii=False, indent=2)

    print(f"\n총 {len(all_items)}건 → {out} 저장 완료")

    # ── 아사히·라디오코리아 링크 검증 출력 ──────────────────────────────────
    def show_sample(label, items):
        print(f"\n=== {label} link 샘플 ===")
        if not items:
            print("  (0건)")
            return
        for c in items[:5]:
            print(f"  제목: {c['title'][:60]}")
            print(f"  link: {c['link']}")
            print()

    asahi = [c for c in all_items if "朝日" in c["media"] or "asahi.com" in c["link"]]
    rk = [c for c in all_items if "라디오코리아" in c["media"] or "radiokorea.com" in c["link"]]
    show_sample("朝日新聞", asahi)
    show_sample("라디오코리아", rk)


if __name__ == "__main__":
    main()
