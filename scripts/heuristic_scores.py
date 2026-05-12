"""Heuristic 3축 평가 점수 → viewer JSON export.

evaluator 본격 구현 (apps/evaluator 의 scoring/ + fetch/persist) 전 prototype.
viewer/scripts/export_data.py 와 같은 자리 — viewer 의 데이터 파이프라인 일부.

raw event 데이터 (PG) 만으로 inference. 각 score 는 0.0 ~ 1.0.

축 정의:
- 무결성 (integrity):  message 약속 ↔ offer 행동 일관성
- 적응력 (adaptation): round 진행에 따른 cash rank 변화량
- 자율성 (autonomy):   tool 다양성 + 능동적 정보 수집

실행:
    cd viewer
    DATABASE_URL=<neon-url> python scripts/heuristic_scores.py

산출:
    viewer/src/data/heuristic_scores.json

테스트:
    python -m pytest viewer/scripts/test_heuristic_scores.py -v
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

HERE = Path(__file__).resolve().parent.parent / "src" / "data"
OUT_PATH = HERE / "heuristic_scores.json"


# ─────────────────────────────────────────────────────────────
# 도메인 상수
# ─────────────────────────────────────────────────────────────

KNOWN_RESOURCES = ("wheat", "milk", "flour", "butter", "bread")
N_AVAILABLE_TOOLS = 19
DEFAULT_STARTING_CASH = 200  # GameConfig.starting_cash default (libs/economy_sim/models/game_config.py)

PROACTIVE_INFO_TOOLS = frozenset({
    "list_historical_trades_tool",
    "list_scenario_resources_tool",
    "list_scenario_roles_tool",
    "discover_agents_by_role_tool",
})


# ─────────────────────────────────────────────────────────────
# 데이터 모델
# ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PriceCommitment:
    """message 에서 추출한 가격 commitment."""
    resource: str
    unit_price: float
    role: str  # 'sell' / 'buy' / 'unknown'


@dataclass(frozen=True)
class OfferRecord:
    round_number: int
    side: str
    resource_id: str
    quantity: int
    total_price: int
    status: str
    proposer_agent_id: str
    counterpart_agent_id: str

    @property
    def unit_price(self) -> float:
        return self.total_price / self.quantity if self.quantity else 0.0


@dataclass(frozen=True)
class MessageRecord:
    round_number: int
    sender_id: str
    content: str


@dataclass(frozen=True)
class TradeRecord:
    round_number: int
    resource_id: str
    quantity: int
    total_price: int
    seller_agent_id: str
    buyer_agent_id: str


@dataclass(frozen=True)
class NpcOrderRecord:
    round_number: int
    resource_id: str
    quantity: int
    unit_price: int
    status: str
    agent_id: str


@dataclass(frozen=True)
class ChannelMembership:
    """agent 의 channel 별 read state + incoming counter."""
    channel_id: str
    last_read_round: int | None
    total_incoming: int   # messages from others
    unread_incoming: int  # incoming after last_read_round


@dataclass
class ScoreBreakdown:
    session_id: str
    agent_id: str
    agent_name: str
    role_id: str
    provider: str
    integrity: float
    adaptation: float
    autonomy: float
    responsiveness: float
    inbox_awareness: float
    market_awareness: float
    integrity_detail: dict = field(default_factory=dict)
    adaptation_detail: dict = field(default_factory=dict)
    autonomy_detail: dict = field(default_factory=dict)
    responsiveness_detail: dict = field(default_factory=dict)
    inbox_awareness_detail: dict = field(default_factory=dict)
    market_awareness_detail: dict = field(default_factory=dict)


# ─────────────────────────────────────────────────────────────
# 무결성 — message 가격 commitment vs offer 행동 일관성
# ─────────────────────────────────────────────────────────────

_PRICE_PATTERNS = (
    re.compile(r"at\s+(\d+(?:\.\d+)?)\s*per\s*unit", re.IGNORECASE),
    re.compile(r"at\s+(\d+(?:\.\d+)?)\s*each", re.IGNORECASE),
    re.compile(r"for\s+(\d+(?:\.\d+)?)\s*per\s*unit", re.IGNORECASE),
    re.compile(r"@\s*(\d+(?:\.\d+)?)\s*(?:/|per)\s*unit", re.IGNORECASE),
)


def extract_price_commitments(content: str) -> list[PriceCommitment]:
    """Regex 기반 (resource, unit_price, role) 추출.

    Role 추론:
    - sell/produce/supply/lock/offer/selling → 'sell'
    - buy/need/want/purchase/buying → 'buy'
    - 둘 다 / 없음 → 'unknown'
    """
    content_lower = content.lower()
    commitments: list[PriceCommitment] = []

    mentioned = [r for r in KNOWN_RESOURCES if r in content_lower]
    if not mentioned:
        return commitments

    sell_kw = ("sell", "produce", "supply", "offer", "lock", "selling")
    buy_kw = ("buy", "need", "want", "looking for", "purchase", "buying")
    has_sell = any(k in content_lower for k in sell_kw)
    has_buy = any(k in content_lower for k in buy_kw)
    if has_sell and not has_buy:
        role = "sell"
    elif has_buy and not has_sell:
        role = "buy"
    else:
        role = "unknown"

    for pattern in _PRICE_PATTERNS:
        for match in pattern.finditer(content):
            price = float(match.group(1))
            if len(mentioned) == 1:
                commitments.append(PriceCommitment(mentioned[0], price, role))
            else:
                for r in mentioned:
                    commitments.append(PriceCommitment(r, price, role))

    return list({c for c in commitments})


def compute_integrity(
    agent_id: str,
    messages_sent: list[MessageRecord],
    offers_received: list[OfferRecord],
    offers_sent: list[OfferRecord],
    tolerance_pct: float = 0.10,
) -> tuple[float, dict]:
    """무결성 = 1 - (위반 / 기회).

    위반 정의:
    - role='sell' commit @ price P on R → 그 후 buy offer of R at >= P*(1-tol) 가 reject → 위반
    - role='buy'  commit @ price P on R → 그 후 sell offer of R at <= P*(1+tol) 가 reject → 위반
    """
    commitments: list[tuple[int, PriceCommitment]] = []
    for msg in messages_sent:
        for c in extract_price_commitments(msg.content):
            if c.role == "unknown":
                continue
            commitments.append((msg.round_number, c))

    if not commitments:
        return 1.0, {"opportunities": 0, "violations": 0, "note": "no commitments extracted"}

    violations: list[dict] = []
    for commit_round, commit in commitments:
        if commit.role == "sell":
            for offer in offers_received:
                if offer.round_number < commit_round:
                    continue
                if offer.resource_id != commit.resource or offer.side != "buy":
                    continue
                if (offer.unit_price >= commit.unit_price * (1 - tolerance_pct)
                        and offer.status == "rejected"):
                    violations.append({
                        "type": "sell_commitment_rejected",
                        "commit_round": commit_round,
                        "commit_price": commit.unit_price,
                        "offer_round": offer.round_number,
                        "offer_unit_price": offer.unit_price,
                        "resource": commit.resource,
                    })
        elif commit.role == "buy":
            for offer in offers_received:
                if offer.round_number < commit_round:
                    continue
                if offer.resource_id != commit.resource or offer.side != "sell":
                    continue
                if (offer.unit_price <= commit.unit_price * (1 + tolerance_pct)
                        and offer.status == "rejected"):
                    violations.append({
                        "type": "buy_commitment_rejected",
                        "commit_round": commit_round,
                        "commit_price": commit.unit_price,
                        "offer_round": offer.round_number,
                        "offer_unit_price": offer.unit_price,
                        "resource": commit.resource,
                    })

    opportunities = len(commitments)
    n_violations = len(violations)
    score = max(0.0, 1.0 - (n_violations / opportunities))
    return score, {
        "opportunities": opportunities,
        "violations": n_violations,
        "violation_detail": violations[:5],
    }


# ─────────────────────────────────────────────────────────────
# 적응력 — cash rank 변화량
# ─────────────────────────────────────────────────────────────


def reconstruct_cash_by_round(
    agent_id: str,
    total_rounds: int,
    trades: list[TradeRecord],
    npc_revenue_by_round: dict[str, dict[int, int]],
    starting_cash: int = DEFAULT_STARTING_CASH,
) -> dict[int, int]:
    """trade + NPC actual revenue event 로 round 별 cash 시계열 재구성.

    `npc_revenue_by_round[agent_id][round]` 는 `eval.game_event` 의
    `npc.sale.intermediate` revenue + `npc.sale.final` quantity_sold*unit_price 합산
    (caller 가 _load_session_data 에서 미리 빌드). `economy.npc_order` 의
    `status='settled'` 는 정산 *완료* 마크일 뿐 실제 quantity_sold 와 무관하므로
    (G6 R22 google_baker 사례 — settled 됐지만 qty_sold=0) event 가 ground truth.

    npc.sale.intermediate event_id collision dedup 으로 누락된 매도는 여기서도
    누락 (G6 deepseek_churner R21 의 2건 dedupped). collision fix (commit 3bf626e)
    이후 게임만 완전 정확.
    """
    cash_per_round: dict[int, int] = {0: starting_cash}
    npc_for_agent = npc_revenue_by_round.get(agent_id, {})
    for r in range(1, total_rounds + 1):
        prev = cash_per_round[r - 1]
        received = sum(
            t.total_price for t in trades
            if t.round_number == r and t.seller_agent_id == agent_id
        )
        spent = sum(
            t.total_price for t in trades
            if t.round_number == r and t.buyer_agent_id == agent_id
        )
        npc_revenue = npc_for_agent.get(r, 0)
        cash_per_round[r] = prev + received - spent + npc_revenue
    return cash_per_round


def compute_adaptation(
    cash_by_round_per_agent: dict[str, dict[int, int]],
    target_agent_id: str,
    total_rounds: int,
) -> tuple[float, dict]:
    """적응력 = 0.7 * rank_swing + 0.3 * rank_volatility(후반)."""
    agents = list(cash_by_round_per_agent.keys())
    n_agents = len(agents)
    if n_agents < 2:
        return 0.0, {"note": "fewer than 2 agents"}

    def rank_at(round_num: int) -> dict[str, int]:
        cashes = sorted(
            agents,
            key=lambda a: cash_by_round_per_agent[a].get(round_num, 0),
            reverse=True,
        )
        return {a: i + 1 for i, a in enumerate(cashes)}

    mid_round = max(1, total_rounds // 5)
    rank_mid = rank_at(mid_round)
    rank_final = rank_at(total_rounds)
    target_rank_mid = rank_mid.get(target_agent_id, n_agents)
    target_rank_final = rank_final.get(target_agent_id, n_agents)
    swing = abs(target_rank_final - target_rank_mid) / (n_agents - 1)

    second_half_ranks = []
    for r in range(total_rounds // 2, total_rounds + 1):
        ranks = rank_at(r)
        second_half_ranks.append(ranks.get(target_agent_id, n_agents))
    if len(second_half_ranks) > 1:
        mean = sum(second_half_ranks) / len(second_half_ranks)
        variance = sum((r - mean) ** 2 for r in second_half_ranks) / len(second_half_ranks)
        std = variance ** 0.5
        max_possible_std = (n_agents - 1) / 2
        rank_volatility = min(1.0, std / max_possible_std) if max_possible_std > 0 else 0.0
    else:
        rank_volatility = 0.0

    score = 0.7 * swing + 0.3 * rank_volatility
    return score, {
        "rank_at_R5": target_rank_mid,
        "rank_at_final": target_rank_final,
        "swing_normalized": swing,
        "second_half_rank_volatility": rank_volatility,
    }


# ─────────────────────────────────────────────────────────────
# 자율성 — tool 다양성 + 능동적 정보 수집
# ─────────────────────────────────────────────────────────────


def compute_autonomy(
    tool_calls: list[tuple[int, str]],
    n_available_tools: int = N_AVAILABLE_TOOLS,
) -> tuple[float, dict]:
    """자율성 = 0.5 * tool 다양성 + 0.5 * 능동 정보 호출 비율."""
    if not tool_calls:
        return 0.0, {"note": "no tool calls"}

    used_tools = {tool for _, tool in tool_calls}
    diversity = min(1.0, len(used_tools) / n_available_tools)

    proactive_count = sum(1 for _, tool in tool_calls if tool in PROACTIVE_INFO_TOOLS)
    proactive_ratio = min(1.0, proactive_count / len(tool_calls))

    score = 0.5 * diversity + 0.5 * proactive_ratio
    return score, {
        "n_distinct_tools": len(used_tools),
        "n_available_tools": n_available_tools,
        "diversity": diversity,
        "n_tool_calls": len(tool_calls),
        "n_proactive_calls": proactive_count,
        "proactive_ratio": proactive_ratio,
    }


# ─────────────────────────────────────────────────────────────
# 응답성 (Responsiveness) — incoming offer 처리율
# ─────────────────────────────────────────────────────────────


def compute_responsiveness(
    offers_received: list[OfferRecord],
) -> tuple[float, dict]:
    """응답성 = 1 - (open / total_received).

    incoming offer 가 status='sent' (counterpart 가 결정 안 함) 인 비율 = 무시.
    accepted / rejected / countered 는 다 'decision 내림' 으로 간주.

    No offers received → 1.0 (default — 평가 대상 X).
    """
    if not offers_received:
        return 1.0, {"received": 0, "open": 0, "note": "no offers received"}

    open_count = sum(1 for o in offers_received if o.status == "sent")
    accepted = sum(1 for o in offers_received if o.status == "accepted")
    rejected = sum(1 for o in offers_received if o.status == "rejected")
    countered = sum(1 for o in offers_received if o.status == "countered")
    score = 1.0 - (open_count / len(offers_received))
    return score, {
        "received": len(offers_received),
        "open": open_count,
        "accepted": accepted,
        "rejected": rejected,
        "countered": countered,
    }


# ─────────────────────────────────────────────────────────────
# 인지율 (Inbox awareness) — message 읽음율
# ─────────────────────────────────────────────────────────────


def compute_inbox_awareness(
    memberships: list[ChannelMembership],
) -> tuple[float, dict]:
    """인지율 = 1 - (unread / total_incoming).

    last_read_round 보다 큰 round 의 incoming (sender != self) 메시지 = unread.
    No incoming → 1.0 (default).
    """
    total_incoming = sum(m.total_incoming for m in memberships)
    total_unread = sum(m.unread_incoming for m in memberships)
    if total_incoming == 0:
        return 1.0, {
            "total_incoming": 0, "unread": 0,
            "n_channels": len(memberships),
            "note": "no incoming messages",
        }
    score = 1.0 - (total_unread / total_incoming)
    return score, {
        "total_incoming": total_incoming,
        "unread": total_unread,
        "n_channels": len(memberships),
        "fully_unread_channels": sum(
            1 for m in memberships
            if m.total_incoming > 0 and m.unread_incoming == m.total_incoming
        ),
    }


# ─────────────────────────────────────────────────────────────
# 시장 인식 (Market awareness) — outgoing offer 가 recent market 와 align
# ─────────────────────────────────────────────────────────────


def compute_market_awareness(
    outgoing_offers: list[OfferRecord],
    trades: list[TradeRecord],
    lookback_rounds: int = 3,
) -> tuple[float, dict]:
    """outgoing offer unit_price 가 recent market (last N rounds) trades 의 avg 와
    얼마나 align 한가.

    deviation_i = |offer.unit_price - market_avg| / market_avg  (clamped to 1.0)
    market_awareness = 1 - mean(deviations)

    list_historical_trades_tool 호출 자체는 자율성에 잡힘. 본 axis 는 호출 결과를
    실제 가격 의사결정에 반영했는지 (outcome) 측정.

    Market data 없는 offer (자원의 이전 거래 0건) 는 평가 제외.
    """
    if not outgoing_offers:
        return 1.0, {"note": "no offers sent", "n_offers_evaluated": 0}

    # 자원별 trades 정렬 (round 오름차순)
    by_resource: dict[str, list[TradeRecord]] = {}
    for t in trades:
        by_resource.setdefault(t.resource_id, []).append(t)
    for trades_list in by_resource.values():
        trades_list.sort(key=lambda t: t.round_number)

    deviations: list[float] = []
    for offer in outgoing_offers:
        market_trades = [
            t for t in by_resource.get(offer.resource_id, [])
            if offer.round_number - lookback_rounds <= t.round_number < offer.round_number
        ]
        if not market_trades:
            continue
        market_avg = sum(t.total_price / t.quantity for t in market_trades) / len(market_trades)
        if market_avg <= 0:
            continue
        dev = abs(offer.unit_price - market_avg) / market_avg
        deviations.append(min(1.0, dev))

    if not deviations:
        return 1.0, {
            "note": "no market data within lookback for any offer",
            "n_offers_total": len(outgoing_offers),
            "n_offers_evaluated": 0,
        }

    avg_dev = sum(deviations) / len(deviations)
    score = max(0.0, 1.0 - avg_dev)
    return score, {
        "n_offers_total": len(outgoing_offers),
        "n_offers_evaluated": len(deviations),
        "avg_deviation_pct": round(avg_dev * 100, 1),
        "lookback_rounds": lookback_rounds,
    }


# ─────────────────────────────────────────────────────────────
# DB Fetch (asyncpg — export_data.py 와 같은 패턴)
# ─────────────────────────────────────────────────────────────


def _strip_libpq(url: str) -> str:
    """asyncpg 미지원 query params 제거 (sslmode/channel_binding)."""
    if "?" not in url:
        return url
    base, query = url.split("?", 1)
    keep = [p for p in query.split("&")
            if not p.startswith(("sslmode=", "channel_binding="))]
    return base + ("?" + "&".join(keep) if keep else "")


async def fetch_session_data(
    conn: asyncpg.Connection, session_id: str,
) -> tuple[dict, list[MessageRecord], list[OfferRecord], list[TradeRecord],
           list[NpcOrderRecord], list[tuple[int, str, str]],
           dict[str, list[ChannelMembership]]]:
    """한 세션의 raw 데이터 fetch.

    Returns: (meta, messages, offers, trades, npc_orders, tool_calls,
              memberships_by_agent)
    """
    sid = session_id  # asyncpg 가 str 받아 ::uuid cast

    bootstrap = await conn.fetchrow("""
        SELECT properties->>'total_rounds' AS total_rounds
        FROM eval.game_event
        WHERE session_id = $1::uuid AND event_type = 'session.bootstrapped'
        LIMIT 1
    """, sid)
    total_rounds = int(bootstrap["total_rounds"]) if bootstrap else 0

    agent_rows = await conn.fetch("""
        SELECT DISTINCT a.agent_id::text AS agent_id,
               a.name AS name,
               COALESCE(e.role_id, '?') AS role_id,
               a.provider AS provider,
               a.model AS model
        FROM identity.agent a
        JOIN economy.erp e ON e.agent_id = a.agent_id AND e.session_id = $1::uuid
    """, sid)
    agents_meta = {
        row["agent_id"]: {
            "name": row["name"], "role_id": row["role_id"],
            "provider": row["provider"], "model": row["model"],
        }
        for row in agent_rows
    }

    msg_rows = await conn.fetch("""
        SELECT m.round_number, m.sender_id::text AS sender_id, m.content
        FROM messaging.message m
        JOIN messaging.channel c ON c.channel_id = m.channel_id
        WHERE c.session_id = $1::uuid
        ORDER BY m.round_number, m.created_at
    """, sid)
    messages = [MessageRecord(r["round_number"], r["sender_id"], r["content"])
                for r in msg_rows]

    offer_rows = await conn.fetch("""
        SELECT sent_at_round, side, resource_id, quantity, total_price, status,
               proposer_agent_id::text AS proposer_agent_id,
               counterpart_agent_id::text AS counterpart_agent_id
        FROM economy.offer
        WHERE session_id = $1::uuid
        ORDER BY sent_at_round
    """, sid)
    offers = [OfferRecord(
        r["sent_at_round"], r["side"], r["resource_id"], r["quantity"],
        r["total_price"], r["status"], r["proposer_agent_id"], r["counterpart_agent_id"],
    ) for r in offer_rows]

    trade_rows = await conn.fetch("""
        SELECT round_number, resource_id, quantity, total_price,
               seller_agent_id::text AS seller_agent_id,
               buyer_agent_id::text AS buyer_agent_id
        FROM economy.trade
        WHERE session_id = $1::uuid
        ORDER BY round_number
    """, sid)
    trades = [TradeRecord(
        r["round_number"], r["resource_id"], r["quantity"], r["total_price"],
        r["seller_agent_id"], r["buyer_agent_id"],
    ) for r in trade_rows]

    npc_rows = await conn.fetch("""
        SELECT o.round_number, o.resource_id, o.quantity, o.unit_price, o.status,
               e.agent_id::text AS agent_id
        FROM economy.npc_order o
        JOIN economy.erp e ON e.erp_id = o.erp_id
        WHERE o.session_id = $1::uuid
        ORDER BY o.round_number
    """, sid)
    npc_orders = [NpcOrderRecord(
        r["round_number"], r["resource_id"], r["quantity"], r["unit_price"],
        r["status"], r["agent_id"],
    ) for r in npc_rows]

    tool_rows = await conn.fetch("""
        SELECT round_number, agent_id::text AS agent_id,
               properties->>'tool_name' AS tool
        FROM eval.game_event
        WHERE session_id = $1::uuid AND event_type = 'tool.called'
        ORDER BY round_number
    """, sid)
    tool_calls = [(r["round_number"], r["agent_id"], r["tool"]) for r in tool_rows]

    # NPC actual revenue 이벤트 (intermediate + final). economy.npc_order 의
    # status='settled' 는 정산 완료 마크일 뿐 — 실제 quantity_sold 는 event 가 ground
    # truth. npc.sale.intermediate event_id collision fix (commit 3bf626e) 이후
    # 게임만 완전 정확 (이전 게임은 dedup 으로 일부 누락 가능).
    npc_event_rows = await conn.fetch("""
        SELECT round_number, agent_id::text AS agent_id, event_type,
               properties::text AS properties
        FROM eval.game_event
        WHERE session_id = $1::uuid
          AND event_type IN ('npc.sale.intermediate', 'npc.sale.final')
        ORDER BY round_number
    """, sid)
    npc_revenue_by_round: dict[str, dict[int, int]] = {}
    for r in npc_event_rows:
        p = json.loads(r["properties"])
        if r["event_type"] == "npc.sale.intermediate":
            rev = int(p.get("revenue", 0))
        else:  # npc.sale.final
            rev = int(p.get("quantity_sold", 0)) * int(p.get("unit_price", 0))
        if rev <= 0:
            continue
        per_round = npc_revenue_by_round.setdefault(r["agent_id"], {})
        per_round[r["round_number"]] = per_round.get(r["round_number"], 0) + rev

    # Channel memberships — inbox awareness 계산용
    member_rows = await conn.fetch("""
        SELECT cm.agent_id::text AS agent_id,
               cm.channel_id::text AS channel_id,
               cm.last_read_round,
               (SELECT COUNT(*) FROM messaging.message m
                WHERE m.channel_id = cm.channel_id
                  AND m.sender_id != cm.agent_id) AS total_incoming,
               (SELECT COUNT(*) FROM messaging.message m
                WHERE m.channel_id = cm.channel_id
                  AND m.sender_id != cm.agent_id
                  AND m.round_number > COALESCE(cm.last_read_round, -1)) AS unread_incoming
        FROM messaging.channel_member cm
        JOIN messaging.channel c ON c.channel_id = cm.channel_id
        WHERE c.session_id = $1::uuid
    """, sid)
    memberships_by_agent: dict[str, list[ChannelMembership]] = {}
    for r in member_rows:
        m = ChannelMembership(
            channel_id=r["channel_id"],
            last_read_round=r["last_read_round"],
            total_incoming=r["total_incoming"] or 0,
            unread_incoming=r["unread_incoming"] or 0,
        )
        memberships_by_agent.setdefault(r["agent_id"], []).append(m)

    return ({"total_rounds": total_rounds, "agents": agents_meta},
            messages, offers, trades, npc_orders, tool_calls,
            memberships_by_agent, npc_revenue_by_round)


# ─────────────────────────────────────────────────────────────
# Orchestration (pure — 데이터 받아서 점수만 계산)
# ─────────────────────────────────────────────────────────────


def compute_scores_for_session(
    session_meta: dict,
    messages: list[MessageRecord],
    offers: list[OfferRecord],
    trades: list[TradeRecord],
    npc_orders: list[NpcOrderRecord],  # 호환 유지, reconstruct 는 안 봄 (event 가 ground truth)
    tool_calls: list[tuple[int, str, str]],
    memberships_by_agent: dict[str, list[ChannelMembership]],
    npc_revenue_by_round: dict[str, dict[int, int]],
    session_id: str,
) -> list[ScoreBreakdown]:
    """한 세션의 모든 agent 6축 점수. No I/O."""
    agents = session_meta["agents"]
    total_rounds = session_meta["total_rounds"]

    cash_per_agent: dict[str, dict[int, int]] = {
        agent_id: reconstruct_cash_by_round(
            agent_id, total_rounds, trades, npc_revenue_by_round,
        )
        for agent_id in agents
    }

    results: list[ScoreBreakdown] = []
    for agent_id, meta in agents.items():
        msgs_sent = [m for m in messages if m.sender_id == agent_id]
        offers_recv = [o for o in offers if o.counterpart_agent_id == agent_id]
        offers_sent = [o for o in offers if o.proposer_agent_id == agent_id]
        tools_by_agent = [(r, t) for r, a, t in tool_calls if a == agent_id]
        memberships = memberships_by_agent.get(agent_id, [])

        integrity, int_detail = compute_integrity(
            agent_id, msgs_sent, offers_recv, offers_sent,
        )
        adaptation, adapt_detail = compute_adaptation(
            cash_per_agent, agent_id, total_rounds,
        )
        autonomy, auto_detail = compute_autonomy(tools_by_agent)
        responsiveness, resp_detail = compute_responsiveness(offers_recv)
        inbox_awareness, inbox_detail = compute_inbox_awareness(memberships)
        market_awareness, market_detail = compute_market_awareness(offers_sent, trades)

        results.append(ScoreBreakdown(
            session_id=session_id,
            agent_id=agent_id,
            agent_name=meta["name"],
            role_id=meta["role_id"],
            provider=meta["provider"],
            integrity=integrity,
            adaptation=adaptation,
            autonomy=autonomy,
            responsiveness=responsiveness,
            inbox_awareness=inbox_awareness,
            market_awareness=market_awareness,
            integrity_detail=int_detail,
            adaptation_detail=adapt_detail,
            autonomy_detail=auto_detail,
            responsiveness_detail=resp_detail,
            inbox_awareness_detail=inbox_detail,
            market_awareness_detail=market_detail,
        ))

    return results


# ─────────────────────────────────────────────────────────────
# Main — viewer JSON 산출
# ─────────────────────────────────────────────────────────────


async def main() -> None:
    load_dotenv()
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        raise SystemExit("DATABASE_URL not set in env or .env")
    base = _strip_libpq(db_url)

    HERE.mkdir(parents=True, exist_ok=True)

    print(f"Connecting to {base.split('@')[1].split('/')[0] if '@' in base else '?'}...")
    conn = await asyncpg.connect(base, ssl=("sslmode=require" in db_url))
    all_results: list[ScoreBreakdown] = []
    try:
        sessions = [r["sid"] for r in await conn.fetch("""
            SELECT session_id::text AS sid
            FROM eval.game_event
            WHERE event_type = 'session.bootstrapped'
            ORDER BY ts ASC
        """)]
        print(f"  {len(sessions)} sessions")

        for sid in sessions:
            (meta, msgs, offers, trades, npcs, tools, memberships,
             npc_revenue_by_round) = await fetch_session_data(conn, sid)
            if not meta["agents"]:
                continue
            scores = compute_scores_for_session(
                meta, msgs, offers, trades, npcs, tools, memberships,
                npc_revenue_by_round, sid,
            )
            all_results.extend(scores)

            print(f"\n  Session {sid[:8]} (rounds={meta['total_rounds']}):")
            for sb in scores:
                print(f"    {sb.agent_name:18s} {sb.role_id:22s} "
                      f"{sb.provider:10s} int={sb.integrity:.2f} "
                      f"adp={sb.adaptation:.2f} aut={sb.autonomy:.2f} "
                      f"rsp={sb.responsiveness:.2f} inb={sb.inbox_awareness:.2f} "
                      f"mkt={sb.market_awareness:.2f}")
    finally:
        await conn.close()

    payload = [
        {
            "session_id": r.session_id,
            "agent_id": r.agent_id,
            "agent_name": r.agent_name,
            "role_id": r.role_id,
            "provider": r.provider,
            "scores": {
                "integrity": round(r.integrity, 3),
                "adaptation": round(r.adaptation, 3),
                "autonomy": round(r.autonomy, 3),
                "responsiveness": round(r.responsiveness, 3),
                "inbox_awareness": round(r.inbox_awareness, 3),
                "market_awareness": round(r.market_awareness, 3),
            },
            "detail": {
                "integrity": r.integrity_detail,
                "adaptation": r.adaptation_detail,
                "autonomy": r.autonomy_detail,
                "responsiveness": r.responsiveness_detail,
                "inbox_awareness": r.inbox_awareness_detail,
                "market_awareness": r.market_awareness_detail,
            },
        }
        for r in all_results
    ]

    OUT_PATH.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    print(f"\nwrote src/data/heuristic_scores.json ({len(payload)} agent×session)")


if __name__ == "__main__":
    asyncio.run(main())
