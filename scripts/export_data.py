"""Neon PostgreSQL → viewer JSON export (sanitized).

generate_mock_data.py 와 같은 schema 형태 — viewer 는 데이터 소스 무관 동작.

실행:
    cd viewer
    DATABASE_URL=<neon-url> python scripts/export_data.py

산출 (viewer/src/data/):
    games.json / agents.json / trades.json / offers.json /
    messages.json / event_stats.json / leaderboard.json / meta.json

Sanitize 정책 (methodology 페이지와 일치):
- ✓ 공개: model/provider, name (label), trade/offer/message, event 통계, token usage
- ✗ 비공개: prompt 본문, api_key_encrypted, reasoning 원문

운영 정책:
- DATABASE_URL 은 read-only role 사용 권장 (script 에서 INSERT 안 함이라 read 권한 충분)
- Public repo 푸시 전 산출 JSON 에 비공개 항목 잔존 X 검증 (visual review)
"""
from __future__ import annotations

import asyncio
import json
import os
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import asyncpg
from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent.parent / "src" / "data"

# ──────────────────────────────────────────────────────────────────────
# Sanitize whitelists — public 컬럼만 (CHECK against schema 변경 시)
# ──────────────────────────────────────────────────────────────────────

AGENT_PUBLIC_COLS = ("agent_id", "name", "role_id", "scenario_id",
                     "provider", "model_id", "prompt_version_id")
GAME_PUBLIC_COLS = ("session_id", "scenario_id", "total_rounds", "rounds_completed",
                    "started_at", "ended_at", "agent_ids", "final_cash",
                    "trade_count", "message_count", "event_count")
TRADE_PUBLIC_COLS = ("trade_id", "session_id", "round_number", "seller_agent_id",
                     "buyer_agent_id", "resource_id", "quantity", "total_price",
                     "unit_price", "created_at")
OFFER_PUBLIC_COLS = ("offer_id", "session_id", "round_number", "side",
                     "proposer_agent_id", "counterpart_agent_id", "resource_id",
                     "quantity", "total_price", "status", "created_at")
MESSAGE_PUBLIC_COLS = ("message_id", "session_id", "round_number", "channel_id",
                       "sender_id", "content", "created_at")


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────


def _iso(t: Any) -> str:
    if isinstance(t, datetime):
        return t.astimezone(UTC).isoformat().replace("+00:00", "Z")
    return str(t)


def _strip_libpq(url: str) -> str:
    """asyncpg 미지원 query params 제거 (sslmode/channel_binding)."""
    if "?" not in url:
        return url
    base, query = url.split("?", 1)
    keep = [p for p in query.split("&")
            if not p.startswith(("sslmode=", "channel_binding="))]
    return base + ("?" + "&".join(keep) if keep else "")


# ──────────────────────────────────────────────────────────────────────
# Queries
# ──────────────────────────────────────────────────────────────────────


async def fetch_agents(conn: asyncpg.Connection) -> list[dict]:
    """identity.agent + identity.agent_prompt_version 조인.

    prompt_text 는 절대 SELECT 안 함 (sanitize). name / model / provider 만.
    """
    rows = await conn.fetch("""
        SELECT
            a.agent_id::text                      AS agent_id,
            a.name                                AS name,
            COALESCE(e.role_id, '?')              AS role_id,
            COALESCE(e.scenario_id, '?')          AS scenario_id,
            a.provider                            AS provider,
            a.model                               AS model_id,
            a.current_prompt_version_id::text     AS prompt_version_id
        FROM identity.agent a
        LEFT JOIN economy.erp e
          ON e.agent_id = a.agent_id
        WHERE a.mode = 'llm'
        ORDER BY a.created_at
    """)
    # 중복 제거 (한 agent 가 여러 ERP 갖는 경우)
    seen: dict[str, dict] = {}
    for r in rows:
        seen.setdefault(r["agent_id"], dict(r))
    return list(seen.values())


