# AgentPit 영상 — Scene Plan

**목표**: 정부 창업공모전 제출용 3분 시연 영상. AI 평가 헤드라인 70% / 양육 시뮬 20% / 글로벌 10%.

**찍는 방식**: viewer(`agentpit.com`) 화면 OBS 캡처 + narration. 라이브 X (N판 녹화 후 명장면 큐레이션). 합성 X (모든 메시지/거래는 실 PG 데이터).

**대상**: 정부 심사위원. 기술 자랑 ✗. 행동 narrative 살아있는 데이터 ✓.

---

## 0. 출연자 (cast)

bread 시나리오, 5 role × 4 provider mix. 영상에서는 4 provider를 "선수"로 의인화.

| Role | Agent name | Provider | Model | Persona (영상용 한 줄) |
|------|-----------|----------|-------|----------------------|
| wheat_farm | patient_farmer | OpenAI | gpt-4o-mini | "침묵의 밀 트레이더 — 숫자로 말함" |
| dairy_ranch | calm_rancher | Google | gemini-2.5-flash | "관계형 낙농가 — 대화로 거래" |
| mill | quiet_miller | xAI | grok-3-mini | "공급망 조율자 — 양쪽 줄다리기" |
| dairy_processor | basic_churner | DeepSeek | deepseek-chat | "JIT 처리 — 재고 최소" |
| bakery | routine_baker | OpenAI | gpt-4o-mini | "수요 곡선 학습자 — 가격 실험" |

영상에서 강조: **4 provider × 같은 게임 = 객관 비교**. bakery가 openai 두 번 들어간 건 baseline reference (영상에선 mention X).

---

## 1. 씬 구조

| # | 시간 | 목적 | 필요 데이터 | 화면 | 7축 매핑 |
|---|------|------|------------|------|---------|
| **S1** | 0:00–0:20 | 4 선수 소개 | agent 5명 + persona prompt 1줄 | 모션그래픽 또는 viewer agent badge | 전략 (prompt 자체) |
| **S2** | 0:20–0:50 | "협상 시작" | round 1-3에 messaging 왕복 3+ | viewer `/games/[id]` messages 섹션 fade-in | 설득 / 자율성 |
| **S3a** | 0:50–1:15 | 명장면: 가격 dramatic 변동 | round 내 unit_price 50% 이상 변동 trade | viewer trades 표 줌인 | 적응력 |
| **S3b** | 1:15–1:35 | 명장면: 협상 길게 | 한 채널 5+ 메시지 + 결국 거래 체결 | viewer message 카드 + trade row | 설득 |
| **S3c** | 1:35–1:50 | 명장면: 막판 역전 또는 bluff | 후반 round에서 ERP 순위 뒤집힘 OR bluff 통한 거래 | viewer trades + final standings | 기만탐지 / 무결성 |
| **S4** | 1:50–2:30 | Final standings | 4 provider 점수 차이 명확 | viewer game detail final standings 표 | 비용효율 |
| **S5** | 2:30–2:55 | 기존 벤치마크 vs AgentPit | (정적 슬라이드 — 데이터 X) | 좌우 분할 슬라이드 | — |
| **S6** | 2:55–3:00 | leaderboard + URL | 누적 게임 N개, 다양한 provider 우승 | viewer `/leaderboard` 풀로드 | — |

---

## 2. 씬별 검증 SQL + 데이터 포인터

게임 굴린 후 아래 쿼리로 "이 씬에 쓸 데이터 나왔나" 확인. **결과물 들어오면 game_id/round 번호 박을 자리는 `<TBD>`.**

### S2 — messaging 왕복 3+

```sql
SELECT session_id, round_number, COUNT(*) AS msg_count
FROM messaging.message
WHERE round_number <= 3
GROUP BY session_id, round_number
HAVING COUNT(*) >= 3
ORDER BY round_number, msg_count DESC;
```

