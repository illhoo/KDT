#!/usr/bin/env python3
"""
KDT 야간 모니터링 — shortlist + 규칙 기반 curation 자동 생성 [v3 화이트리스트]

candidates.json
  → shortlist.json  (신규 우선 + 중복 제거 + 국가 캡)
  → curation.json   (규칙 기반 자동 분류 — 모델 개입 없음)

[v3 화이트리스트 전환 — 철학 역전]
  기존(v2): 노이즈를 정규식으로 쳐내는 블랙리스트. 안 본 패턴은 계속 뚫림.
  변경(v3): "동포·한인 직결 신호가 있어야만 리스트". 통과 기준을 좁힘.

  - 리스트  = 동포/한인 직결 키워드 필수 (DONGPO_KEYWORDS / 지역+한인 거점)
  - 이관    = 동포 신호 약하지만 한국 관련성 있는 그물 (놓침 방지)
  - 제외    = 본토 일반뉴스(정책 포함)·스포츠·광고·가십·증시
  ※ 한국 본토 정책뉴스는 KDT가 안 잡음 — 발행인이 원문 직접 생산하는 별도 트랙.

  v2 블랙리스트(EXCLUDE_TITLE_RE)는 명백 노이즈 1차 차단용으로 유지하되,
  핵심 판정은 score_item의 화이트리스트 가점으로 이동.

[v2 신규성 감쇠] seen.json 누적 → 최근 노출 제목 7일 선형 감점(소프트).
"""

import datetime
import json
import re
import unicodedata
from collections import defaultdict

CANDIDATES_PATH = "candidates.json"
SHORTLIST_PATH  = "shortlist.json"
CURATION_PATH   = "curation.json"
SEEN_PATH       = "seen.json"

COUNTRY_CAP = 30   # 기본 국가당 최대 건수
COUNTRY_CAP_OVERRIDE = {   # 국가별 예외 (동포 기사 많은 곳 상향)
    "미국": 50,
}
TARGET_MAX  = 100  # shortlist 목표 상한
FRESH_MIN   = 30   # 신규가 이 미만이면 old=true 보충

# ── 신규성 감쇠 설정 ──────────────────────────────────────────────────────────
SEEN_WINDOW   = 7   # 최근 N일 내 노출 항목만 감점 대상
SEEN_MAX_PEN  = 5   # 최대 감점폭(노출 직후). 날짜 지날수록 선형 감소.

# KST 기준 오늘
_KST = datetime.timezone(datetime.timedelta(hours=9))
TODAY = datetime.datetime.now(_KST).date()
TODAY_STR = TODAY.isoformat()

# ══════════════════════════════════════════════════════════════════════════════
# 1차 차단 — 명백 노이즈 (블랙리스트, v2 유지·축약)
#   화이트리스트가 핵심이므로 여기는 "동포 키워드가 우연히 박힌 광고"까지
#   확실히 쳐낼 최소한만 유지. 나머지는 점수가 알아서 떨어뜨림.
# ══════════════════════════════════════════════════════════════════════════════
EXCLUDE_MEDIA_SUBSTR = [
    "Hot Deal",
    "AERA DIGITAL",
    "YouTube",       # 개인 영상 — 음모론·혐오 콘텐츠 유입 차단
    "facebook.com",  # 개인 게시물
]

