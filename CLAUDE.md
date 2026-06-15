# 교민일보 야간 모니터링 — 운영 지침

## 실행 순서 (필수)

매 세션 시작 시 **반드시** 아래 순서로 진행한다.

```
1. python fetch_candidates.py
2. candidates.json 읽기
3. 리포트 작성 및 이메일 발송
4. git commit & push
```

RSS를 모델이 직접 긁지 않는다. `fetch_candidates.py` 가 RSS 수집·링크 결정을 모두 처리한다.

---

## 링크 규칙 (절대 준수)

- **`candidates.json`의 `link` 필드를 그대로 쓴다.** 모델이 링크를 재판단하거나 변경하지 않는다.
- **"검색 요망" 문자열을 리포트에 절대 쓰지 않는다.** `link`는 항상 URL이다.
- 구글 뉴스 리다이렉트 URL(`news.google.com/rss/articles/…`)은 정상 링크다. ⚠️ 경고 표기 없이 그대로 쓴다.

---

## 발행일 표기

- `old: true` 항목 → `⚠️[오래됨: YYYY-MM-DD]` 표기 (pubDate 기준)
- `date_unverified: true` 항목 → 날짜 표기 생략, 기사 내용으로만 판단

---

## 리포트 구조

```
# 교민일보 야간 모니터링 리포트 — YYYY-MM-DD

> 수집 범위 / 실행일 / 링크 정책 / RSS 피드 수

## ⭐ 오늘의 주목 1건
(교민 직접 영향도 최상위 1건 — 국가·제목·매체 + 취재 의미 3~5줄)
🔗 {link}

---

## 📌 기록가치 순 리스트
(교민 연관성 높은 순, 번호 매김)
각 항목: [번호] [국가] 제목 / 매체
→ 취재 의미 1~2줄
⚠️ 팩트 체크 필요 사항 (있을 경우)
🔗 {link}

---

## ↪️ 타 데스크 이관
(교민 연관 낮으나 가치 있는 기사 — 링크 포함)

## 🗑️ 제외
(광고·행사·스포츠 단신 등 — 이유 명시)

## 💡 묶음 아이디어
(복수 국가 공통 주제 기획 제안)

## 이메일 발송 결과
```

---

## 3축 정렬 기준

1. **교민 직접 영향도** (체류·이민·생활·법률)
2. **취재 확장 가능성** (인터뷰·후속보도 각도)
3. **신선도** (`old: false` 우선)

---

## 이메일 발송 (Resend API)

- 환경변수 `RESEND_API_KEY` 사용
- 수신: `publisher@gyominilbo.com`
- 제목: `교민일보 야간 모니터링 — YYYY-MM-DD`
- HTML 본문: 리포트 마크다운을 HTML로 변환

---

## Git

```
git add reports/YYYY-MM-DD.md candidates.json
git commit -m "야간 모니터링 리포트 YYYY-MM-DD"
git push -u origin main
```

`candidates.json`은 매일 덮어쓴다. 커밋에 포함해 수집 내역을 보존한다.
