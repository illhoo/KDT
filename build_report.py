#!/usr/bin/env python3
"""
KDT 야간 모니터링 — 리포트 조립·발송·push
입력:
  candidates.json   — fetch_candidates.py 출력
  curation.json     — 모델이 만든 큐레이션 판단 (아래 형식)

curation.json 형식:
[
  {
    "id": 0,                        # candidates.json 배열 인덱스
    "rank": 1,                      # 정렬 순위(낮을수록 상위)
    "desk": "주목|리스트|이관|제외", # 배치 데스크
    "one_liner": "취재 의미 1~2줄",
    "caution": "팩트체크 주의 (없으면 빈 문자열)",
    "bundle_idea": "묶음 아이디어 (있을 때만)"
  },
  ...
]
"""

import datetime
import json
import os
import re
import subprocess
import sys

import requests

# ── 날짜 (KST = UTC+9) ────────────────────────────────────────────────────────
KST = datetime.timezone(datetime.timedelta(hours=9))
TODAY = datetime.datetime.now(KST).date()
DATE_STR = TODAY.isoformat()   # "2026-06-15"

CANDIDATES_PATH = "candidates.json"
SHORTLIST_PATH  = "shortlist.json"
CURATION_PATH   = "curation.json"
SEEN_PATH       = "seen.json"
REPORT_DIR      = "reports"
REPORT_PATH     = f"{REPORT_DIR}/{DATE_STR}.md"


# ── 로드 ─────────────────────────────────────────────────────────────────────

def load_candidates() -> list[dict]:
    with open(CANDIDATES_PATH, encoding="utf-8") as f:
        return json.load(f)


def load_shortlist() -> list[dict]:
    with open(SHORTLIST_PATH, encoding="utf-8") as f:
        return json.load(f)


def load_curation() -> list[dict]:
    with open(CURATION_PATH, encoding="utf-8") as f:
        return json.load(f)


# ── 링크 조립 ─────────────────────────────────────────────────────────────────

def format_link(cand: dict) -> str:
    """candidates.json의 link 필드를 그대로 반환. 비어 있으면 매체홈 유도."""
    link = (cand.get("link") or "").strip()
    if link:
        return link
    # link가 정말 없는 예외 케이스
    media = cand.get("media", "")
    return f"(링크 없음 — {media} 홈에서 확인)"


# ── 날짜 표기 ─────────────────────────────────────────────────────────────────

def date_prefix(cand: dict) -> str:
    if cand.get("old"):
        pub = cand.get("pubDate", "")
        # pubDate에서 날짜만 추출 시도
        m = re.search(r"\d{4}-\d{2}-\d{2}", pub)
        if not m:
            # RFC 2822 → 파싱
            from email.utils import parsedate_to_datetime
            try:
                dt = parsedate_to_datetime(pub)
                m_str = dt.date().isoformat()
            except Exception:
                m_str = pub[:16] if pub else "날짜 미상"
        else:
            m_str = m.group(0)
        return f"⚠️[오래됨: {m_str}] "
    return ""


# ── Markdown 조립 ─────────────────────────────────────────────────────────────