EXCLUDE_TITLE_RE = [
    # 부동산·렌트 광고
    r"\d+br\s*\d+ba", r"유학생\s*ok", r"보증금.*구입", r"밴조선\s*부동산",
    # 코인·주식 종목 (한국어 포함)
    r"\bsolana\b", r"\bbitcoin\b", r"\bcrypto\b", r"암호화폐",
    r"주식\s*/\s*코인방", r"코인방",
    # 증시 와이어 (영문 — 한국어 유가·물가·환율은 안 걸림)
    r"\bUS stocks\b", r"\bWall Street\b", r"\bNasdaq\b", r"\bS&P 500\b",
    r"\bDow (Green|Jones)\b", r"\bStock Market Today\b", r"\bsell-?off\b",
    r"\bpremarket\b", r"\bFOREX:\s", r"\bKRW[A-Z]{3}\b", r"\b[A-Z]{3}KRW\b",
    r"\([A-Z]{2,5}\)\s+(Stock|Shares)\b",
    # 상품 광고 화법
    r"한\s*통이면", r"오래오래", r"단\s*한\s*번에", r"이거\s*하나면",
    r"스피드\s*염색", r"한\s*병으로", r"평생\s*무료", r"무료\s*체험",
    r"끝판왕.*\$", r"안마의자.*(단\s*\$|\₩|\d+만원)",
    # 쇼핑·기업 홍보
    r"고국배송", r"welcomes the launches?\b", r"\bIPO\b",
    r"\bearnings\s+(call|report)", r"\bfinancial results\b",
    r"\bgaming\s+pc\b", r"\bpowerful\s+gaming\b",
    # 종합 피드성·인덱스성 (매일 반복, 발제 가치 없음)
    r"모닝뉴스\s*헤드라인", r"^라디오코리아\s*뉴스$", r"증권소식",
    r"^\d+월\s*\d+일\s*(모닝|뉴스|헤드라인)", r"오늘의\s*(증권|뉴스|헤드라인)",
    r"주요\s*뉴스\s*$", r"뉴스\s*브리핑\s*$", r"^.{0,6}\s*뉴스데스크",
    # ── v5 추가: 교민 매체 벼룩시장·구인구직·부동산 게시글 ──
    # RadioKorea 등 교민 매체의 생활정보 게시판 글이 뉴스로 색인되는 것 차단.
    # "한인타운" 등 동포 키워드가 박혀 있어 화이트리스트를 통과하므로 제목 패턴으로 차단.
    # 구인·구직
    r"모집합니다", r"모집\s*합니다", r"구인\s*(합니다|광고)?",
    r"(서버|주방|캐셔|직원|매니저|기사|알바|파트|풀타임|사원)\s*(분)?\s*(모집|구함|구합니다|채용)",
    r"채용\s*공고", r"구직", r"사람\s*구합니다", r"급구",
    # 부동산·렌트 매물
    r"방\s*\d+\s*\+?\s*화\s*\d+",   # 방1+화1
    r"\d+\s*bed\s*\d+\s*bath", r"렌트\s*(합니다|줍니다|놓습니다)",
    r"(콘도|타운하우스|아파트|스튜디오|하우스)\s*렌트", r"룸메이트?\s*(구함|모집)",
    r"셰어\s*합니다", r"매매\s*합니다", r"(전세|월세|매물)\s*(있습니다|나왔습니다)",
    # 중고·판매 게시글
    r"팝니다\s*$", r"삽니다\s*$", r"양도\s*합니다", r"드립니다\s*$",
]

# ══════════════════════════════════════════════════════════════════════════════
# 화이트리스트 — 리스트 진입 자격 (이게 v3의 핵심)
# ══════════════════════════════════════════════════════════════════════════════

# ── 강한 동포 신호 (+5): 이거 있으면 리스트 직행 ──
# 재외동포·한인 사회를 직접 가리키는 명시 키워드
DONGPO_STRONG = [
    # 한국어 — 동포 지위·커뮤니티
    "동포", "교민", "교포", "재외국민", "재외동포", "재외선거",
    "한인회", "한인 사회", "한인사회", "한인 커뮤니티", "한인타운", "코리아타운",
    "동포청", "재외공관", "이달의 재외동포",
    "재미", "재일", "재캐", "재호", "재베", "재독", "재중",
    "재미동포", "재일동포", "재미교포", "재일교포",
    # 한국어 — 체류·신분 (동포 직결)
    "영주권", "시민권", "귀화", "영사관", "재외선거인",
    # 일본어 — 재일동포 직결
    "在日韓国", "在日朝鮮", "在日コリアン", "韓国籍", "在日2世", "在日3世",
]

