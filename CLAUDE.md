# 교민일보 야간 모니터링 — 운영 지침

## 분업 원칙 (절대 준수)

| 담당 | 내용 |
|------|------|
| **코드** | RSS 수집, 링크 결정, shortlist 생성, **규칙 기반 curation 자동 생성**, markdown 조립, HTML 변환, Resend 발송, git push |
| **모델** | 개입하지 않음 (진단·디버깅 요청 시에만) |

---

## 실행 순서 (완전 무인 자동화 — SessionStart 훅)

```
1. python fetch_candidates.py   ← RSS 수집·링크 결정 → candidates.json
2. python make_shortlist.py     ← shortlist.json + curation.json 자동 생성 (규칙 기반)
3. python build_report.py       ← 리포트·메일·main push 전부 처리
```

**모델 개입 없음.** 세 스크립트가 SessionStart 훅에서 순차 실행된다.
모델이 curation.json을 수동으로 편집하는 워크플로는 폐지됐다.

---

## shortlist.json 특성 (make_shortlist.py 출력)

- candidates.json(전체 수집본)에서 아래 전처리를 거친 80~100건
  1. `old=false`(신규) 우선 추출
  2. 제목 기준 근접중복 제거 (자카드 유사도 0.6 이상 → 첫 항목 보존)
  3. 국가당 최대 30건 상한 (미국 편중 방지)
  4. 신규 30건 미만이면 `old=true` 최신순으로 보충
- 각 항목에 `_orig_id` 필드(candidates.json 원본 인덱스) 포함 — 코드 내부용, 모델은 무시

---

## 모델 큐레이션 출력 형식 (curation.json)

```json
[
  {
    "id": 0,
    "rank": 1,
    "desk": "주목|리스트|이관|제외",
    "one_liner": "취재 의미 1~2줄",
    "caution": "팩트체크 주의 (없으면 빈 문자열)",
    "bundle_idea": "묶음 아이디어 (없으면 빈 문자열)"
  }
]
```

- `id`: **shortlist.json 배열 인덱스 (0-based)** — candidates.json 인덱스 아님
- `desk`: 주목(최상위 1건), 리스트(기록가치 순), 이관(타 데스크), 제외
- `rank`: 같은 desk 내 정렬 순서 (낮을수록 상위)

**모델이 출력하는 것은 이 JSON뿐.** 링크·날짜·HTML·메일·git 명령 없음.

**shortlist에 든 것은 전부 한 번씩 판단 대상.** grep 임의 필터 금지.

---

## 3축 정렬 기준

1. **교민 직접 영향도** (체류·이민·생활·법률)
2. **취재 확장 가능성** (인터뷰·후속보도 각도)
3. **신선도** (`old: false` 우선)

---

## 링크 규칙 (절대 준수)

- **shortlist.json의 `link` 필드를 그대로 쓴다.** 모델이 링크를 재판단하거나 변경하지 않는다.
- **"검색 요망" 문자열을 절대 쓰지 않는다.** build_report.py 코드도 이 문자열을 생성하지 않는다.
- 구글 뉴스 리다이렉트 URL(`news.google.com/rss/articles/…`)은 정상 링크 — 경고 표기 없이 그대로.
- link가 빈 경우에만 "(링크 없음 — 매체명 홈에서 확인)" 출력 (거의 발생하지 않음).

---

## 발행일 표기

- `old: true` 항목 → `⚠️[오래됨: YYYY-MM-DD]` 표기 (pubDate 기준) — build_report.py가 처리
- `date_unverified: true` 항목 → 날짜 표기 생략

---

## build_report.py 역할 (코드 전담)

- shortlist.json + curation.json → id(shortlist 인덱스)로 조인
- 리포트 구조 조립:
  - `⭐ 오늘의 주목 1건` — desk=주목 rank=1
  - `📌 기록가치 순 리스트` — desk=주목+리스트 전체
  - `↪️ 타 데스크 이관`
  - `🗑️ 제외`
  - `💡 묶음 아이디어`
  - `이메일 발송 결과`
- markdown → HTML 변환 (링크 `<a href>` 정확히 변환)
- `reports/YYYY-MM-DD.md` 저장 (KST 기준)
- Resend API 발송 (from: onboarding@resend.dev, to: publisher@gyominilbo.com)
- git checkout main → pull → add → commit → push origin main (feature 브랜치 아님)

---

## 이메일 발송 (Resend API)

- 환경변수: `RESEND_API_KEY`
- from: `onboarding@resend.dev`
- to: `publisher@gyominilbo.com`
- subject: `교민일보 야간 모니터링 — YYYY-MM-DD`
- HTML 본문: 리포트 마크다운을 HTML로 변환

---

## Git (코드가 실행 — 모델 직접 실행 금지)

```bash
git checkout main
git pull origin main
git add reports/YYYY-MM-DD.md candidates.json shortlist.json
git commit -m "야간 모니터링 리포트 YYYY-MM-DD"
git push -u origin main
```

`candidates.json`과 `shortlist.json`은 매일 덮어쓴다. 커밋에 포함해 수집 내역을 보존한다.
