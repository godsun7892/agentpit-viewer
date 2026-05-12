// AgentPit viewer — JSON 데이터 schema.
//
// export_data.py 가 Neon PG 에서 SELECT 후 sanitize 하여 이 형태로 출력.
// public 정책: api_key / prompt 본문 / reasoning 원문 X. 그 외 공개.

export type Provider = "openai" | "google" | "xai" | "deepseek" | "anthropic";

export interface Agent {
  agent_id: string;
  name: string;            // 사용자-친화 라벨 (예: "patient_farmer")
  role_id: string;         // wheat_farm / dairy_ranch / mill / dairy_processor / bakery
  scenario_id: string;     // bread
  provider: Provider;
  model_id: string;        // gpt-4o-mini, gemini-2.5-flash, ...
  prompt_version_id: string;  // unique. prompt 본문은 노출 X
}

export interface Game {
  session_id: string;
  scenario_id: string;
  total_rounds: number;
  rounds_completed: number;
  started_at: string;       // ISO 8601
  ended_at: string;
  agent_ids: string[];      // 5
  final_cash: Record<string, number>;  // agent_id → final cash
  trade_count: number;
  message_count: number;
  event_count: number;
}

export interface Trade {
  trade_id: string;
  session_id: string;
  round_number: number;
  seller_agent_id: string;
  buyer_agent_id: string;
  resource_id: string;
  quantity: number;
  total_price: number;
  unit_price: number;       // total_price / quantity
  created_at: string;
}

export interface Offer {
  offer_id: string;
  session_id: string;
  round_number: number;
  side: "sell" | "buy";
  proposer_agent_id: string;
  counterpart_agent_id: string;
  resource_id: string;
  quantity: number;
  total_price: number;
  status: "sent" | "accepted" | "rejected" | "countered";
  created_at: string;
}

export interface Message {
  message_id: string;
  session_id: string;
  round_number: number;
  channel_id: string;
  sender_id: string;
  content: string;
  created_at: string;
}

// Aggregated stats per agent across all games
export interface LeaderboardEntry {
  agent_id: string;
  name: string;
  provider: Provider;
  model_id: string;
  role_id: string;
  games_played: number;
  total_cash_earned: number;
  avg_final_cash: number;
  trade_count: number;
  message_count: number;
  total_tokens_in: number;
  total_tokens_out: number;
  avg_latency_ms: number;
  // 7축 평가 (Phase 2 미구현 — 일단 0)
  axis_scores?: Partial<{
    strategy: number;
    persuasion: number;
    deception: number;
    autonomy: number;
    adaptability: number;
    integrity: number;
    cost_efficiency: number;
  }>;
}

// Event type stats per game (집계 — raw events 다 보여주면 너무 큼)
export interface EventStats {
  session_id: string;
  by_type: Record<string, number>;  // event_type → count
  llm_calls: {
    total: number;
    by_provider: Record<string, number>;
    total_tokens_in: number;
    total_tokens_out: number;
    avg_latency_ms: number;
  };
  tool_calls: {
    total: number;
    successes: number;
    failures: number;
    by_tool: Record<string, number>;
  };
}

// 6축 heuristic 평가 점수 (apps/evaluator 본격 구현 전 prototype).
// 출처: viewer/scripts/heuristic_scores.py
export interface HeuristicScoreEntry {
  session_id: string;
  agent_id: string;
  agent_name: string;
  role_id: string;
  provider: Provider;
  scores: {
    integrity: number;        // 0..1 — message 약속 vs offer 행동 일관성
    adaptation: number;       // 0..1 — round 진행 cash rank 변화량
    autonomy: number;         // 0..1 — tool 다양성 + 능동 정보 수집
    responsiveness: number;   // 0..1 — 1 - (open / received offers)
    inbox_awareness: number;  // 0..1 — 1 - (unread / total incoming msgs)
    market_awareness: number; // 0..1 — outgoing offer 가 recent market trades 와 align
  };
  detail: {
    integrity: {
      opportunities?: number;
      violations?: number;
      violation_detail?: Array<Record<string, unknown>>;
      note?: string;
    };
    adaptation: {
      rank_at_R5?: number;
      rank_at_final?: number;
      swing_normalized?: number;
      second_half_rank_volatility?: number;
      note?: string;
    };
    autonomy: {
      n_distinct_tools?: number;
      n_available_tools?: number;
      diversity?: number;
      n_tool_calls?: number;
      n_proactive_calls?: number;
      proactive_ratio?: number;
      note?: string;
    };
    responsiveness: {
      received?: number;
      open?: number;
      accepted?: number;
      rejected?: number;
      countered?: number;
      note?: string;
    };
    inbox_awareness: {
      total_incoming?: number;
      unread?: number;
      n_channels?: number;
      fully_unread_channels?: number;
      note?: string;
    };
    market_awareness: {
      n_offers_total?: number;
      n_offers_evaluated?: number;
      avg_deviation_pct?: number;
      lookback_rounds?: number;
      note?: string;
    };
  };
}

export interface DataExportMeta {
  exported_at: string;
  total_games: number;
  total_agents: number;
  total_trades: number;
  total_messages: number;
  total_events: number;
  data_source: "neon" | "mock";
  schema_version: 1;
}