# ── 중간 동포 신호 (+3): 한인 거점 지명 + 한국인/한인 정황 ──
# "LA 한국 식당 화재"처럼 동포 키워드 없어도 한인 거점에서 벌어진 일
KOREATOWN_HUBS = [
    # 미국
    "LA", "엘에이", "로스앤젤레스", "뉴욕", "뉴저지", "애틀랜타",
    "시카고", "시애틀", "댈러스", "휴스턴", "오렌지카운티", "어바인",
    "플러싱", "팰리세이즈", "풀러튼", "부에나파크",
    # 일본
    "도쿄", "오사카", "신오쿠보", "이쿠노", "쓰루하시", "교토",
    # 캐나다
    "토론토", "밴쿠버", "코퀴틀람",
    # 호주
    "시드니", "멜버른", "스트라스필드",
    # 베트남·동남아
    "하노이", "호치민", "다낭",
]
# 거점 지명이 "한국인/한인" 정황과 함께 있을 때만 동포로 인정 (가드)
HUB_KR_GUARD = [
    "한국인", "한인", "교민", "동포", "한국계", "한국 식당", "한국 마트",
    "한국 교회", "한국 학교", "한글학교", "한국 영사", "韓国人", "韓国系",
]

# ── 약한 신호 (+1, 이관 그물): 한국 직결이지만 동포 정황 약함 ──
# 점수만으론 리스트 미달(+3 필요), 이관으로 받아 발행인이 "이거 발제거리?" 눈으로 훑음.
# v3.2: 넓은 한국 신호(한국·한국인·Korea·Korean) 추가 — 이관 그물 확대.
#       이건 +1이라 리스트(+3)엔 절대 못 올라오고 이관 풀에만 쌓인다.
DIASPORA_GENERAL = [
    "diaspora", "immigrant", "overseas korean", "korean american",
    "korean canadian", "korean australian", "ethnic korean",
    "디아스포라", "이민 사회", "이민자", "동포사회", "이주민",
    # v3.2 넓은 한국 신호 (이관 그물용)
    "한국", "한국인", "한국계", "korea", "korean", "코리안",
]

# ── 교민 생활 사건·사고 (+2): 동포 신호와 결합 시 강화 ──
# 단독으로는 리스트 자격 없음. 동포/거점 신호와 함께 있을 때 가점.
LIFE_INCIDENT = [
    "실종", "체포", "징역", "사망", "사고", "화재", "총격", "강도",
    "추방", "단속", "구속", "피해", "사기", "행방불명",
    "산불", "홍수", "지진", "허리케인", "토네이도",
    "비자", "이민법", "영주권", "추방", "체류",
]

# ══════════════════════════════════════════════════════════════════════════════
# 감점 — 리스트 진입 방해 (블랙리스트 보조)
# ══════════════════════════════════════════════════════════════════════════════

# 홍보성·자사 실적
PROMO_TITLE_RE = [
    r"독보적\s*존재감", r"순익\s*달성", r"\d+억\s*순익",
    r"1분기.*순익", r"해외서.*존재감", r"\brecord\b.*\bfirst\s+half\b",
]

# 한반도·고국 정치 → 이관 강제 (동포 관심사이나 본토 사안)
KOREA_POL_KEYWORDS = [
    "서울시장", "대통령", "국회", "총선", "대선", "한반도",
    "북한", "미북", "남북", "비핵화", "종전", "평화협정",
    "윤석열", "이재명", "한덕수", "국회의장",
]

# 스포츠 (경기 결과·기록) — 동포 가드 없으면 감점
SPORTS_KEYWORDS = [
    "야구", "축구", "골프", "배구", "농구", "수영", "펜싱", "체조",
    "올스타", "MVP", "홈런", "타율", "완봉", "결승", "예선", "리그",
    "월드컵", "감독", "구단", "프로", "우승", "신기록", "주니어",
    "野球", "サッカー", "ゴルフ", "リーグ", "W杯", "選手権",
    "World Cup", "League", "Cup Final", "Semifinal", "Nations League",
]
SPORTS_DONGPO_GUARD = [
    "교민", "동포", "한인", "응원", "재외", "한마음", "교포",
    "한인회", "대표팀 환영",
]

# 연예 가십 (사생활·외모) — 동포 현장 가드 없으면 강한 감점
GOSSIP_KEYWORDS = [
    "열애", "열애설", "혼인신고", "결혼설", "이혼설", "파경", "재혼", "♥",
    "몸매", "극세사", "민소매", "비키니", "꿀벅지", "각선미", "s라인",
    "화보", "심쿵", "근황 공개", "사복 패션", "미모",
]
ENT_DONGPO_GUARD = [
    "교민", "동포", "한인", "한인회", "재외", "교포", "현지",
    "한인 사회", "동포 커뮤니티", "한인 팬",
]