def build_markdown(candidates: list[dict], curation: list[dict]) -> str:
    # id → candidate 매핑
    id2cand = {i: c for i, c in enumerate(candidates)}

    # desk별 분류
    spotlight = []    # 주목
    listed    = []    # 리스트
    transfer  = []    # 이관
    excluded  = []    # 제외
    bundles   = []    # 묶음 아이디어

    for entry in curation:
        idx = entry["id"]
        cand = id2cand.get(idx)
        if cand is None:
            continue
        desk = entry.get("desk", "제외")
        if desk == "주목":
            spotlight.append((entry, cand))
        elif desk == "리스트":
            listed.append((entry, cand))
        elif desk == "이관":
            transfer.append((entry, cand))
        else:
            excluded.append((entry, cand))
        if entry.get("bundle_idea"):
            bundles.append(entry["bundle_idea"])

    # rank 기준 정렬
    spotlight.sort(key=lambda x: x[0].get("rank", 999))
    listed.sort(key=lambda x: x[0].get("rank", 999))

    lines = []
    lines.append(f"# 교민일보 야간 모니터링 리포트 — {DATE_STR}")
    lines.append("")
    lines.append(f"> 수집 범위: 한국·미국·일본·캐나다·호주·베트남 / 실행일: {DATE_STR}")
    lines.append(f"> 링크 정책: candidates.json의 link 필드 그대로 사용 (모델 생성 링크 없음)")
    lines.append(f"> RSS 피드 수: {len(set(c['media'] for c in candidates))}개 매체 / shortlist: {len(candidates)}건 큐레이션")
    lines.append("")

    # ── 오늘의 주목 ──────────────────────────────────────────────────────────
    lines.append("## ⭐ 오늘의 주목 1건")
    lines.append("")
    if spotlight:
        e, c = spotlight[0]
        dp = date_prefix(c)
        lines.append(f"**[{c['country']}] {dp}{c['title']} / {c['media']}**")
        lines.append("")
        lines.append(e.get("one_liner", ""))
        if e.get("caution"):
            lines.append(f"⚠️ {e['caution']}")
        lines.append(f"🔗 {format_link(c)}")
    else:
        lines.append("(해당 없음)")
    lines.append("")
    lines.append("---")
    lines.append("")

    # ── 기록가치 순 리스트 ────────────────────────────────────────────────────
    lines.append("## 📌 기록가치 순 리스트")
    lines.append("")
    # 주목 1위 항목이 리스트에도 나오지 않도록 제외 처리
    spotlight_ids = {e["id"] for e, _ in spotlight[:1]}
    all_list = [(e, c) for e, c in (spotlight[1:] + listed) if e["id"] not in spotlight_ids or True]
    # 리스트에는 주목[0]을 포함시키되 이미 위에서 보여줬으므로 1번부터 시작
    # 실제로 주목 항목을 리스트에 반복 포함할지 여부: 포함하되 주목 표시
    num = 1
    for e, c in (spotlight + listed):
        dp = date_prefix(c)
        lines.append(f"**[{num}] [{c['country']}] {dp}{c['title']} / {c['media']}**")
        lines.append(f"→ {e.get('one_liner', '')}")
        if e.get("caution"):
            lines.append(f"⚠️ {e['caution']}")
        lines.append(f"🔗 {format_link(c)}")
        lines.append("")
        num += 1
    if num == 1:
        lines.append("(해당 없음)")
        lines.append("")
    lines.append("---")
    lines.append("")

    # ── 타 데스크 이관 ────────────────────────────────────────────────────────
    lines.append("## ↪️ 타 데스크 이관")
    lines.append("")
    for e, c in transfer:
        dp = date_prefix(c)
        lines.append(f"- [{c['country']}] {dp}{c['title']} / {c['media']}")
        lines.append(f"  → {e.get('one_liner', '')}")
        lines.append(f"  🔗 {format_link(c)}")
    if not transfer:
        lines.append("(해당 없음)")
    lines.append("")

    # ── 제외 ──────────────────────────────────────────────────────────────────
    # v2: 기자 발송용으로 제외 섹션 숨김. (증시·일왕·광고 등 노이즈 비노출)
    #     디버깅이 필요하면 SHOW_EXCLUDED = True 로 전환.
    SHOW_EXCLUDED = False
    if SHOW_EXCLUDED:
        lines.append("## 🗑️ 제외")
        lines.append("")
        for e, c in excluded:
            reason = e.get("one_liner", "기준 미달")
            lines.append(f"- [{c['country']}] {c['title']} — {reason}")
        if not excluded:
            lines.append("(해당 없음)")
        lines.append("")

    # ── 묶음 아이디어 ─────────────────────────────────────────────────────────
    lines.append("## 💡 묶음 아이디어")
    lines.append("")
    if bundles:
        for b in bundles:
            lines.append(f"- {b}")
    else:
        lines.append("(해당 없음)")
    lines.append("")

    return "\n".join(lines)