async def fetch_games(conn: asyncpg.Connection) -> list[dict]:
    """게임 list — bootstrap 시 emit 된 session.bootstrapped 이벤트 기반."""
    rows = await conn.fetch("""
        SELECT
            session_id::text                                          AS session_id,
            (properties->>'scenario_id')                              AS scenario_id,
            (properties->>'total_rounds')::int                        AS total_rounds,
            ts                                                        AS started_at
        FROM eval.game_event
        WHERE event_type = 'session.bootstrapped'
        ORDER BY ts DESC
    """)

    games = []
    for r in rows:
        sid = r["session_id"]

        # ended_at = 마지막 round.ended timestamp
        end_row = await conn.fetchrow("""
            SELECT ts, round_number FROM eval.game_event
            WHERE session_id = $1::uuid AND event_type = 'round.ended'
            ORDER BY round_number DESC LIMIT 1
        """, sid)
        rounds_completed = end_row["round_number"] if end_row else 0
        ended_at = end_row["ts"] if end_row else r["started_at"]

        # agents in this session
        agent_ids_rows = await conn.fetch(
            "SELECT DISTINCT agent_id::text FROM economy.erp WHERE session_id = $1::uuid", sid,
        )
        agent_ids = [a["agent_id"] for a in agent_ids_rows]

        # final cash per agent — economy.erp 는 append-only, agent 별 최신 row
        cash_rows = await conn.fetch(
            """
            SELECT DISTINCT ON (agent_id) agent_id::text AS agent_id, cash
            FROM economy.erp
            WHERE session_id = $1::uuid
            ORDER BY agent_id, created_at DESC
            """,
            sid,
        )
        final_cash = {c["agent_id"]: c["cash"] for c in cash_rows}

        trade_count = await conn.fetchval(
            "SELECT count(*) FROM economy.trade WHERE session_id = $1::uuid", sid,
        )
        # message 는 channel 통해 session 매핑
        msg_count = await conn.fetchval(
            """
            SELECT count(*)
            FROM messaging.message m
            JOIN messaging.channel ch ON ch.channel_id = m.channel_id
            WHERE ch.session_id = $1::uuid
            """,
            sid,
        ) or 0
        event_count = await conn.fetchval(
            "SELECT count(*) FROM eval.game_event WHERE session_id = $1::uuid", sid,
        )

        games.append({
            "session_id": sid,
            "scenario_id": r["scenario_id"] or "?",
            "total_rounds": r["total_rounds"] or 0,
            "rounds_completed": rounds_completed,
            "started_at": _iso(r["started_at"]),
            "ended_at": _iso(ended_at),
            "agent_ids": agent_ids,
            "final_cash": final_cash,
            "trade_count": trade_count,
            "message_count": msg_count,
            "event_count": event_count,
        })
    return games


async def fetch_trades(conn: asyncpg.Connection) -> list[dict]:
    rows = await conn.fetch("""
        SELECT
            trade_id::text          AS trade_id,
            session_id::text        AS session_id,
            round_number,
            seller_agent_id::text   AS seller_agent_id,
            buyer_agent_id::text    AS buyer_agent_id,
            resource_id, quantity, total_price,
            CASE WHEN quantity > 0 THEN total_price / quantity ELSE 0 END AS unit_price,
            created_at
        FROM economy.trade
        ORDER BY created_at
    """)
    return [{**dict(r), "created_at": _iso(r["created_at"])} for r in rows]


async def fetch_offers(conn: asyncpg.Connection) -> list[dict]:
    rows = await conn.fetch("""
        SELECT
            offer_id::text             AS offer_id,
            session_id::text           AS session_id,
            sent_at_round              AS round_number,
            side,
            proposer_agent_id::text    AS proposer_agent_id,
            counterpart_agent_id::text AS counterpart_agent_id,
            resource_id, quantity, total_price, status, created_at
        FROM economy.offer
        ORDER BY created_at
    """)
    return [{**dict(r), "created_at": _iso(r["created_at"])} for r in rows]


async def fetch_messages(conn: asyncpg.Connection) -> list[dict]:
    rows = await conn.fetch("""
        SELECT
            m.message_id::text   AS message_id,
            ch.session_id::text  AS session_id,
            m.channel_id::text   AS channel_id,
            m.sender_id::text    AS sender_id,
            m.content, m.round_number, m.created_at
        FROM messaging.message m
        JOIN messaging.channel ch ON ch.channel_id = m.channel_id
        ORDER BY m.created_at
    """)
    return [
        {
            "message_id": r["message_id"],
            "session_id": r["session_id"],
            "round_number": r["round_number"],
            "channel_id": r["channel_id"],
            "sender_id": r["sender_id"],
            "content": r["content"],
            "created_at": _iso(r["created_at"]),
        }
        for r in rows
    ]


async def fetch_event_stats(conn: asyncpg.Connection) -> list[dict]:
    """게임당 event 통계 — raw event 데이터는 viewer JSON 에 안 박음 (크기 부담)."""
    sessions = await conn.fetch(
        "SELECT DISTINCT session_id::text AS session_id FROM eval.game_event",
    )
    stats_list = []
    for s in sessions:
        sid = s["session_id"]
        by_type_rows = await conn.fetch(
            "SELECT event_type, count(*) FROM eval.game_event "
            "WHERE session_id = $1::uuid GROUP BY event_type", sid,
        )
        by_type = {r["event_type"]: r["count"] for r in by_type_rows}

        # llm.call 상세
        llm_rows = await conn.fetch(
            "SELECT properties FROM eval.game_event "
            "WHERE session_id = $1::uuid AND event_type = 'llm.call'", sid,
        )
        by_provider: dict[str, int] = defaultdict(int)
        total_in = total_out = 0
        latencies = []
        for r in llm_rows:
            p = r["properties"]
            if isinstance(p, str):
                p = json.loads(p)
            by_provider[p.get("provider", "?")] += 1
            total_in += int(p.get("prompt_tokens", 0) or 0)
            total_out += int(p.get("completion_tokens", 0) or 0)
            if p.get("latency_ms"):
                latencies.append(int(p["latency_ms"]))
        avg_latency = sum(latencies) // len(latencies) if latencies else 0

        # tool.* 상세
        tool_called = by_type.get("tool.called", 0)
        tool_succ = by_type.get("tool.succeeded", 0)
        tool_fail = by_type.get("tool.failed", 0)
        tool_rows = await conn.fetch(
            "SELECT (properties->>'tool_name') AS tn, count(*) FROM eval.game_event "
            "WHERE session_id = $1::uuid AND event_type = 'tool.called' "
            "GROUP BY (properties->>'tool_name')", sid,
        )
        by_tool = {r["tn"]: r["count"] for r in tool_rows if r["tn"]}

        stats_list.append({
            "session_id": sid,
            "by_type": by_type,
            "llm_calls": {
                "total": len(llm_rows),
                "by_provider": dict(by_provider),
                "total_tokens_in": total_in,
                "total_tokens_out": total_out,
                "avg_latency_ms": avg_latency,
            },
            "tool_calls": {
                "total": tool_called,
                "successes": tool_succ,
                "failures": tool_fail,
                "by_tool": by_tool,
            },
        })
    return stats_list