# ── 유틸 ──────────────────────────────────────────────────────────────────────

def _normalize(title: str) -> str:
    t = unicodedata.normalize("NFC", title)
    t = re.sub(r"[^\w\s]", "", t, flags=re.UNICODE)
    t = re.sub(r"\s+", " ", t).strip().lower()
    return t


def _sim(a: str, b: str) -> float:
    sa, sb = set(a.split()), set(b.split())
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def dedup(items: list[dict], threshold: float = 0.45) -> list[dict]:
    """
    유사 제목을 묶되 버리지 않음. 대표 1건에 묶음 정보 기록.
    - dup_count: 묶인 총 매체 수 (대표 포함)
    - dup_media: 묶인 매체명 리스트
    임계값 0.45: 같은 사건 다른 매체(在日 차별 소송 4건 등)를 한 묶음으로.
    너무 낮으면 다른 기사 오묶음 위험 → 결과 보고 0.5로 조정 가능.
    """
    kept, norms = [], []
    for item in items:
        n = _normalize(item["title"])
        matched = False
        for i, p in enumerate(norms):
            if _sim(n, p) >= threshold:
                # 기존 대표에 묶음 — 매체 추가
                rep = kept[i]
                rep.setdefault("dup_media", [rep.get("media", "")])
                m = item.get("media", "")
                if m and m not in rep["dup_media"]:
                    rep["dup_media"].append(m)
                rep["dup_count"] = len(rep["dup_media"])
                matched = True
                break
        if not matched:
            item.setdefault("dup_media", [item.get("media", "")])
            item["dup_count"] = 1
            kept.append(item)
            norms.append(n)
    return kept


def apply_country_cap(items: list[dict], cap: int) -> list[dict]:
    buckets: dict[str, list] = defaultdict(list)
    for item in items:
        buckets[item["country"]].append(item)
    result = []
    for country, group in buckets.items():
        c = COUNTRY_CAP_OVERRIDE.get(country, cap)   # 국가별 예외 우선
        result.extend(group[:c])
    return result


def supplement(shortlist: list[dict], all_items: list[dict], target: int) -> list[dict]:
    needed = target - len(shortlist)
    if needed <= 0:
        return shortlist
    used_ids = {id(x) for x in shortlist}
    stale = sorted(
        [x for x in all_items if x.get("old") and id(x) not in used_ids],
        key=lambda x: x.get("pubDate") or "",
        reverse=True,
    )
    norms = [_normalize(x["title"]) for x in shortlist]
    added = 0
    for item in stale:
        if added >= needed:
            break
        n = _normalize(item["title"])
        if not any(_sim(n, p) >= 0.6 for p in norms):
            shortlist.append(item)
            norms.append(n)
            added += 1
    return shortlist


# ── 신규성 감쇠 (seen ledger) ────────────────────────────────────────────────

def load_seen() -> dict:
    try:
        with open(SEEN_PATH, encoding="utf-8-sig") as f:   # utf-8-sig: BOM 있어도 처리
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError, OSError):
        return {}   # 깨진 seen.json은 무시하고 빈 상태로 — 파이프라인 중단 방지


def seen_penalty(last_date_str: str) -> int:
    try:
        last = datetime.date.fromisoformat(last_date_str)
    except (ValueError, TypeError):
        return 0
    days_since = (TODAY - last).days
    if days_since < 0 or days_since >= SEEN_WINDOW:
        return 0
    frac = (SEEN_WINDOW - days_since) / SEEN_WINDOW
    return -round(SEEN_MAX_PEN * frac)


def prune_seen(seen: dict) -> dict:
    out = {}
    for key, date_str in seen.items():
        try:
            last = datetime.date.fromisoformat(date_str)
        except (ValueError, TypeError):
            continue
        if (TODAY - last).days < SEEN_WINDOW:
            out[key] = date_str
    return out


# ── 제외 판정 (1차 블랙리스트) ───────────────────────────────────────────────

_COMPILED_TITLE_RE  = [re.compile(p, re.IGNORECASE) for p in EXCLUDE_TITLE_RE]
_COMPILED_PROMO_RE  = [re.compile(p, re.IGNORECASE) for p in PROMO_TITLE_RE]