**Pointer**: `<TBD: session_id, round>`

### S3a — 가격 dramatic 변동

```sql
WITH ranked AS (
  SELECT session_id, round_number, resource_id, unit_price,
         LAG(unit_price) OVER (PARTITION BY session_id, resource_id ORDER BY round_number, created_at) AS prev_price
  FROM economy.trade
)
SELECT session_id, round_number, resource_id, prev_price, unit_price,
       ROUND((unit_price - prev_price) * 100.0 / NULLIF(prev_price, 0), 1) AS pct_change
FROM ranked
WHERE prev_price IS NOT NULL
  AND ABS((unit_price - prev_price) * 1.0 / NULLIF(prev_price, 0)) >= 0.5
ORDER BY ABS(pct_change) DESC
LIMIT 10;
```

**Pointer**: `<TBD: session_id, round, resource_id>`

### S3b — 한 채널 5+ 메시지 후 거래

```sql
WITH chatty AS (
  SELECT session_id, channel_id, COUNT(*) AS msg_count,
         MIN(round_number) AS first_r, MAX(round_number) AS last_r
  FROM messaging.message
  GROUP BY session_id, channel_id
  HAVING COUNT(*) >= 5
)
SELECT c.*, EXISTS (
  SELECT 1 FROM economy.trade t
  WHERE t.session_id = c.session_id
    AND t.round_number BETWEEN c.first_r AND c.last_r + 1
) AS resulted_in_trade
FROM chatty c
ORDER BY msg_count DESC LIMIT 5;
```

**Pointer**: `<TBD: session_id, channel_id>`

### S3c — 막판 역전

```sql
-- TODO: ERP snapshot 테이블 확인 후 round-by-round cash 비교 쿼리 작성
-- 일단은 final standings + game_event 의 erp_snapshot 이벤트로 reconstruct
SELECT session_id, event_type, properties->>'round_number' AS round,
       properties->>'agent_id' AS agent, properties->>'cash' AS cash
FROM eval.game_event
WHERE event_type IN ('erp.snapshot', 'round.end')
ORDER BY session_id, round, agent;
```

**Pointer**: `<TBD: session_id, switching_round>`

### S4 — 명확한 점수 차

```sql
-- ERP final cash 가 round 마지막에 어떻게 분포돼있는지
-- (현 시점 economy.erp 가 마지막 상태 보관 — round_number 컬럼 활용)
SELECT session_id, agent_id, cash, round_number
FROM economy.erp
WHERE session_id IN (
  SELECT DISTINCT session_id FROM economy.erp
  ORDER BY session_id DESC LIMIT 5
)
ORDER BY session_id, cash DESC;
```

**Pointer**: `<TBD: session_id 최소 1개, ideally provider별로 1등 다른 회차 섞임>`

### S6 — 다양한 우승자

```sql
WITH winners AS (
  SELECT session_id,
         (SELECT agent_id FROM economy.erp e2
          WHERE e2.session_id = e1.session_id
          ORDER BY cash DESC LIMIT 1) AS winner_id
  FROM economy.erp e1
  GROUP BY session_id
)
SELECT a.provider, COUNT(*) AS wins
FROM winners w
JOIN identity.agent a ON a.agent_id = w.winner_id
GROUP BY a.provider
ORDER BY wins DESC;
```

**기준**: 최소 3개 provider가 1번 이상 우승.

**Pointer**: `<TBD: 게임 N=? 까지 확보 시 충족>`

---

## 3. Run plan

### 모델 escalation 원칙

- **baseline**: 현 시드 그대로 (mid-low tier 4 provider). 모든 게임 여기서 시작
- **escalation trigger**: 특정 씬 데이터가 N=3 게임 후에도 안 나오면 → 해당 액션이 약한 모델만 1단계 위로
  - 협상 메시지가 빈약(S2/S3b) → openai gpt-4o-mini → gpt-4.1-mini OR google flash → pro
  - bluff/collusion 실패(S3c) → expert prompt 적용 + 가장 instruction-following 강한 provider로
  - 가격 발견 안 됨(S3a) → economy 파라미터 조정 (prompt가 아니라 시나리오)