def build_leaderboard(agents: list[dict], games: list[dict],
                      trades: list[dict], messages: list[dict],
                      event_stats: list[dict]) -> list[dict]:
    out = []
    for a in agents:
        aid = a["agent_id"]
        my_games = [g for g in games if aid in g["agent_ids"]]
        cash_total = sum(g["final_cash"].get(aid, 0) for g in my_games)
        avg_cash = cash_total / len(my_games) if my_games else 0
        n_trades = sum(
            1 for t in trades if t["seller_agent_id"] == aid or t["buyer_agent_id"] == aid
        )
        n_msgs = sum(1 for m in messages if m["sender_id"] == aid)

        # llm token / latency — agent 단위로 정확히 계산은 복잡 (event 에 agent_id 있음)
        tokens_in = tokens_out = 0
        latencies = []
        for es in event_stats:
            sid = es["session_id"]
            if sid not in {g["session_id"] for g in my_games}:
                continue
            # rough: provider 매칭 비율로 분배 (정밀 하려면 raw event 봐야)
            p = a["provider"]
            ratio = es["llm_calls"]["by_provider"].get(p, 0) / max(1, es["llm_calls"]["total"])
            tokens_in += int(es["llm_calls"]["total_tokens_in"] * ratio)
            tokens_out += int(es["llm_calls"]["total_tokens_out"] * ratio)
            latencies.append(es["llm_calls"]["avg_latency_ms"])
        avg_lat = sum(latencies) // len(latencies) if latencies else 0

        out.append({
            "agent_id": aid,
            "name": a["name"],
            "provider": a["provider"],
            "model_id": a["model_id"],
            "role_id": a["role_id"],
            "games_played": len(my_games),
            "total_cash_earned": cash_total,
            "avg_final_cash": round(avg_cash, 1),
            "trade_count": n_trades,
            "message_count": n_msgs,
            "total_tokens_in": tokens_in,
            "total_tokens_out": tokens_out,
            "avg_latency_ms": avg_lat,
        })

    out.sort(key=lambda x: x["avg_final_cash"], reverse=True)
    return out


async def main() -> None:
    load_dotenv()
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        raise SystemExit("DATABASE_URL not set in env or .env")
    base = _strip_libpq(db_url)

    HERE.mkdir(parents=True, exist_ok=True)

    print(f"Connecting to {base.split('@')[1].split('/')[0] if '@' in base else '?'}...")
    conn = await asyncpg.connect(base, ssl=("sslmode=require" in db_url))
    try:
        print("Fetching agents...")
        agents = await fetch_agents(conn)
        print(f"  {len(agents)} agents")

        print("Fetching games...")
        games = await fetch_games(conn)
        print(f"  {len(games)} games")

        print("Fetching trades...")
        trades = await fetch_trades(conn)
        print(f"  {len(trades)} trades")

        print("Fetching offers...")
        offers = await fetch_offers(conn)
        print(f"  {len(offers)} offers")

        print("Fetching messages...")
        messages = await fetch_messages(conn)
        print(f"  {len(messages)} messages")

        print("Fetching event stats...")
        event_stats = await fetch_event_stats(conn)
        print(f"  {len(event_stats)} sessions with events")
    finally:
        await conn.close()

    leaderboard = build_leaderboard(agents, games, trades, messages, event_stats)

    meta = {
        "exported_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "total_games": len(games),
        "total_agents": len(agents),
        "total_trades": len(trades),
        "total_messages": len(messages),
        "total_events": sum(g["event_count"] for g in games),
        "data_source": "neon",
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
        print(f"  wrote src/data/{name}")

    print(f"\nExport done. {len(games)} games / {len(agents)} agents / "
          f"{meta['total_events']} events.")


if __name__ == "__main__":
    asyncio.run(main())