def is_excluded(item: dict) -> tuple[bool, str]:
    media = item.get("media", "")
    title = item.get("title", "")
    for substr in EXCLUDE_MEDIA_SUBSTR:
        if substr.lower() in media.lower():
            return True, f"매체 패턴: {substr}"
    for rx in _COMPILED_TITLE_RE:
        if rx.search(title):
            return True, f"제목 패턴: {rx.pattern[:40]}"
    return False, ""


# ── 점수 계산 (화이트리스트 핵심) ────────────────────────────────────────────

def _clean_title(title_raw: str) -> str:
    """Google News RSS의 ' - 매체명' suffix 제거."""
    return re.sub(r"\s*-\s*[^-]+$", "", title_raw).strip()


def score_item(item: dict) -> dict:
    """
    화이트리스트 점수 산출.
    반환: {score, signal, is_korea_pol, is_kpop_gossip}
      signal: 'strong' / 'hub' / 'general' / 'none'  (어떤 동포 신호로 통과했나)
    """
    title_raw = item.get("title") or ""
    title = _clean_title(title_raw)
    tl = title.lower()
    score = 0
    signal = "none"

    # ── 화이트리스트 가점 ──
    # 1) 강한 동포 신호 (+5)
    strong_hit = any(kw in title or kw.lower() in tl for kw in DONGPO_STRONG)
    if strong_hit:
        score += 5
        signal = "strong"

    # 2) 한인 거점 지명 + 한국인 정황 (+3)
    hub_hit = any(kw in title or kw.lower() in tl for kw in KOREATOWN_HUBS)
    hub_guard = any(kw in title or kw.lower() in tl for kw in HUB_KR_GUARD)
    if hub_hit and hub_guard and not strong_hit:
        score += 3
        signal = "hub"

    # 3) 디아스포라 일반 (+1, 이관 그물)
    if signal == "none":
        if any(kw in title or kw.lower() in tl for kw in DIASPORA_GENERAL):
            score += 1
            signal = "general"

    # 4) 교민 생활 사건·사고 (+2) — 동포/거점 신호와 함께일 때만 강화
    if signal in ("strong", "hub"):
        if any(kw in title or kw.lower() in tl for kw in LIFE_INCIDENT):
            score += 2

    # ── 감점 ──
    # 홍보성
    if any(rx.search(title_raw) for rx in _COMPILED_PROMO_RE):
        score -= 3

    # 스포츠 (동포 가드 없으면)
    sports_hit = any(kw in title or kw.lower() in tl for kw in SPORTS_KEYWORDS)
    sports_guard = any(kw in title or kw.lower() in tl for kw in SPORTS_DONGPO_GUARD)
    if sports_hit and not sports_guard:
        score -= 5   # 화이트리스트 가점을 확실히 상쇄

    # 연예 가십 (동포 가드 없으면)
    gossip_hit = any(kw in title or kw.lower() in tl for kw in GOSSIP_KEYWORDS)
    ent_guard = any(kw in title or kw.lower() in tl for kw in ENT_DONGPO_GUARD)
    is_kpop_gossip = gossip_hit and not ent_guard
    if is_kpop_gossip:
        score -= 10

    # 한반도·고국 정치 → 이관 강제 플래그
    is_korea_pol = any(kw.lower() in tl for kw in KOREA_POL_KEYWORDS)

    # ── 여러 매체 중복 보도 가점 (묶인 매체 수) ──
    # 같은 사건을 N개 매체가 보도 = 발제 가치 신호. 단 과대평가 방지로 상한 +3.
    dup_count = item.get("dup_count", 1)
    if dup_count >= 2:
        score += min(dup_count - 1, 3)   # 2매체 +1, 3매체 +2, 4+ 매체 +3 상한

    return {
        "score": score,
        "signal": signal,
        "is_korea_pol": is_korea_pol,
        "is_kpop_gossip": is_kpop_gossip,
    }