### Round 수

- **calibration**: 8 round (~5분 wall clock, $0.5~1 예상)
- **본판**: 10~12 round (~7분, $1~2). drama 충분히 발생할 분량
- **막판 역전 노릴 때만**: 15 round

### 실행 순서

1. **Game 1 (calibration, default prompts, 8 round)** ← 다음 단계
2. PG 직접 SQL로 위 검증 쿼리 6개 돌림
3. 충족 씬은 doc pointer 채움, 미충족 씬은 다음 게임 prompt/시나리오 튜닝
4. **Game 2~** 1판씩 반복. 미충족 씬 채울 때까지

### 명령

```bash
# seed (한 번만)
.venv/bin/python scripts/seed_4provider_mix.py

# game 1판
set -a && source .env && set +a && \
  GM_TOTAL_ROUNDS=8 GM_TICK_SPEED=40 GM_USE_EXISTING_AGENTS=true \
  .venv/bin/python -m gamemaster.main
```

---

## 4. 7축 평가 가능성 점검

각 게임 후 "이 7축이 이 데이터로 산출 가능한가?" 표를 채우며 진행. evaluator 코드 작성 시점에 검증된 상태로 시작 가능.

| 축 | 필요 데이터 | 현 데이터로 가능? | 추가 필요 시 |
|----|-----------|----------------|------------|
| 전략 | trade outcome + ERP 시계열 | TBD | round별 ERP snapshot 누적 필요 |
| 설득 | message 내용 + 거래 체결 인과 | TBD | message → trade 시간적 연결 분석 |
| 기만탐지 | bluff 메시지 + 사실 ground truth | TBD | bluff 라벨링 필요 (expert prompt 표시) |
| 자율성 | tool 호출 패턴 + intervention 없음 | TBD | tool.call 이벤트 집계 |
| 적응력 | 가격/전략 변화 → 이후 행동 변화 | TBD | round간 행동 변화 측정 |
| 무결성 | 거짓 정보 발신 빈도 | TBD | bluff 라벨링 필요 |
| 비용효율 | final_cash / total_token_cost | TBD | eval.game_event.llm.call에 token + cost 있어야 |

**Game 1 후 채울 것**.

---

## 5. 밸런스 모니터링

게임 굴릴 때마다 아래 확인:

- 매 게임 final cash 격차 (Top - Bottom). 너무 크면(>10x) 한 role 압도적 → 시나리오 economy 파라미터 조정
- Provider 별 누적 승률. N=10에서 한 provider가 >70%면 모델 능력 차 아니라 시스템 편향 의심
- "거래 한 번도 안 한 agent" 발생률. 시장 발견 실패 신호

각 게임 후 한 줄 요약 누적:

| Game | Session ID | Round | Winner (provider) | Top-bottom cash gap | 거래 미참여 agent | 비고 |
|------|-----------|-------|-------------------|--------------------|-----------------|------|
| 1 | TBD | 8 | TBD | TBD | TBD | calibration |

---

## 6. Data Pointer 색인 (영상 컷용)

영상 편집 시 필요한 정확한 데이터 위치. **씬별 검증 통과 후 채움**.

### Game 1 (calibration, 20 round, expert prompts, session `4d94d2bf-0768-433a-9672-651f2a1262eb`)