# ── Markdown → HTML ───────────────────────────────────────────────────────────

def md_to_html(md: str, title: str) -> str:
    """
    최소 변환기 — 외부 라이브러리 없이도 동작.
    가능하면 markdown 패키지 사용.
    """
    try:
        import markdown as mdlib
        body = mdlib.markdown(md, extensions=["extra"])
    except ImportError:
        # 수동 변환 (기본만)
        body = _manual_md_to_html(md)

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
  body {{ font-family: 'Apple SD Gothic Neo', 'Malgun Gothic', sans-serif;
         max-width: 800px; margin: 40px auto; line-height: 1.7; color: #222; }}
  h1 {{ color: #1a3a6e; border-bottom: 2px solid #1a3a6e; padding-bottom: 8px; }}
  h2 {{ color: #2c5f9e; margin-top: 32px; }}
  a {{ color: #1a56db; }}
  blockquote {{ background: #f0f4ff; border-left: 4px solid #2c5f9e;
                margin: 0; padding: 12px 16px; }}
  hr {{ border: none; border-top: 1px solid #ddd; margin: 24px 0; }}
  li {{ margin-bottom: 6px; }}
</style>
</head>
<body>
{body}
</body>
</html>"""


def _manual_md_to_html(md: str) -> str:
    """markdown 패키지 없을 때 최소 변환."""
    lines = md.split("\n")
    out = []
    in_ul = False
    for line in lines:
        # 헤딩
        m = re.match(r"^(#{1,6})\s+(.*)", line)
        if m:
            if in_ul:
                out.append("</ul>"); in_ul = False
            n = len(m.group(1))
            out.append(f"<h{n}>{_inline(m.group(2))}</h{n}>")
            continue
        # HR
        if re.match(r"^---+$", line.strip()):
            if in_ul:
                out.append("</ul>"); in_ul = False
            out.append("<hr>")
            continue
        # blockquote
        if line.startswith("> "):
            if in_ul:
                out.append("</ul>"); in_ul = False
            out.append(f"<blockquote>{_inline(line[2:])}</blockquote>")
            continue
        # list
        if line.startswith("- ") or re.match(r"^\d+\. ", line):
            if not in_ul:
                out.append("<ul>"); in_ul = True
            text = re.sub(r"^-\s+|^\d+\.\s+", "", line)
            out.append(f"<li>{_inline(text)}</li>")
            continue
        # 빈 줄
        if not line.strip():
            if in_ul:
                out.append("</ul>"); in_ul = False
            out.append("")
            continue
        # 일반 텍스트
        if in_ul:
            out.append("</ul>"); in_ul = False
        out.append(f"<p>{_inline(line)}</p>")

    if in_ul:
        out.append("</ul>")
    return "\n".join(out)


def _inline(text: str) -> str:
    """인라인 마크다운 변환 (bold, link, emoji 보존)."""
    # 링크 [text](url)
    text = re.sub(
        r"\[([^\]]+)\]\((https?://[^\)]+)\)",
        r'<a href="\2">\1</a>',
        text
    )
    # 🔗 URL (naked URL)
    text = re.sub(
        r'(🔗\s*)(https?://\S+)',
        r'\1<a href="\2">\2</a>',
        text
    )
    # bold **text**
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    # italic *text*
    text = re.sub(r"\*(.+?)\*", r"<em>\1</em>", text)
    return text


# ── Resend 발송 ───────────────────────────────────────────────────────────────

def send_email(html_body: str, subject: str) -> str:
    """Resend API로 발송. 결과 문자열 반환."""
    api_key = os.environ.get("RESEND_API_KEY", "")
    if not api_key:
        return "ERROR: RESEND_API_KEY 환경변수 없음"

    payload = {
        "from": "onboarding@resend.dev",
        "to": ["publisher@gyominilbo.com"],
        "subject": subject,
        "html": html_body,
    }
    try:
        resp = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=30,
        )
        data = resp.json()
        if resp.status_code in (200, 201):
            return f"발송 성공 — id: {data.get('id', 'unknown')}"
        else:
            return f"발송 실패 {resp.status_code}: {data}"
    except Exception as e:
        return f"발송 오류: {e}"


# ── Git push ─────────────────────────────────────────────────────────────────

def git_push(report_path: str) -> str:
    """현재 브랜치에서 커밋 후 HEAD:main 으로 직접 push. 결과 문자열 반환."""
    cmds = [
        ["git", "add", report_path, CANDIDATES_PATH, SHORTLIST_PATH, CURATION_PATH, SEEN_PATH],
        ["git", "commit", "-m", f"야간 모니터링 리포트 {DATE_STR}"],
        ["git", "push", "origin", "HEAD:main", "--force-with-lease"],
    ]
    log = []
    for cmd in cmds:
        result = subprocess.run(cmd, capture_output=True, text=True)
        line = f"$ {' '.join(cmd)}\n"
        if result.stdout:
            line += result.stdout
        if result.stderr:
            line += result.stderr
        log.append(line)
        if result.returncode != 0:
            # commit 실패는 "nothing to commit" 가능성 — 계속 진행
            if "commit" in cmd and ("nothing to commit" in result.stdout + result.stderr):
                log.append("(변경사항 없음 — push 생략)")
                return "\n".join(log)
            log.append(f"[ERROR] 종료코드 {result.returncode}")
    return "\n".join(log)


# ── 메인 ─────────────────────────────────────────────────────────────────────

def send_failure_alert(reason: str) -> None:
    """수집 실패 시 빈 리포트 대신 알림 메일 발송."""
    subject = f"[수집 실패] 교민일보 야간 모니터링 — {DATE_STR}"
    body = f"<h2>수집 실패 알림</h2><p>{reason}</p><p>날짜: {DATE_STR}</p>"
    result = send_email(body, subject)
    print(f"  실패 알림 메일: {result}")
    sys.exit(1)


def main():
    print(f"build_report.py 실행 — {DATE_STR}")

    candidates = load_candidates()
    print(f"  candidates: {len(candidates)}건")

    # 안전 가드: 수집 0건이면 알림 메일 후 종료
    if len(candidates) == 0:
        send_failure_alert("candidates.json이 비어 있습니다 — RSS 수집 전체 실패")

    shortlist = load_shortlist()
    print(f"  shortlist:  {len(shortlist)}건")

    if len(shortlist) == 0:
        send_failure_alert("shortlist.json이 비어 있습니다 — 전처리 실패")

    curation = load_curation()
    print(f"  curation:   {len(curation)}건")

    if len(curation) == 0:
        send_failure_alert("curation.json이 비어 있습니다 — 분류 실패")

    # 1. Markdown 조립 — curation id는 shortlist 인덱스 기준
    md = build_markdown(shortlist, curation)

    # 2. 발송 결과 placeholder — 실제 발송 후 추가
    subject = f"교민일보 야간 모니터링 — {DATE_STR}"

    # 3. HTML 변환 & 발송
    html = md_to_html(md, subject)
    send_result = send_email(html, subject)
    print(f"  이메일: {send_result}")

    # 4. 발송 결과를 리포트 하단에 추가
    md += f"\n## 이메일 발송 결과\n\n{send_result}\n"

    # 5. 파일 저장
    os.makedirs(REPORT_DIR, exist_ok=True)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write(md)
    print(f"  저장: {REPORT_PATH}")

    # 6. Git push
    git_log = git_push(REPORT_PATH)
    print("\n=== git 로그 ===")
    print(git_log)


if __name__ == "__main__":
    main()