# ── 데스크 분류 ───────────────────────────────────────────────────────────────
# 화이트리스트: 리스트 진입 = 동포 신호 필수 (score >= LIST_SCORE_THRESHOLD)
LIST_SCORE_THRESHOLD = 3   # +3 이상 = strong(+5) 또는 hub(+3). general(+1)은 미달→이관
TRAN_SCORE_MIN       = 1   # v3.1: 0→1. 이관도 동포 신호(+1 이상) 필수.
                           # 신호 0(none)은 광고·일반뉴스 전부 제외. (산양유·쿠첸 제거)
LIST_CAP  = 20             # 진단 단계: 넉넉히. 실제 통과분 보고 조정
TRAN_CAP  = 30             # v3.2: 15→30. 이관을 검토용 그물로 — 발행인이 눈으로 훑는 풀
LIST_COUNTRY_CAP = 4       # v5: 국가별 리스트 상한. 한 나라가 리스트 독식 방지.
                           # 일본 재일 기사가 리스트 7/10을 먹던 편중 해소 목적.
                           # 초과분은 버리지 않고 이관으로 강등(발제 회의에서 볼 수 있게).


def classify(shortlist: list[dict], seen: dict) -> list[dict]:
    entries = []
    for idx, item in enumerate(shortlist):
        excluded, reason = is_excluded(item)
        if excluded:
            entries.append({
                "idx": idx, "score": -99, "desk": "제외",
                "signal": "none", "is_korea_pol": False,
                "reason": reason,
            })
            continue

        r = score_item(item)
        s = r["score"]

        # 신규성 감쇠
        seen_key = _normalize(item.get("title", ""))
        pen = seen_penalty(seen.get(seen_key, ""))
        s += pen

        entries.append({
            "idx": idx, "score": s, "desk": None,
            "signal": r["signal"],
            "is_korea_pol": r["is_korea_pol"],
            "is_kpop_gossip": r["is_kpop_gossip"],
            "seen_pen": pen,
            "reason": "",
        })

    active = [e for e in entries if e["desk"] != "제외"]
    active.sort(key=lambda e: e["score"], reverse=True)

    list_count = 0
    tran_count = 0
    country_list_count: dict[str, int] = defaultdict(int)   # v5: 국가별 리스트 카운터
    for e in active:
        s = e["score"]
        is_korea_pol = e.get("is_korea_pol", False)
        country = shortlist[e["idx"]].get("country", "")

        # 한반도 정치는 점수 무관 이관 강제
        if is_korea_pol and s < LIST_SCORE_THRESHOLD:
            if tran_count < TRAN_CAP:
                e["desk"] = "이관"; tran_count += 1
            else:
                e["desk"] = "제외"
        # 화이트리스트 통과 → 리스트 (단 국가별 상한 준수)
        elif s >= LIST_SCORE_THRESHOLD and list_count < LIST_CAP:
            if country_list_count[country] < LIST_COUNTRY_CAP:
                e["desk"] = "리스트"
                list_count += 1
                country_list_count[country] += 1
            else:
                # v5: 국가 상한 초과 — 버리지 않고 이관으로 강등
                e["desk"] = "이관" if tran_count < TRAN_CAP else "제외"
                e["country_capped"] = True   # 진단용 플래그
                if e["desk"] == "이관":
                    tran_count += 1
        # 동포 신호 약하지만 0 이상 → 이관 그물
        elif s >= TRAN_SCORE_MIN and tran_count < TRAN_CAP:
            e["desk"] = "이관"; tran_count += 1
        else:
            e["desk"] = "제외"

    for e in entries:
        if e["desk"] is None:
            e["desk"] = "제외"

    # 리스트 최고점 1건 → 주목
    list_entries = [e for e in entries if e["desk"] == "리스트"]
    if list_entries:
        top = max(list_entries, key=lambda e: e["score"])
        top["desk"] = "주목"

    # seen 갱신
    for e in entries:
        if e["desk"] in ("주목", "리스트"):
            key = _normalize(shortlist[e["idx"]].get("title", ""))
            if key:
                seen[key] = TODAY_STR

    return entries


# ── curation.json 조립 ────────────────────────────────────────────────────────