| 씬 | 상태 | Pointer |
|----|------|--------|
| S1 cast intro | ✅ | identity.agent 5명 (patient_farmer/calm_rancher/quiet_miller/basic_churner/routine_baker) |
| S2 (round 1-3) | ⚠️ 부분 | session `4d94d2bf` round 1 = 8 msg. round 2-3 비어있음 |
| S3a (가격 변동) | ⚠️ 약함 | session `4d94d2bf` round 18 wheat 7→10 (+42.9%). 1건만. **다음 게임 더 봐야 함** |
| S3b (긴 대화) | ✅ 강함 | 채널 4개 5+ messages, 다 거래로 이어짐. 채널 IDs: `019e158e-3327`(24msg, bakery), `019e158e-33b2`(22msg, bakery), `019e158e-184b`(21msg, ranch), `019e158f-bf87`(6msg, farm) |
| S3c (역전) | ❌ schema 한계 | economy.erp가 in-place update — round별 cash 시계열 추적 불가. **영상에선 final standings drama로 대체** |
| S4 (final) | ⚠️ 부분 | patient_farmer 326 > calm_rancher 264 > routine_baker 200 > basic_churner 166 > quiet_miller 86. **편차는 큼 — drama로 OK. 단, 빵 안 만들어진 게임이라 narrative 부족** |
| S6 (다양한 우승자) | ❌ | OpenAI 1승. 누적 부족 |
| **빵 NPC 판매** | ❌ **0건** | flour 거래 0, butter 거래 0, bread 생산 0. **영상 핵심 narrative 깨짐** |

### Game 1 진단

- ✅ wheat (3 trades) + milk (3 trades) — tier 1 흐름 OK
- ❌ flour/butter 거래 0 — **mill이 wheat 가격 협상에 stuck**:
  - R4 buy offer countered → R18까지 14 round 낭비 (expert prompt "5%씩 양보" 룰 때문)
  - R18 milling 완료, R20 게임 종료 — flour 팔 시간 없음
- ❌ bakery는 input 0 → baking 불가 → bread 0 → NPC 판매 0

### Game 2 (iteration, 25 round, time-pressure prompts, session ?)

**Iteration 변경점**:
- 5 role prompts 전부 `TIME PRESSURE RULE (READ FIRST)` 헤더 추가 — round 5/8/12 단계별로 가격 양보 강제
- Round 20 → 25 (협상 stuck 마진 + 빵 NPC 판매 시간 확보)
- spoilage 늘림 그대로 유지 (wheat/milk/butter 4→6, bread 3→5)
- agent 재사용 (TRUNCATE X) — Game 1 messaging/trade 데이터 보존

| 씬 | Pointer |
|----|--------|
| S1 cast intro | (Game 1과 동일 agent — 재사용) |
| S2 (round 1-3) | `<TBD: Game 2 session>` |
| S3a (가격 변동) | `<TBD>` |
| S3b (긴 대화) | (Game 1 4 channel + Game 2 채널 합산 큐레이션) |
| S3c | 영상 미사용 (schema 한계) |
| S4 (final) | `<TBD: Game 2 session>` — **빵 만들어지면 이걸 메인 컷** |
| S6 (다양한 우승자) | Game 1+2 누적, 더 필요 |
| **빵 NPC 판매** | `<TBD: 핵심 검증>` |

---

## 7. 진행 로그

| Date | Session | Round | Models | Result | 다음 액션 |
|------|---------|-------|--------|--------|-----------|
| 2026-05-11 | `4d94d2bf` | 20 | mid-low mix | flour/butter/bread 0건. mill stuck. S3b 4 채널 wins | prompt에 time pressure rule 추가, 25 round 재시도 |
| 2026-05-11 | (Game 2 진행 중) | 25 | mid-low mix + time pressure | TBD | TBD |

## 8. 다음 단계

1. ✅ Game 1 calibration → time pressure rule 식별
2. ✅ Game 2 굴림 (25 round, time-pressure prompts, session `bf421c6a`)
3. ✅ 빵 NPC 판매 여전히 0 — 진짜 원인 = mill 가격 anchor 거부 + bakery accept_offer 잘못된 ID
4. **다음 후보**: Game 3 — 옵션 선택 미정 (가격 강제 X, 모델 변경 X 룰 적용 — 시나리오 가격대 조정 또는 tool 설계 보강이 path)

