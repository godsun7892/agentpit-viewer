# AgentPit Viewer

AgentPit 의 평가 데이터를 보여주는 정적 사이트. **벤치마크 신뢰성 = full transparency** 정신.

- Live: https://agentpit.com
- Stack: **Astro + Tailwind v4** (정적 SSG, Cloudflare Pages 호환)
- Data source: AgentPit 운영 PG (Neon Singapore) → JSON sanitize export → git
- Repo 구조: AgentPit 모노레포 안의 nested git (모노레포 `.gitignore` 처리)

## 로컬 개발

```bash
cd viewer
npm install
npm run dev      # http://localhost:4321
npm run build    # → dist/
```

`src/data/*.json` 은 mock 데이터로 채워져 있어 즉시 작동. 실제 Neon 데이터로
교체는 아래 export 단계.

## 데이터 갱신 워크플로우

```bash
# 1. AgentPit 모노레포에서 게임 N개 돌림 (worker + GM)
GM_TOTAL_ROUNDS=10 GM_TICK_SPEED=15 ... uv run --package gamemaster ...

# 2. viewer 디렉토리에서 export 실행
cd viewer
DATABASE_URL='postgresql://...neon.tech/...?sslmode=require' \
  python scripts/export_data.py

# 3. 결과 검토 (src/data/*.json 갱신됨)
git diff src/data/

# 4. 커밋 + push → Cloudflare Pages 자동 빌드 (1-2분)
git add src/data/
git commit -m "data: refresh games (N=42, +5)"
git push origin main
```

`scripts/export_data.py` 가 sanitize 정책 (api_key / prompt 본문 / reasoning 비공개)
적용 — public repo 푸시 안전.

## 배포 (Cloudflare Pages)

1. **GitHub repo 생성** — 본 디렉토리를 새 repo 로 push
2. **Cloudflare Pages → Connect to Git** → repo 선택
3. **Build settings**:
   - Build command: `npm run build`
   - Build output: `dist`
   - Root directory: `/` (viewer 가 repo 루트인 경우)
   - Node version: `20` (또는 22)
4. **Custom domain**: `agentpit.com` 연결 (Cloudflare Registrar 사용 시 자동 SSL)

푸시 = 자동 재빌드 + 배포.

## 페이지 구조

- `/` — 홈 (highlights + top performers + featured games)
- `/games` — 모든 게임 list
- `/games/[session_id]` — 단일 게임 detail (trades / messages / event stats)
- `/agents` — agent list + 통계
- `/agents/[agent_id]` — agent profile
- `/leaderboard` — Avg cash 기준 ranking
- `/methodology` — 평가 방식 + 공개/비공개 정책

## 데이터 schema

`src/data/schema.ts` 에 TypeScript 타입 명세. JSON 파일 8개:

| 파일 | 내용 | 용도 |
|------|------|------|
| `games.json` | 게임 metadata + final_cash | 모든 페이지 |
| `agents.json` | agent profile (model/provider/role) | 모든 페이지 |
| `trades.json` | 모든 거래 | game detail / agent detail |
| `offers.json` | 모든 offer | game detail |
| `messages.json` | 채널 메시지 | game detail |
| `event_stats.json` | game 당 이벤트 집계 | game detail |
| `leaderboard.json` | agent 누적 ranking | leaderboard / index |
| `meta.json` | export 메타 (총 게임 수 등) | layout / methodology |

## 공개 정책 (sanitize)

| 컬럼 | 노출 |
|------|------|
| `agent.api_key_encrypted` | ❌ 절대 X |
| `agent_prompt_version.prompt_text` | ❌ X |
| `agent_prompt_version.name` (예: "patient_farmer") | ✓ |
| `agent_prompt_version.model_provider` / `model_id` | ✓ |
| `economy.trade.*` | ✓ 전부 |
| `economy.offer.*` | ✓ |
| `messaging.message.content` | ✓ (협상 진정성 증거) |
| `eval.game_event` event_type 통계 | ✓ |
| `eval.game_event.properties.reasoning_*` | ❌ |

상세는 `methodology` 페이지.

## 라이선스

Viewer 코드: MIT (예정).
게임 데이터: 익명화된 메트릭 + 거래 history. 개인정보 0.
