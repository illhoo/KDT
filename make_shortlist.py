#!/usr/bin/env python3
"""
KDT 야간 모니터링 — shortlist + 규칙 기반 curation 자동 생성

candidates.json
  → shortlist.json  (신규 우선 + 중복 제거 + 국가 캡, 80~100건)
  → curation.json   (규칙 기반 자동 분류 — 모델 개입 없음)

[v2 신규성 감쇠]
  seen.json 누적 → 최근 리스트/주목에 올린 제목은 점수 감점(소프트 강등)
  완전 차단 아님. 7일 선형 감쇠. 새 기사 없으면 다시 올라올 수 있음.
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

COUNTRY_CAP = 30   # 국가당 최대 건수
TARGET_MAX  = 100  # shortlist 목표 상한
FRESH_MIN   = 30   # 신규가 이 미만이면 old=true 보충

# ── 신규성 감쇠 설정 ──────────────────────────────────────────────────────────
SEEN_WINDOW   = 7   # 최근 N일 내 노출 항목만 감점 대상
SEEN_MAX_PEN  = 5   # 최대 감점폭(노출 직후). 날짜 지날수록 선형 감소.

# KST 기준 오늘
_KST = datetime.timezone(datetime.timedelta(hours=9))
TODAY = datetime.datetime.now(_KST).date()
TODAY_STR = TODAY.isoformat()

# ── 제외 규칙 ─────────────────────────────────────────────────────────────────
# 매체명에 이 문자열이 포함되면 무조건 제외
EXCLUDE_MEDIA_SUBSTR = [
    "Hot Deal",
    "AERA DIGITAL",   # 일본 잡지 — 교민 무관 일반 콘텐츠
]

# 제목에 이 패턴이 있으면 제외 (re.search, IGNORECASE)
EXCLUDE_TITLE_RE = [
    # 부동산 광고 패턴
    r"\d+br\s*\d+ba", r"free\s*no소셜", r"유학생\s*ok", r"보증금.*구입",
    r"밴조선\s*부동산",
    # 기업 실적·IR·보도자료 (주로 영문)
    r"\bearnings\s+(call|report)", r"\bfinancial results\b", r"\bfiscal\s+20\d\d\b",
    r"\bsecures\s+retail\b", r"\bgrants?\s+due diligence\b", r"\blifts\s+profit\b",
    r"\bboosts?\s+dividend\b", r"\bcapital\s+markets?\s+day\b",
    r"\bexecutives?\s+boost\s+holdings\b", r"\bfirst-year results\b",
    r"\brecord-breaking\s+first\s+half\b", r"\bnew\s+york\s+capital\b",
    r"\bprojects?\s+record\b", r"\bdue diligence\b",
    # 코인·주식 종목
    r"\bsolana\b", r"\bbitcoin\b", r"\bcrypto\b", r"암호화폐",
    r"trading at its lowest", r"\bdividend\s+etf\b", r"\bonly buy one.*etf\b",
    # 일반 월드컵·스포츠 경기 결과 (교민·한국 무관)
    r"무승부에\s*환호", r"빈\s*좌석", r"\d+대\d+\s*무승부",
    r"キックオフ", r"선행\s*逃切",  # 일본어 경기 기사
    # 특정국 vs 특정국 경기 스코어 (한국 없는 경우)
    r"(체코|남아공|에콰도르|코트디부아르|네덜란드|독일|스페인|브라질|아르헨티나).*(전으로|생존전|대결|8강|16강)",
    # 일본어 스포츠 결과 (W杯, 惨敗 등 교민 무관)
    r"W杯.*惨敗", r"W杯.*涙", r"W杯.*真相",
    r"サッカー.*W杯", r"日蘭",  # 일본-네덜란드 경기
    # 일반 일본 내정·일왕·자위대 (교민 무관)
    r"両陛下", r"天皇", r"自衛隊",
    # 쇼핑몰·광고 한국어 키워드
    r"고국배송", r"파더스데이\s*선물.*끝판왕",
    # 기업 홍보성
    r"welcomes the launches?\b", r"\bIPO\b", r"listing.*new\s*york",
    # PC·전자제품 판매 광고 (영문 제목)
    r"\b(gaming|editing)\s+pc\b", r"\bpowerful\s+gaming\b",
    r"안마의자.*(단\s*\$|\₩|\d+만원)", r"끝판왕.*\$",
    # 여행 광고성
    r"인생\s*라운드", r"절벽과\s*바다.*라운드", r"리조트.*특가", r"골프.*투어.*홍보",
    # 교민 무관 일반 국제뉴스 (갱단, 이란 속보 등 단순 속보)
    r"갱단에\s*사실상\s*국가\s*마비",
    r"호르무즈\s*(통행료|해협\s*개방)",  # 이란 종전 단순 속보
    # K팝·연예 순수 가십 (동포 연결 없음) — 예능 프로그램 태그·섹션 태그
    r"숏폼\]", r"\[O!\s*STAR\s*숏폼\]", r"\[O!\s*STAR\b",
    r"\[핫피플\]", r"\[순간포착\]", r"\[Oh!llywood\]", r"\[Oh!llywood\b",
    r"\('카더정원'\)", r"\('아근진'\)", r"\('조선의\s*사랑꾼'\)",
    r"근황.*PT다녀", r"\d+kg\s*감량.*근황",
    r"^\[사진\]",  # [사진] 단독 사진 기사
    r"\[사진\]\s*'하느님의\s*품'",
    # 연예 가십 일반 패턴 (결혼·연애·체중 등 사생활)
    r"결혼\s*생각\s*없었다", r"♥.*결혼", r"kg\s*쪘",

    # ── v2 추가: 영문 증시·환율 와이어 (Globe and Mail 노이즈) ──
    # 주의: 영문 패턴만 사용 → 한국어 유가·물가·환율 기사는 안 걸림
    r"\bUS stocks\b", r"\bAsian shares\b", r"\bWorld shares\b",
    r"\bWall Street\b", r"\bpremarket\b", r"\bin (mixed|thin) (trading|holiday)\b",
    r"\bGrowth Stocks?\b", r"\bTrillion Club\b", r"\$\d+\s*Billion Valuation",
    r"\bBuy (the Dip|in Right Now|on the Dip)\b", r"\bStock Investors?\b",
    r"\bReceives? (a |an )?(Buy|Sell|Hold)\b", r"\bRemains a (Buy|Sell|Hold)\b",
    r"\bNew Buy Recommendation\b", r"\bNAV Price History\b",
    r"\bFOREX:\s", r"\bKRW[A-Z]{3}\b", r"\b[A-Z]{3}KRW\b",
    r"\bETFs? (Let You|to Buy)\b", r"\bonce-?in-?a-?decade opportunity\b",
    r"\bPublic Equity Offering\b", r"\breceives new orders\b",
    # ── v2.1 보강: Korea 포함 증시 기사 차단 (디아스포라 가중 전에 제외) ──
    r"\bStock Market Today\b", r"\bJitters\b", r"\b(Global |Tech )?Pullback\b",
    r"\b(tech|big tech)[- ]?(sell-?off|selloff)\b", r"\bsell-?off\b",
    r"\bStock (Suddenly )?Crashed\b", r"\bShares? (Crashed|Plummet|Obliterated)\b",
    r"\bStock Is (Trading Lower|Nosediving)\b", r"\bStocks? Trade Down\b",
    r"\bShares Are Falling\b", r"\bStock Slide\b", r"\bMemory Selloff\b",
    r"\bWatch These \d+ Things\b", r"\bWhen It Reports Earnings\b",
    r"\bDow (Green|Jones)\b", r"\bNasdaq\b", r"\bS&P 500\b",
    r"\([A-Z]{2,5}\)\s+(Stock|Shares)\b",  # 종목코드 (AMD)/(MU) 등 + Stock/Shares

    # ── v2 추가: 외신 비(非)동포 스포츠 (영문 — 캐나다 CBC/Globe) ──
    # 주의: 한국어 '응원·교민·동포' 스포츠는 score_item에서 별도 보호
    r"\bVolleyball Nations League\b", r"\bPentathlon World Cup\b",
    r"\bWorld Boxing Cup\b", r"\bSoftball World Cup\b",
    r"\bNations Cup\b", r"\bChampionship Tour\b",

    # ── v2 추가: 상품 광고 화법 (가격·숫자 아님, '파는 말투'만) ──
    r"한\s*통이면", r"오래오래", r"단\s*한\s*번에", r"이거\s*하나면",
    r"스피드\s*염색", r"한\s*병으로", r"평생\s*무료", r"무료\s*체험",
]

# ── 동포 관련 가중 키워드 ─────────────────────────────────────────────────────
# 제목에 포함될수록 리스트 우선도 증가 (한국어 + 일본어)
DONGPO_KEYWORDS = [
    # 한국어
    "동포", "교민", "재외국민", "재외동포", "영사", "비자", "시민권",
    "이민", "영주권", "귀화", "유학생", "한인", "재일", "재미", "재캐",
    "재호", "재베", "한국인", "동포청", "이달의 재외동포", "한인회",
    "한인 사회", "한인 커뮤니티", "한인 행사", "교포", "이민자",
    # 일본어 동포·한국 관련 (일본 소스 기사 가중)
    "在日", "在韓", "韓国人", "韓国系", "コリアン", "朝鮮人",
    "領事", "永住", "移民", "ビザ", "帰化",
    # 일본어 한국 키워드 (재일동포 관련 맥락 포착)
    "在日韓国", "在日朝鮮", "韓国籍",
]

# ── 홍보성·자사 실적 감산 키워드 ─────────────────────────────────────────────
# 이 패턴이 있으면 score -2 (리스트 상위 방지, 이관/제외로 밀림)
PROMO_TITLE_RE = [
    r"독보적\s*존재감", r"순익\s*달성", r"\d+억\s*순익",
    r"1분기.*순익", r"해외서.*존재감",
    r"\brecord\b.*\bfirst\s+half\b", r"\bstrong\s+membership\b",
]

# 주제별 가중 (정책·법률·사건·교민 생활)
POLICY_KEYWORDS = [
    # 체류·신분
    "비자", "영주권", "시민권", "귀화", "이민법", "추방", "체류",
    "영사관", "대사관", "외교부", "동포청", "재외선거", "투표권",
    "이민국", "입국", "출국", "단속",
    # 법·사건
    "체포", "징역", "판결", "소송",
    # 교민 생활·금융·의료
    "보험", "의료", "요양원", "의료비", "주택", "렌트", "세금", "지원금",
    "퇴직연금", "401", "연금", "학자금", "등록금",
    # 교육
    "유학생", "sat", "act", "입시", "입학", "대학원",
    # 사건사고 (교민 거주지)
    "산불", "홍수", "지진", "허리케인", "토네이도",
]

# 한반도·고국 정치 키워드 (교민 관심사이나 본토 사안 → 이관)
# score를 올리지 않고 이관 가산만 부여 (별도 플래그로 처리)
KOREA_POL_KEYWORDS = [
    "서울시장", "대통령", "국회", "총선", "대선", "한반도",
    "북한", "미북", "남북", "비핵화", "종전", "평화협정",
    "윤석열", "이재명", "한덕수",
]

# K팝·연예 감지
KPOP_ENT_KEYWORDS = [
    "k팝", "k-pop", "케이팝", "아이돌", "걸그룹", "보이그룹",
    "배우", "가수", "드라마", "뮤직비디오", "싱글", "앨범", "팬클럽",
    "팬미팅", "숏폼", "데뷔", "컴백", "콘서트", "뮤지컬",
    "シングル", "デビュー", "アイドル",  # 일본어 연예
]

# K팝·연예 중 동포 현지 연결 → 리스트로 승격
KPOP_LOCAL_KEYWORDS = [
    "공연", "내한", "월드투어", "투어", "현지", "교포", "해외 팬",
    "동포 커뮤니티", "한인 팬", "팬 행사",
]

# ── v2 추가: 스포츠 감점 키워드 (경기 결과·선수 개인 기록) ──
# 단, 아래 SPORTS_DONGPO_GUARD 키워드가 있으면 감점 면제 (교민 응원 등 보호)
SPORTS_KEYWORDS = [
    # 한국어
    "야구", "축구", "골프", "배구", "농구", "수영", "펜싱", "체조",
    "올스타", "MVP", "홈런", "타율", "완봉", "선발", "결승", "예선",
    "리그", "월드컵", "경기", "감독", "구단", "프로", "우승", "출장",
    # 일본어
    "野球", "サッカー", "ゴルフ", "リーグ", "完封", "本塁打", "勝目",
    "W杯", "選手権", "ホッケー", "サーフィン", "スイミング", "フェンシング",
    # 영문
    "World Cup", "League", "Cup Final", "Semifinal", "knockout stage",
]

# 스포츠라도 이 키워드가 있으면 동포 콘텐츠로 보고 감점 면제
SPORTS_DONGPO_GUARD = [
    "교민", "동포", "한인", "응원", "재외", "한마음", "교포",
    "한인회", "한인 사회", "현지 동포", "대표팀 환영",
]

# ── v2 추가: 외신 디아스포라 가중 ──
# 제외(증시·스포츠·광고) 통과 후에만 적용되므로 증시 기사는 되살아나지 않음.
# 한인 직결(+3) vs 디아스포라 일반(+1) 차등.
DIASPORA_KR_KEYWORDS = [  # 한인 직결 — 동포 키워드와 동급 가중(+3)
    "korea", "korean", "overseas korean", "korean american",
    "korean canadian", "korean australian", "ethnic korean",
    "한국", "한인", "재외", "교민", "재미", "재일", "재캐", "재호",
]
DIASPORA_GENERAL_KEYWORDS = [  # 타국 디아스포라·이민 일반 — 약한 가중(+1)
    "diaspora", "immigrant community", "overseas community",
    "expatriate", "migrant community", "디아스포라", "이민 사회",
    "이민자 공동체", "동포사회",
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


def dedup(items: list[dict], threshold: float = 0.6) -> list[dict]:
    kept, norms = [], []
    for item in items:
        n = _normalize(item["title"])
        if not any(_sim(n, p) >= threshold for p in norms):
            kept.append(item)
            norms.append(n)
    return kept


def apply_country_cap(items: list[dict], cap: int) -> list[dict]:
    buckets: dict[str, list] = defaultdict(list)
    for item in items:
        buckets[item["country"]].append(item)
    result = []
    for group in buckets.values():
        result.extend(group[:cap])
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
    """seen.json 로드. 없거나 깨졌으면 빈 dict. {정규화제목: 'YYYY-MM-DD'}"""
    try:
        with open(SEEN_PATH, encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def seen_penalty(last_date_str: str) -> int:
    """
    마지막 노출일 기준 감점값(음수) 반환.
    노출 직후 -SEEN_MAX_PEN, 날짜 지날수록 선형 감소, SEEN_WINDOW일 이후 0.
    """
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
    """SEEN_WINDOW 넘은 항목 제거 — 파일 무한 증식 방지."""
    out = {}
    for key, date_str in seen.items():
        try:
            last = datetime.date.fromisoformat(date_str)
        except (ValueError, TypeError):
            continue
        if (TODAY - last).days < SEEN_WINDOW:
            out[key] = date_str
    return out


# ── 제외 판정 ─────────────────────────────────────────────────────────────────

_COMPILED_TITLE_RE  = [re.compile(p, re.IGNORECASE) for p in EXCLUDE_TITLE_RE]
_COMPILED_PROMO_RE  = [re.compile(p, re.IGNORECASE) for p in PROMO_TITLE_RE]


def is_excluded(item: dict) -> tuple[bool, str]:
    """(제외 여부, 이유) 반환."""
    media = item.get("media", "")
    title = item.get("title", "")

    for substr in EXCLUDE_MEDIA_SUBSTR:
        if substr.lower() in media.lower():
            return True, f"매체 패턴: {substr}"

    for rx in _COMPILED_TITLE_RE:
        if rx.search(title):
            return True, f"제목 패턴: {rx.pattern[:40]}"

    return False, ""


# ── 점수 계산 ─────────────────────────────────────────────────────────────────

def score_item(item: dict) -> tuple[int, bool, bool, bool]:
    """
    (score, is_kpop_ent, kpop_local, is_korea_pol) 반환.
    score: 높을수록 리스트 우선.
    is_kpop_ent: K팝·연예 감지 여부.
    kpop_local: 동포 현지 연결 여부.
    is_korea_pol: 한반도·고국 정치 → 이관 강제.
    """
    title_raw = item.get("title") or ""
    # " - 매체명" suffix 제거 (Google News RSS가 제목에 매체명 붙임)
    title = re.sub(r"\s*-\s*[^-]+$", "", title_raw).strip()
    title_lower = title.lower()
    score = 0

    # 동포 키워드 가중 (한국어·일본어 통합) — 정제된 title에서만 검색
    dongpo_hit = any(kw in title or kw.lower() in title_lower for kw in DONGPO_KEYWORDS)
    if dongpo_hit:
        score += 3

    # 교민 생활·정책·법률 키워드 가중
    if any(kw.lower() in title_lower for kw in POLICY_KEYWORDS):
        score += 2

    # 홍보성·자사 실적 감산
    if any(rx.search(title_raw) for rx in _COMPILED_PROMO_RE):
        score -= 2

    # 한국 발 기사는 교민 직접 관련도 낮으면 소폭 감점
    if item.get("country") == "한국" and score == 0:
        score -= 1

    # K팝·연예 여부
    is_kpop = any(kw.lower() in title_lower for kw in KPOP_ENT_KEYWORDS)
    kpop_local = is_kpop and any(kw.lower() in title_lower for kw in KPOP_LOCAL_KEYWORDS)
    if kpop_local:
        score += 2

    # ── v2 추가: 스포츠 감점 (단, 동포 가드 키워드 있으면 면제) ──
    sports_hit = any(kw in title or kw.lower() in title_lower for kw in SPORTS_KEYWORDS)
    dongpo_guard = any(kw in title or kw.lower() in title_lower for kw in SPORTS_DONGPO_GUARD)
    if sports_hit and not dongpo_guard:
        score -= 2

    # ── v2 추가: 외신 디아스포라 가중 (제외 통과분에만 적용) ──
    # 동포 키워드(DONGPO)에서 이미 +3 받은 경우 중복 가중 방지.
    if not dongpo_hit:
        if any(kw in title or kw.lower() in title_lower for kw in DIASPORA_KR_KEYWORDS):
            score += 3   # 한인 직결 외신
        elif any(kw in title or kw.lower() in title_lower for kw in DIASPORA_GENERAL_KEYWORDS):
            score += 1   # 타국 디아스포라·이민 일반

    # 한반도·고국 정치 → 이관 강제 (점수에 관계없이)
    is_korea_pol = any(kw.lower() in title_lower for kw in KOREA_POL_KEYWORDS)

    return score, is_kpop, kpop_local, is_korea_pol


# ── 데스크 분류 ───────────────────────────────────────────────────────────────

LIST_SCORE_THRESHOLD = 1   # v2: 2→1 하향. 추천 건수 확대 (동포 키워드 약하게라도 있으면 리스트)
TRAN_SCORE_MIN       = -1  # 이 미만이면 이관 아니라 제외 (잡음 방지)
LIST_CAP  = 18
TRAN_CAP  = 12


def classify(shortlist: list[dict], seen: dict) -> list[dict]:
    """
    shortlist → curation 항목 리스트 반환.
    desk: 주목 / 리스트 / 이관 / 제외
    seen: 신규성 감쇠 ledger. 이 함수가 오늘 리스트/주목 항목을 seen에 기록(in-place).
    """
    entries = []
    for idx, item in enumerate(shortlist):
        excluded, reason = is_excluded(item)
        if excluded:
            entries.append({
                "idx": idx, "score": -99, "desk": "제외",
                "is_kpop": False, "kpop_local": False,
                "reason": reason,
            })
            continue

        s, is_kpop, kpop_local, is_korea_pol = score_item(item)

        # ── 신규성 감쇠: 최근 리스트/주목에 올린 제목이면 소프트 강등 ──
        seen_key = _normalize(item.get("title", ""))
        pen = seen_penalty(seen.get(seen_key, ""))
        s += pen

        entries.append({
            "idx": idx, "score": s, "desk": None,
            "is_kpop": is_kpop, "kpop_local": kpop_local,
            "is_korea_pol": is_korea_pol,
            "seen_pen": pen,
            "reason": "",
        })

    # 점수 내림차순 정렬 (제외 제외)
    active = [e for e in entries if e["desk"] != "제외"]
    active.sort(key=lambda e: e["score"], reverse=True)

    # 리스트 / 이관 / 제외 배정
    list_count = 0
    tran_count = 0
    for e in active:
        s = e["score"]
        is_kpop = e["is_kpop"]
        is_korea_pol = e.get("is_korea_pol", False)

        # 한반도·고국 정치는 점수에 관계없이 이관 강제
        if is_korea_pol and s < LIST_SCORE_THRESHOLD:
            if tran_count < TRAN_CAP:
                e["desk"] = "이관"
                tran_count += 1
            else:
                e["desk"] = "제외"
        elif s >= LIST_SCORE_THRESHOLD and list_count < LIST_CAP:
            e["desk"] = "리스트"
            list_count += 1
        elif is_kpop and not e["kpop_local"]:
            # 순수 K팝·연예 (현지 연결 없음) — score 충분히 낮으면 제외
            if s < TRAN_SCORE_MIN:
                e["desk"] = "제외"
            elif tran_count < TRAN_CAP:
                e["desk"] = "이관"
                tran_count += 1
            else:
                e["desk"] = "제외"
        elif s < TRAN_SCORE_MIN:
            # 점수 너무 낮은 일반 기사 → 이관 아니라 제외
            e["desk"] = "제외"
        elif tran_count < TRAN_CAP:
            e["desk"] = "이관"
            tran_count += 1
        else:
            e["desk"] = "제외"

    # 제외 항목도 desk 확정
    for e in entries:
        if e["desk"] is None:
            e["desk"] = "제외"

    # 리스트 중 최고점 1건 → 주목
    list_entries = [e for e in entries if e["desk"] == "리스트"]
    if list_entries:
        top = max(list_entries, key=lambda e: e["score"])
        top["desk"] = "주목"

    # ── seen 갱신: 오늘 리스트/주목에 오른 항목을 오늘 날짜로 기록 ──
    for e in entries:
        if e["desk"] in ("주목", "리스트"):
            key = _normalize(shortlist[e["idx"]].get("title", ""))
            if key:
                seen[key] = TODAY_STR

    return entries


# ── curation.json 조립 ────────────────────────────────────────────────────────

def build_curation(shortlist: list[dict], entries: list[dict]) -> list[dict]:
    # desk별 rank 카운터
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

    # ── shortlist 생성 ──────────────────────────────────────────────────────
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

    # ── 신규성 감쇠 ledger 로드 ─────────────────────────────────────────────
    seen = load_seen()
    print(f"\nseen.json: {len(seen)}건 로드 (최근 {SEEN_WINDOW}일 노출 기억)")

    # ── 규칙 기반 curation 생성 ─────────────────────────────────────────────
    entries = classify(fresh, seen)
    curation = build_curation(fresh, entries)

    with open(CURATION_PATH, "w", encoding="utf-8") as f:
        json.dump(curation, f, ensure_ascii=False, indent=2)

    # ── seen ledger 저장 (오래된 항목 정리 후) ──────────────────────────────
    seen = prune_seen(seen)
    with open(SEEN_PATH, "w", encoding="utf-8") as f:
        json.dump(seen, f, ensure_ascii=False, indent=2)
    print(f"seen.json: {len(seen)}건 저장")

    # 분류 요약
    from collections import Counter as Cnt
    desk_cnt = Cnt(e["desk"] for e in entries)
    print(f"\ncuration.json 자동 생성:")
    for desk in ["주목", "리스트", "이관", "제외"]:
        print(f"  {desk}: {desk_cnt.get(desk, 0)}건")

    # 리스트 항목 미리보기
    list_items = [(e, fresh[e["idx"]]) for e in entries if e["desk"] in ("주목", "리스트")]
    list_items.sort(key=lambda x: x[0]["score"], reverse=True)
    print(f"\n── 리스트 항목 ({len(list_items)}건) ──")
    for e, item in list_items:
        flag = "⭐" if e["desk"] == "주목" else "  "
        pen = e.get("seen_pen", 0)
        pen_str = f" [신규성{pen}]" if pen else ""
        print(f"  {flag} [{item['country']}] {item['title'][:55]} (점수:{e['score']}{pen_str})")

    tran_items = [(e, fresh[e["idx"]]) for e in entries if e["desk"] == "이관"]
    tran_items.sort(key=lambda x: x[0]["score"], reverse=True)
    print(f"\n── 이관 항목 ({len(tran_items)}건) ──")
    for e, item in tran_items:
        tag = "[연예]" if e["is_kpop"] else ("[정치]" if e.get("is_korea_pol") else "")
        print(f"    {tag}[{item['country']}] {item['title'][:55]} (점수:{e['score']})")


if __name__ == "__main__":
    main()