def build_curation(shortlist: list[dict], entries: list[dict]) -> list[dict]:
    rank_counter: dict[str, int] = defaultdict(int)
    curation = []
    for e in sorted(entries, key=lambda x: (-x["score"], x["idx"])):
        desk = e["desk"]
        rank_counter[desk] += 1
        item = shortlist[e["idx"]]
        title = item.get("title", "")[:60]
        media = item.get("media", "")
        curation.append({
            "id": e["idx"],
            "rank": rank_counter[desk],
            "desk": desk,
            "one_liner": f"{title} / {media}",
            "caution": "",
            "bundle_idea": "",
        })
    return curation


# ── 메인 ─────────────────────────────────────────────────────────────────────

def main():
    with open(CANDIDATES_PATH, encoding="utf-8") as f:
        all_items: list[dict] = json.load(f)

    total = len(all_items)
    if total == 0:
        print("candidates.json이 비어 있습니다 — 종료")
        return

    print(f"candidates.json: {total}건")

    fresh = [x for x in all_items if not x.get("old")]
    print(f"  old=false(신규): {len(fresh)}건")
    fresh = dedup(fresh)
    print(f"  중복 제거 후: {len(fresh)}건")
    fresh = apply_country_cap(fresh, COUNTRY_CAP)
    print(f"  국가 캡({COUNTRY_CAP}건) 적용 후: {len(fresh)}건")

    if len(fresh) < FRESH_MIN:
        print(f"  신규 {len(fresh)}건 < {FRESH_MIN} → old=true 보충")
        fresh = supplement(fresh, all_items, FRESH_MIN)
        print(f"  보충 후: {len(fresh)}건")

    if len(fresh) > TARGET_MAX:
        fresh = fresh[:TARGET_MAX]
        print(f"  {TARGET_MAX}건으로 절삭")

    idx_map = {id(x): i for i, x in enumerate(all_items)}
    for item in fresh:
        item["_orig_id"] = idx_map.get(id(item), -1)

    with open(SHORTLIST_PATH, "w", encoding="utf-8") as f:
        json.dump(fresh, f, ensure_ascii=False, indent=2)

    from collections import Counter
    cc = Counter(x["country"] for x in fresh)
    print(f"\nshortlist.json: {len(fresh)}건 저장")
    for country, cnt in sorted(cc.items(), key=lambda x: -x[1]):
        print(f"  {country}: {cnt}건")

    seen = load_seen()
    print(f"\nseen.json: {len(seen)}건 로드 (최근 {SEEN_WINDOW}일 노출 기억)")

    entries = classify(fresh, seen)
    curation = build_curation(fresh, entries)

    with open(CURATION_PATH, "w", encoding="utf-8") as f:
        json.dump(curation, f, ensure_ascii=False, indent=2)

    seen = prune_seen(seen)
    with open(SEEN_PATH, "w", encoding="utf-8") as f:
        json.dump(seen, f, ensure_ascii=False, indent=2)
    print(f"seen.json: {len(seen)}건 저장")

    # ══════════════════════════════════════════════════════════════════════
    # 진단 출력 (v3 강화) — LIST_CAP·threshold 조정 판단용
    # ══════════════════════════════════════════════════════════════════════
    from collections import Counter as Cnt
    desk_cnt = Cnt(e["desk"] for e in entries)
    sig_cnt  = Cnt(e["signal"] for e in entries if e["desk"] != "제외")

    print(f"\n{'='*60}")
    print(f"curation.json 자동 생성 [v3 화이트리스트]")
    print(f"{'='*60}")
    for desk in ["주목", "리스트", "이관", "제외"]:
        print(f"  {desk}: {desk_cnt.get(desk, 0)}건")

    print(f"\n── 동포 신호 분포 (제외 제외) ──")
    print(f"  strong(+5): {sig_cnt.get('strong', 0)}건  "
          f"hub(+3): {sig_cnt.get('hub', 0)}건  "
          f"general(+1): {sig_cnt.get('general', 0)}건  "
          f"none: {sig_cnt.get('none', 0)}건")

    # 리스트 항목 (점수·신호 함께)
    list_items = [(e, fresh[e["idx"]]) for e in entries if e["desk"] in ("주목", "리스트")]
    list_items.sort(key=lambda x: x[0]["score"], reverse=True)
    print(f"\n── 리스트 항목 ({len(list_items)}건) ──")
    for e, item in list_items:
        flag = "⭐" if e["desk"] == "주목" else "  "
        pen = e.get("seen_pen", 0)
        pen_str = f" [신규성{pen}]" if pen else ""
        sig = e["signal"]
        dup = item.get("dup_count", 1)
        dup_str = f" [他{dup-1}개매체]" if dup >= 2 else ""
        print(f"  {flag} [{item['country']}] ({sig}/{e['score']}{pen_str}{dup_str}) {item['title'][:50]}")

    # 이관 항목
    tran_items = [(e, fresh[e["idx"]]) for e in entries if e["desk"] == "이관"]
    tran_items.sort(key=lambda x: x[0]["score"], reverse=True)
    print(f"\n── 이관 항목 ({len(tran_items)}건) ──")
    for e, item in tran_items:
        tag = "[정치]" if e.get("is_korea_pol") else ""
        sig = e["signal"]
        print(f"    {tag}[{item['country']}] ({sig}/{e['score']}) {item['title'][:50]}")

    # 제외 중 동포 신호가 있었는데 떨어진 것 — 놓침 점검용
    missed = [(e, fresh[e["idx"]]) for e in entries
              if e["desk"] == "제외" and e.get("signal", "none") != "none"]
    if missed:
        print(f"\n── ⚠ 제외됐지만 동포 신호 있던 항목 ({len(missed)}건) — 놓침 점검 ──")
        for e, item in missed[:15]:
            print(f"    ({e['signal']}/{e['score']}) {item['title'][:50]}")

    # ══════════════════════════════════════════════════════════════════════
    # v5 신설: 비주력 국가 진단 — 왜 리스트에 안 오르는가
    #   두 관문(fresh × strong)을 국가별로 갈라 원인 특정.
    #   candidates 전체를 봐야 하므로 all_items 기준으로 재집계.
    # ══════════════════════════════════════════════════════════════════════
    print(f"\n{'='*60}")
    print("국가별 관문 통과 진단 (왜 리스트에 안 오르는가)")
    print(f"{'='*60}")

    from collections import defaultdict as dd
    stat = dd(lambda: {"수집": 0, "fresh": 0, "strong_fresh": 0, "리스트": 0})

    # 1) candidates 전체에서 수집·fresh 집계
    for item in all_items:
        c = item.get("country", "?")
        stat[c]["수집"] += 1
        if not item.get("old"):
            stat[c]["fresh"] += 1

    # 2) fresh(shortlist) 중 strong/hub 신호 받은 건수
    for e in entries:
        item = fresh[e["idx"]]
        c = item.get("country", "?")
        if e.get("signal") in ("strong", "hub"):
            stat[c]["strong_fresh"] += 1
        if e["desk"] in ("주목", "리스트"):
            stat[c]["리스트"] += 1

    print(f"  {'국가':<10} {'수집':>5} {'fresh':>6} {'동포신호':>7} {'리스트':>6}   병목")
    print(f"  {'-'*10} {'-'*5} {'-'*6} {'-'*7} {'-'*6}   {'-'*20}")
    for c in sorted(stat, key=lambda x: -stat[x]["수집"]):
        s = stat[c]
        # 병목 진단
        if s["리스트"] > 0:
            bottleneck = "통과"
        elif s["수집"] == 0:
            bottleneck = "수집 0 — 피드 점검"
        elif s["fresh"] == 0:
            bottleneck = "fresh 0 — 발행빈도 낮음(STALE)"
        elif s["strong_fresh"] == 0:
            bottleneck = "동포신호 0 — 현지 일반뉴스뿐"
        else:
            bottleneck = "점수 미달 or 국가상한"
        print(f"  {c:<10} {s['수집']:>5} {s['fresh']:>6} {s['strong_fresh']:>7} {s['리스트']:>6}   {bottleneck}")

    # 국가 상한으로 강등된 항목
    capped = [(e, fresh[e["idx"]]) for e in entries if e.get("country_capped")]
    if capped:
        print(f"\n── 국가 상한({LIST_COUNTRY_CAP}건) 초과로 이관 강등 ({len(capped)}건) ──")
        for e, item in capped[:10]:
            print(f"    [{item['country']}] ({e['signal']}/{e['score']}) {item['title'][:45]}")


if __name__ == "__main__":
    main()