---

## 9. 발견 누적 — 영상 narrative 재료 + 평가/관찰성 개선 todo

### 9.1 영상 narrative — "AI의 말/행동 불일치" (S3c 대체 컷 후보)

**상황 (Game 1+2 공통)**:
- mill (xAI grok-3-mini) 가 message로 "I produce ~3 flour every 2 rounds. Locking 6 flour at 11 per unit?" — 자기가 11 가격 sell 제안
- bakery가 정확히 11 (또는 12) 단가로 buy offer 보냄
- mill이 5번 다 **rejected**
- 메시지의 가격 약속 ↔ offer 거절 행동이 불일치

**영상 활용**:
> "AI가 자기 입으로 11원에 팔겠다고 한 뒤 11원에 사겠다는 제안을 거절합니다. 점수만 보는 벤치마크는 못 잡습니다. AgentPit은 행동 일관성까지 데이터로 남깁니다."

영상 S3c (역전) 자리에 끼우면 정부 공모전 차별성 ↑.

데이터 위치:
- session `bf421c6a` mill (b16d6983) sent message R1 "locking 6 flour at 11 per unit"
- session `bf421c6a` mill → routine_baker 응답으로 rejected offer 5건 (R4×2, R7, R10, R22)

### 9.2 평가/관찰성 한계 — events properties 정보 손실

`eval.game_event` 의 `tool.succeeded` properties = `{read_only, tool_name, args_fingerprint, duration_ms}`. **tool args/result/ok/error_code 안 담음**.

영향:
- bakery가 accept_offer로 어떤 offer_id 시도했는지 모름
- 도메인 에러(`OFFER_ALREADY_PROCESSED` 등) 추적 불가
- 7축 평가 중 "기만 탐지"/"전략" 산출에 필요한 raw decision data가 hash 형태

**Evaluator 가동 전 todo**: `tool.succeeded` payload에 `ok`, `error_code`, sanitize된 result snippet 추가. raw 큰 텍스트는 S3 cold에. 이미 ADR-016 에 reasoning 원문 S3 cold 명시되어 있음 — tool result도 같은 정책 후보.

### 9.3 tool 설계 약점 — return schema 정보 부족

**`list_incoming_offers_tool` 반환** (offer_tools.py:128-138):
- `total_price` 만 — `unit_price` 없음 → mid-tier model 산수 부정확
- `sent_at_round` 만 — `expires_at_round` 없음 → 마감 임박 우선순위 판단 불가
- `side='sell'`의 의미 ("proposer 판매자, 내가 buyer") schema에 없음, docstring에만

**`list_incoming_messages_tool` + `read_channel_tool` 분리** — bakery는 list만 부르고 read는 0회. mid-tier model에 2-step 부담. 통합 검토.

### 9.4 시장 정보 도구 사용 편향

`list_historical_trades_tool` Game 2 사용 분포:
- deepseek-chat (dairy_proc): 41
- xai grok-3-mini (mill): 3
- openai gpt-4o-mini (bakery): 2
- openai gpt-4o-mini (wheat_farm): 0
- google gemini-2.5-flash (dairy_ranch): 0

**시장 가격 학습 행동 = deepseek 만**. 다른 model은 prompt anchor만 보고 행동. 결과: 가격 발견 비효율 + 협상 stuck.

### 9.5 담합/배신 데이터 — 5 agent로는 0건

expert prompt에 COLLUSION/BLUFF clause 있지만 **같은 role agent 0명** 이라 사문화. 영상에 "AI들 담합" 컷 넣으려면 role × 2 게임 별도 필요.

### 9.6 Bakery (gpt-4o-mini) — 절차 일관성 약함

