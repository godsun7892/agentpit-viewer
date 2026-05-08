"""30 게임 mock 데이터 생성 — viewer scaffold 검증용.

deterministic random seed 로 재현 가능. export_data.py 가 진짜 Neon 데이터로
대체할 때 같은 schema 형태.

실행:
    python viewer/scripts/generate_mock_data.py

산출:
    viewer/src/data/games.json
    viewer/src/data/agents.json
    viewer/src/data/trades.json
    viewer/src/data/offers.json
    viewer/src/data/messages.json
    viewer/src/data/leaderboard.json
    viewer/src/data/event_stats.json
    viewer/src/data/meta.json
"""
from __future__ import annotations

import json
import random
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

SEED = 20260508
RNG = random.Random(SEED)
HERE = Path(__file__).resolve().parent.parent / "src" / "data"

# ──────────────────────────────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────────────────────────────

PROVIDERS = [
    ("openai", "gpt-4o-mini"),
    ("google", "gemini-2.5-flash"),
    ("xai", "grok-3-mini"),
    ("deepseek", "deepseek-chat"),
    ("openai", "gpt-4o-mini"),  # bakery — 2번째 openai (baseline 비교)
]

ROLES = ["wheat_farm", "dairy_ranch", "mill", "dairy_processor", "bakery"]

AGENT_NAMES = [
    "patient_farmer",
    "calm_rancher",
    "quiet_miller",
    "basic_churner",
    "routine_baker",
]

RESOURCES_BY_ROLE = {
    "wheat_farm": "wheat",
    "dairy_ranch": "milk",
    "mill": "flour",
    "dairy_processor": "butter",
    "bakery": "bread",
}

# tier별 참고가 (mock 가격 분포 베이스)
RESOURCE_BASE_PRICE = {
    "wheat": 8,
    "milk": 12,
    "flour": 18,
    "butter": 22,
    "bread": 50,
}

START_TIME = datetime(2026, 5, 5, 14, 0, tzinfo=timezone.utc)

NUM_GAMES = 30


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────


def _uuid(prefix: str = "") -> str:
    """deterministic uuid v4 형태 (실제 v4 random 위에 RNG 시드 사용)."""
    h = RNG.getrandbits(128).to_bytes(16, "big")
    u = uuid.UUID(bytes=h, version=4)
    return str(u) if not prefix else f"{prefix}-{u}"


def _iso(t: datetime) -> str:
    return t.isoformat().replace("+00:00", "Z")


def _content_pool() -> list[str]:
    """협상 메시지 mock pool — 협상 깊이 다양성 위해 10여 가지 패턴."""
    return [
        "Looking to buy {resource} at {price}/each. Quantity needed: {qty}.",
        "I have {qty} units of {resource} available. Best offer welcome.",
        "Can you do {price} per unit? Stock running low here.",
        "Counter-proposal: {qty} at {price}. Accept by next round?",
        "My production is steady — can commit {qty} more this round.",
        "Price too low for me — others offering {price_high}. Match?",
        "Settled at {price}? Need to allocate inventory before bake cycle.",
        "Round {round}: still need {resource}. Anyone else producing?",
        "Confirming order: {qty} @ {price}. Will deliver same round.",
        "Pass — {price} is below my cost basis. Higher offers?",
    ]


# ──────────────────────────────────────────────────────────────────────
# Generators
# ──────────────────────────────────────────────────────────────────────


def gen_agents() -> list[dict]:
    """5 unique agents (1 per role)."""
    return [
        {
            "agent_id": _uuid("agent"),
            "name": AGENT_NAMES[i],
            "role_id": ROLES[i],
            "scenario_id": "bread",
            "provider": PROVIDERS[i][0],
            "model_id": PROVIDERS[i][1],
            "prompt_version_id": _uuid("pv"),
        }
        for i in range(5)
    ]


def gen_games(agents: list[dict]) -> tuple[list[dict], list[dict], list[dict], list[dict], list[dict]]:
    """30 게임 데이터 + trades / offers / messages / event_stats 일괄 생성."""
    games = []
    trades = []
    offers = []
    messages = []
    event_stats_list = []

    agent_ids = [a["agent_id"] for a in agents]

    # 시간이 흐름에 따라 점차 trade 늘어남 (학습 효과 시뮬레이션)
    for g_idx in range(NUM_GAMES):
        session_id = _uuid("session")
        rounds = RNG.choice([5, 6, 8, 10, 12])
        rounds_completed = rounds  # 모두 완료
        game_start = START_TIME + timedelta(hours=g_idx * 4)
        game_end = game_start + timedelta(seconds=rounds * 25)

        # 게임 후반으로 갈수록 거래 활발
        progress_factor = 0.4 + (g_idx / NUM_GAMES) * 0.6  # 0.4 → 1.0

        # ── trades ──
        n_trades = RNG.randint(int(2 * progress_factor), int(8 * progress_factor) + 1)
        game_trades = []
        for _ in range(n_trades):
            seller_idx = RNG.randint(0, 4)
            buyer_idx = RNG.randint(0, 4)
            while buyer_idx == seller_idx:
                buyer_idx = RNG.randint(0, 4)
            resource = RESOURCES_BY_ROLE[ROLES[seller_idx]]
            base = RESOURCE_BASE_PRICE[resource]
            quantity = RNG.randint(1, 5)
            unit_price = base + RNG.randint(-2, 3)
            unit_price = max(1, unit_price)
            total = unit_price * quantity
            round_num = RNG.randint(1, rounds)
            trade_time = game_start + timedelta(seconds=round_num * 25)
            t = {
                "trade_id": _uuid("trade"),
                "session_id": session_id,
                "round_number": round_num,
                "seller_agent_id": agent_ids[seller_idx],
                "buyer_agent_id": agent_ids[buyer_idx],
                "resource_id": resource,
                "quantity": quantity,
                "total_price": total,
                "unit_price": unit_price,
                "created_at": _iso(trade_time),
            }
            trades.append(t)
            game_trades.append(t)

        # ── offers (trade 의 1.5~3배) ──
        n_offers = int(n_trades * RNG.uniform(1.5, 3.0))
        for _ in range(n_offers):
            seller_idx = RNG.randint(0, 4)
            buyer_idx = RNG.randint(0, 4)
            while buyer_idx == seller_idx:
                buyer_idx = RNG.randint(0, 4)
            resource = RESOURCES_BY_ROLE[ROLES[seller_idx]]
            base = RESOURCE_BASE_PRICE[resource]
            quantity = RNG.randint(1, 5)
            unit_price = base + RNG.randint(-3, 4)
            unit_price = max(1, unit_price)
            round_num = RNG.randint(1, rounds)
            offer_time = game_start + timedelta(seconds=round_num * 25 - 5)
            status = RNG.choices(
                ["sent", "accepted", "rejected", "countered"],
                weights=[15, 30, 25, 30],
            )[0]
            offers.append({
                "offer_id": _uuid("offer"),
                "session_id": session_id,
                "round_number": round_num,
                "side": RNG.choice(["sell", "buy"]),
                "proposer_agent_id": agent_ids[seller_idx],
                "counterpart_agent_id": agent_ids[buyer_idx],
                "resource_id": resource,
                "quantity": quantity,
                "total_price": unit_price * quantity,
                "status": status,
                "created_at": _iso(offer_time),
            })

        # ── messages (협상 패턴) ──
        n_messages = RNG.randint(int(4 * progress_factor), int(15 * progress_factor) + 1)
        templates = _content_pool()
        for _ in range(n_messages):
            sender_idx = RNG.randint(0, 4)
            resource = RESOURCES_BY_ROLE[ROLES[sender_idx]]
            base = RESOURCE_BASE_PRICE[resource]
            content = RNG.choice(templates).format(
                resource=resource,
                price=base + RNG.randint(-2, 2),
                price_high=base + RNG.randint(2, 5),
                qty=RNG.randint(1, 5),
                round=RNG.randint(1, rounds),
            )
            round_num = RNG.randint(1, rounds)
            msg_time = game_start + timedelta(seconds=round_num * 25 - 10)
            messages.append({
                "message_id": _uuid("msg"),
                "session_id": session_id,
                "round_number": round_num,
                "channel_id": _uuid("ch"),
                "sender_id": agent_ids[sender_idx],
                "content": content,
                "created_at": _iso(msg_time),
            })

        # ── final cash distribution ──
        # bakery (T3) 가 평균적으로 더 벎. 변동성 추가
        base_cash = {
            "wheat_farm": 100 + RNG.randint(-30, 80),
            "dairy_ranch": 110 + RNG.randint(-30, 80),
            "mill": 130 + RNG.randint(-40, 100),
            "dairy_processor": 140 + RNG.randint(-40, 100),
            "bakery": 200 + RNG.randint(-50, 200),  # 큰 변동
        }
        final_cash = {
            agent_ids[i]: max(0, base_cash[ROLES[i]])
            for i in range(5)
        }

        # ── event stats (집계) ──
        tool_calls = RNG.randint(40, 120)
        tool_failures = RNG.randint(0, max(1, tool_calls // 20))
        llm_calls = RNG.randint(20, 60)
        tokens_in_per = RNG.randint(3000, 7000)
        tokens_out_per = RNG.randint(50, 800)

        by_provider = {}
        for a in agents:
            p = a["provider"]
            by_provider[p] = by_provider.get(p, 0) + RNG.randint(2, 12)
        # llm_calls 와 sum 일치
        diff = llm_calls - sum(by_provider.values())
        if diff != 0 and by_provider:
            k = list(by_provider.keys())[0]
            by_provider[k] = max(0, by_provider[k] + diff)

        by_tool = {
            "list_my_offers_tool": RNG.randint(5, 20),
            "send_offer_tool": RNG.randint(2, 15),
            "accept_offer_tool": RNG.randint(0, 8),
            "list_historical_trades_tool": RNG.randint(3, 12),
            "read_channel_tool": RNG.randint(2, 15),
            "send_message_tool": RNG.randint(1, 10),
            "produce_tool": RNG.randint(0, 8),
            "sell_to_npc_final_tool": RNG.randint(0, 6),
        }

        event_count = (
            tool_calls + tool_calls  # called + succeeded/failed
            + llm_calls
            + len(game_trades)
            + len(messages)
            + rounds * 2  # round.started + ended
            + 5  # agent.wake
            + 1  # session.bootstrapped
        )

        event_stats_list.append({
            "session_id": session_id,
            "by_type": {
                "tool.called": tool_calls,
                "tool.succeeded": tool_calls - tool_failures,
                "tool.failed": tool_failures,
                "llm.call": llm_calls,
                "trade.executed": len(game_trades),
                "offer.sent": n_offers,
                "message.sent": n_messages,
                "round.started": rounds,
                "round.ended": rounds,
                "agent.wake": 5,
                "session.bootstrapped": 1,
            },
            "llm_calls": {
                "total": llm_calls,
                "by_provider": by_provider,
                "total_tokens_in": tokens_in_per * llm_calls,
                "total_tokens_out": tokens_out_per * llm_calls,
                "avg_latency_ms": RNG.randint(800, 6000),
            },
            "tool_calls": {
                "total": tool_calls,
                "successes": tool_calls - tool_failures,
                "failures": tool_failures,
                "by_tool": by_tool,
            },
        })

        games.append({
            "session_id": session_id,
            "scenario_id": "bread",
            "total_rounds": rounds,
            "rounds_completed": rounds_completed,
            "started_at": _iso(game_start),
            "ended_at": _iso(game_end),
            "agent_ids": agent_ids,
            "final_cash": final_cash,
            "trade_count": len(game_trades),
            "message_count": n_messages,
            "event_count": event_count,
        })

    return games, trades, offers, messages, event_stats_list


def gen_leaderboard(agents: list[dict], games: list[dict],
                     trades: list[dict], messages: list[dict],
                     event_stats: list[dict]) -> list[dict]:
    """agent별 누적 stats."""
    out = []
    for a in agents:
        aid = a["agent_id"]
        games_played = len(games)  # mock 에선 모든 agent 가 모든 게임 참여
        total_cash = sum(g["final_cash"].get(aid, 0) for g in games)
        avg_cash = total_cash / games_played if games_played else 0
        trade_count = sum(
            1 for t in trades
            if t["seller_agent_id"] == aid or t["buyer_agent_id"] == aid
        )
        msg_count = sum(1 for m in messages if m["sender_id"] == aid)

        # llm 통계 (event_stats 의 by_provider 합산은 정확 안 함 — agent 단위로
        # 다시 분배 안 함. 대략 균등 분배 시뮬)
        provider = a["provider"]
        per_agent_llm = sum(
            es["llm_calls"]["by_provider"].get(provider, 0)
            for es in event_stats
        ) // 5  # rough
        total_in = per_agent_llm * RNG.randint(3500, 6500)
        total_out = per_agent_llm * RNG.randint(80, 600)
        avg_latency = RNG.randint(900, 5500)

        out.append({
            "agent_id": aid,
            "name": a["name"],
            "provider": provider,
            "model_id": a["model_id"],
            "role_id": a["role_id"],
            "games_played": games_played,
            "total_cash_earned": total_cash,
            "avg_final_cash": round(avg_cash, 1),
            "trade_count": trade_count,
            "message_count": msg_count,
            "total_tokens_in": total_in,
            "total_tokens_out": total_out,
            "avg_latency_ms": avg_latency,
        })

    # 정렬: avg_final_cash desc
    out.sort(key=lambda x: x["avg_final_cash"], reverse=True)
    return out


def main() -> None:
    HERE.mkdir(parents=True, exist_ok=True)

    agents = gen_agents()
    games, trades, offers, messages, event_stats = gen_games(agents)
    leaderboard = gen_leaderboard(agents, games, trades, messages, event_stats)

    meta = {
        "exported_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "total_games": len(games),
        "total_agents": len(agents),
        "total_trades": len(trades),
        "total_messages": len(messages),
        "total_events": sum(g["event_count"] for g in games),
        "data_source": "mock",
        "schema_version": 1,
    }

    files = {
        "games.json": games,
        "agents.json": agents,
        "trades.json": trades,
        "offers.json": offers,
        "messages.json": messages,
        "event_stats.json": event_stats,
        "leaderboard.json": leaderboard,
        "meta.json": meta,
    }

    for name, data in files.items():
        path = HERE / name
        path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"  wrote {path.relative_to(HERE.parent.parent)} "
              f"({len(json.dumps(data))} bytes)")

    print(f"\n✓ Mock data generated for {len(games)} games · "
          f"{len(agents)} agents · {len(trades)} trades · "
          f"{len(messages)} messages")


if __name__ == "__main__":
    main()