bakery는 R10에 list_incoming_offers로 butter sell offer (qty=2 unit=16) 봄. 그 직후 `accept_offer_tool` 안 부르고 `send_offer_tool` (자기 buy offer 11)만 호출. **incoming 봤지만 무시 결정**. mid-tier model이 expert prompt "accept rationals immediately"를 매 turn 일관 따르지 못함.

### 9.7 Role balance imbalance — 게임 design 차원 문제 (영상보다 본질적)

평가 의의 = 같은 model/prompt 면 결과 비슷해야 정상. **격차 = role 자체의 유리/불리**. 3게임 평균:

| Role | G1 | G2 | G3 | 평균 | vs 평균 |
|------|-----|-----|-----|------|--------|
| dairy_ranch | 264 | 432 | 580 | **425** | +76% |
| wheat_farm | 326 | 350 | 258 | 311 | +29% |
| bakery | 200 | 200 | 93 | 164 | -32% |
| dairy_processor | 166 | 130 | 119 | 138 | -43% |
| mill | 86 | 134 | 178 | **133** | -45% |

dairy_ranch 425 ↔ mill 133 = **3.2배 imbalance**. 원인 후보:
- dairy_ranch: 자동 생산 + NPC floor 가까운 sell. 협상 부담 ≈ 0
- mill: 양측 협상 + 가공 + 좁은 margin
- bakery: tier 3, chain 끝, fragile

→ 이 imbalance 가 model/prompt 평가 신호를 묻어버림. **economy_sim scenario 파라미터 (recipe outputs / NPC prices / spoilage) 재밸런싱 필요**. 영상 narrative 와 별개의 본질 작업.

### 9.8 Game 3 — Token 매핑 부분 성공 (dairy_processor inbound 만 작동)

session `89103477`. Token 매핑이 **dairy_processor 의 inbound accept 에는 명확한 효과**, **bakery 의 inbound 에는 변화 없음**:

| 지표 | Game 2 | Game 3 |
|------|--------|--------|
| dairy_processor accept rate | 5/11 (45%) | **13/17 (76%)** ✓ |
| bakery accept rate (incoming) | 0/2 (0%) | **0/3 (0%)** ✗ |
| butter inter-agent trades | 0 | **4** ✓ (bakery → dairy_proc 의 send_offer 경로) |
| flour 거래 | 0 | **0** ✗ (mill grok-3-mini 거절 패턴 불변) |
| bread 생산 / NPC 판매 | 0 / 0 | **0 / 0** ✗ |

butter 4 거래는 bakery 의 **buy offer 가 accepted** 된 것 (R7/R14×2/R18, unit 17-18). bakery 의 incoming butter sell offer 3건은 여전히 status='sent' 그대로 — bakery 가 "send_offer 후 dairy_proc 이 accept" 경로에 만족, incoming은 거의 안 봄. accept_offer_tool 호출 Game 2 11회 → Game 3 **2회로 급감**.

mill 의 flour reject 패턴은 token 무관 — 별도 가설 필요.

### 9.9 Game 3 후 관찰성 보강 — traced_tool 의 dict 도메인 에러 캡처 (2026-05-11)

기존 한계: tool 함수가 `error()` helper 로 status='error' dict 반환 시 `tool.succeeded` 로 마크 → 도메인 에러 / actionable error 메시지 events properties 에 누락. evaluator 와 LLM self-correction 추적 불가.

수정: `traced_function_tool` 데코레이터가 result 가 `isinstance(dict)` + `status == 'error'` 면 `tool.failed` 발화 (error_class='DomainError', error_code, reason 풍부). raise 형식 (EngineError) 과 의미적 일관.

기대 효과:
- Game 4+ 부터 bakery 의 잘못된 토큰 / OFFER_NOT_FOUND 등 LLM self-correction 추적 가능
- 7축 평가의 자율성 / 적응력 / 무결성 산출 raw 데이터 확보
- 이전 게임 (G1-G3) 데이터는 이 정보 손실 — 재현 불가
