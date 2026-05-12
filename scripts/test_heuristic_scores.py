"""heuristic_scores.py unit tests — pure functions only, no DB."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from heuristic_scores import (
    ChannelMembership,
    MessageRecord,
    NpcOrderRecord,
    OfferRecord,
    PriceCommitment,
    TradeRecord,
    compute_adaptation,
    compute_autonomy,
    compute_inbox_awareness,
    compute_integrity,
    compute_market_awareness,
    compute_responsiveness,
    extract_price_commitments,
    reconstruct_cash_by_round,
)

# ─────────────────────────────────────────────────────────────
# extract_price_commitments
# ─────────────────────────────────────────────────────────────


def test_extract_simple_sell_commitment() -> None:
    text = "I sell flour at 11 per unit."
    result = extract_price_commitments(text)
    assert PriceCommitment(resource="flour", unit_price=11.0, role="sell") in result


def test_extract_buy_commitment() -> None:
    text = "I need to buy wheat at 7 per unit."
    result = extract_price_commitments(text)
    assert PriceCommitment(resource="wheat", unit_price=7.0, role="buy") in result


def test_extract_each_pattern() -> None:
    text = "I'm offering butter at 17 each."
    result = extract_price_commitments(text)
    assert PriceCommitment(resource="butter", unit_price=17.0, role="sell") in result


def test_extract_lock_pattern_real_data() -> None:
    text = "I produce ~3 flour every 2 rounds. Locking 6 flour at 11 per unit?"
    result = extract_price_commitments(text)
    assert any(c.resource == "flour" and c.unit_price == 11.0 for c in result)


def test_extract_no_resource_no_commitment() -> None:
    assert extract_price_commitments("Hi, how are you?") == []


def test_extract_no_price_no_commitment() -> None:
    assert extract_price_commitments("I have lots of wheat to sell.") == []


def test_extract_unknown_role_when_both_keywords() -> None:
    text = "I sell wheat and want to buy flour at 12 per unit."
    result = extract_price_commitments(text)
    for c in result:
        assert c.role == "unknown"


def test_extract_no_role_keyword_at_all() -> None:
    text = "flour at 11 per unit"
    result = extract_price_commitments(text)
    for c in result:
        assert c.role == "unknown"


# ─────────────────────────────────────────────────────────────
# compute_integrity
# ─────────────────────────────────────────────────────────────


def test_integrity_no_commitments_perfect_score() -> None:
    score, detail = compute_integrity("A", [], [], [])
    assert score == 1.0
    assert detail["opportunities"] == 0


def test_integrity_sell_commitment_then_reject_violation() -> None:
    """§9.1 패턴: mill 의 11 sell 약속 후 11 buy offer 거절."""
    msgs = [MessageRecord(
        round_number=1, sender_id="A",
        content="I sell flour at 11 per unit.",
    )]
    offers_recv = [OfferRecord(
        round_number=4, side="buy", resource_id="flour",
        quantity=3, total_price=33, status="rejected",
        proposer_agent_id="B", counterpart_agent_id="A",
    )]
    score, detail = compute_integrity("A", msgs, offers_recv, [])
    assert score == 0.0
    assert detail["violations"] == 1
    assert detail["opportunities"] == 1


def test_integrity_sell_commitment_accepted_no_violation() -> None:
    msgs = [MessageRecord(1, "A", "I sell flour at 11 per unit.")]
    offers_recv = [OfferRecord(
        4, "buy", "flour", 3, 33, "accepted", "B", "A",
    )]
    score, _ = compute_integrity("A", msgs, offers_recv, [])
    assert score == 1.0


def test_integrity_tolerance_pct_within_range() -> None:
    msgs = [MessageRecord(1, "A", "I sell flour at 11 per unit.")]
    offers_recv = [OfferRecord(
        4, "buy", "flour", 3, 30, "rejected", "B", "A",
    )]
    score, detail = compute_integrity("A", msgs, offers_recv, [])
    assert score == 0.0
    assert detail["violations"] == 1


def test_integrity_offer_below_tolerance_not_violation() -> None:
    msgs = [MessageRecord(1, "A", "I sell flour at 11 per unit.")]
    offers_recv = [OfferRecord(
        4, "buy", "flour", 3, 27, "rejected", "B", "A",
    )]
    score, detail = compute_integrity("A", msgs, offers_recv, [])
    assert score == 1.0
    assert detail["violations"] == 0


def test_integrity_commitment_before_offer_only() -> None:
    msgs = [MessageRecord(5, "A", "I sell flour at 11 per unit.")]
    offers_recv = [OfferRecord(
        2, "buy", "flour", 3, 33, "rejected", "B", "A",
    )]
    score, detail = compute_integrity("A", msgs, offers_recv, [])
    assert detail["violations"] == 0
    assert score == 1.0


def test_integrity_buy_commitment_violation() -> None:
    msgs = [MessageRecord(1, "A", "Looking to buy wheat at 7 per unit.")]
    offers_recv = [OfferRecord(
        4, "sell", "wheat", 3, 21, "rejected", "B", "A",
    )]
    score, _ = compute_integrity("A", msgs, offers_recv, [])
    assert score == 0.0


def test_integrity_unknown_role_skipped() -> None:
    msgs = [MessageRecord(1, "A", "flour at 11 per unit")]
    offers_recv = [OfferRecord(
        4, "buy", "flour", 3, 33, "rejected", "B", "A",
    )]
    score, detail = compute_integrity("A", msgs, offers_recv, [])
    assert detail["opportunities"] == 0
    assert score == 1.0


# ─────────────────────────────────────────────────────────────
# reconstruct_cash_by_round
# ─────────────────────────────────────────────────────────────


def test_cash_no_activity_stays_starting() -> None:
    cash = reconstruct_cash_by_round("A", total_rounds=5, trades=[], npc_orders=[],
                                      starting_cash=100)
    assert all(v == 100 for v in cash.values())


def test_cash_sold_increases() -> None:
    trades = [TradeRecord(2, "wheat", 3, 21, "A", "B")]
    cash = reconstruct_cash_by_round("A", 5, trades, [], starting_cash=100)
    assert cash[2] == 121
    assert cash[5] == 121


def test_cash_bought_decreases() -> None:
    trades = [TradeRecord(3, "wheat", 3, 21, "B", "A")]
    cash = reconstruct_cash_by_round("A", 5, trades, [], starting_cash=100)
    assert cash[3] == 79


def test_cash_npc_revenue_added() -> None:
    npc = [NpcOrderRecord(4, "wheat", 3, 2, "settled", "A")]
    cash = reconstruct_cash_by_round("A", 5, [], npc, starting_cash=100)
    assert cash[4] == 106


def test_cash_npc_pending_not_added() -> None:
    npc = [NpcOrderRecord(4, "wheat", 3, 2, "pending", "A")]
    cash = reconstruct_cash_by_round("A", 5, [], npc, starting_cash=100)
    assert cash[4] == 100


def test_cash_cumulative_across_rounds() -> None:
    trades = [
        TradeRecord(1, "wheat", 3, 21, "A", "B"),
        TradeRecord(3, "wheat", 3, 21, "A", "B"),
        TradeRecord(5, "wheat", 3, 21, "A", "B"),
    ]
    cash = reconstruct_cash_by_round("A", 5, trades, [], starting_cash=100)
    assert cash[1] == 121
    assert cash[3] == 142
    assert cash[5] == 163


# ─────────────────────────────────────────────────────────────
# compute_adaptation
# ─────────────────────────────────────────────────────────────


def test_adaptation_no_rank_change_low_score() -> None:
    cash = {
        "A": {r: 100 + r for r in range(11)},
        "B": {r: 50 for r in range(11)},
        "C": {r: 30 for r in range(11)},
    }
    score, detail = compute_adaptation(cash, "A", total_rounds=10)
    assert score == 0.0
    assert detail["rank_at_R5"] == 1
    assert detail["rank_at_final"] == 1


def test_adaptation_full_reversal_high_score() -> None:
    cash = {
        "A": {r: 0 if r <= 5 else 200 for r in range(11)},
        "B": {r: 50 for r in range(11)},
        "C": {r: 100 if r <= 5 else 20 for r in range(11)},
    }
    score, detail = compute_adaptation(cash, "A", total_rounds=10)
    assert detail["rank_at_R5"] == 3
    assert detail["rank_at_final"] == 1
    assert detail["swing_normalized"] == 1.0
    assert score >= 0.7


def test_adaptation_few_agents_safe_default() -> None:
    cash = {"A": {0: 100, 1: 100}}
    score, _ = compute_adaptation(cash, "A", total_rounds=1)
    assert score == 0.0


# ─────────────────────────────────────────────────────────────
# compute_autonomy
# ─────────────────────────────────────────────────────────────


def test_autonomy_no_calls_zero() -> None:
    score, _ = compute_autonomy([])
    assert score == 0.0


def test_autonomy_max_diversity_max_proactive() -> None:
    tool_calls = [(1, "list_historical_trades_tool") for _ in range(19)]
    score, detail = compute_autonomy(tool_calls, n_available_tools=19)
    assert detail["n_distinct_tools"] == 1
    assert detail["proactive_ratio"] == 1.0
    assert 0.5 < score < 0.6


def test_autonomy_no_proactive() -> None:
    tool_calls = [(1, "send_offer_tool"), (1, "accept_offer_tool"), (2, "send_message_tool")]
    score, detail = compute_autonomy(tool_calls, n_available_tools=19)
    assert detail["proactive_ratio"] == 0.0
    assert 0.05 < score < 0.1


def test_autonomy_mixed_calls() -> None:
    tool_calls = [
        (1, "list_incoming_offers_tool"),
        (1, "list_historical_trades_tool"),
        (1, "send_offer_tool"),
        (2, "discover_agents_by_role_tool"),
        (2, "send_message_tool"),
    ]
    score, detail = compute_autonomy(tool_calls, n_available_tools=19)
    assert detail["n_distinct_tools"] == 5
    assert detail["n_proactive_calls"] == 2
    assert detail["proactive_ratio"] == 0.4
    assert abs(score - 0.332) < 0.01


# ─────────────────────────────────────────────────────────────
# compute_responsiveness
# ─────────────────────────────────────────────────────────────


def test_responsiveness_no_offers_perfect() -> None:
    score, detail = compute_responsiveness([])
    assert score == 1.0
    assert detail["received"] == 0


def test_responsiveness_all_open_zero() -> None:
    """openai gpt-4o-mini 의 100% 무시 패턴 — 점수 0.0"""
    offers = [
        OfferRecord(r, "buy", "flour", 3, 33, "sent", "P", "A")
        for r in (4, 7, 10, 13, 16)
    ]
    score, detail = compute_responsiveness(offers)
    assert score == 0.0
    assert detail["received"] == 5
    assert detail["open"] == 5


def test_responsiveness_all_accepted_perfect() -> None:
    offers = [
        OfferRecord(r, "buy", "flour", 3, 33, "accepted", "P", "A")
        for r in (4, 7, 10)
    ]
    score, _ = compute_responsiveness(offers)
    assert score == 1.0


def test_responsiveness_all_rejected_still_perfect() -> None:
    """rejected 도 decision — 점수에 영향 X (open 만 점수 깎음)."""
    offers = [
        OfferRecord(r, "buy", "flour", 3, 33, "rejected", "P", "A")
        for r in (4, 7, 10)
    ]
    score, _ = compute_responsiveness(offers)
    assert score == 1.0


def test_responsiveness_mixed() -> None:
    """3 accepted + 2 open = score 0.6"""
    offers = [
        OfferRecord(1, "buy", "f", 1, 10, "accepted", "P", "A"),
        OfferRecord(2, "buy", "f", 1, 10, "accepted", "P", "A"),
        OfferRecord(3, "buy", "f", 1, 10, "accepted", "P", "A"),
        OfferRecord(4, "buy", "f", 1, 10, "sent", "P", "A"),
        OfferRecord(5, "buy", "f", 1, 10, "sent", "P", "A"),
    ]
    score, detail = compute_responsiveness(offers)
    assert abs(score - 0.6) < 0.001
    assert detail["accepted"] == 3
    assert detail["open"] == 2


# ─────────────────────────────────────────────────────────────
# compute_inbox_awareness
# ─────────────────────────────────────────────────────────────


def test_inbox_awareness_no_messages_perfect() -> None:
    score, detail = compute_inbox_awareness([])
    assert score == 1.0
    assert detail["total_incoming"] == 0


def test_inbox_awareness_all_unread_zero() -> None:
    """routine_baker 의 last_read_round=0, max_msg=19 패턴."""
    m = [ChannelMembership(
        channel_id="ch1", last_read_round=0,
        total_incoming=17, unread_incoming=17,
    )]
    score, detail = compute_inbox_awareness(m)
    assert score == 0.0
    assert detail["unread"] == 17
    assert detail["fully_unread_channels"] == 1


def test_inbox_awareness_all_read_perfect() -> None:
    m = [ChannelMembership(
        channel_id="ch1", last_read_round=19,
        total_incoming=17, unread_incoming=0,
    )]
    score, _ = compute_inbox_awareness(m)
    assert score == 1.0


def test_inbox_awareness_multichannel_mixed() -> None:
    """ch1 fully unread + ch2 fully read = 절반"""
    m = [
        ChannelMembership("ch1", 0, total_incoming=10, unread_incoming=10),
        ChannelMembership("ch2", 19, total_incoming=10, unread_incoming=0),
    ]
    score, detail = compute_inbox_awareness(m)
    assert score == 0.5
    assert detail["total_incoming"] == 20
    assert detail["unread"] == 10
    assert detail["fully_unread_channels"] == 1


def test_inbox_awareness_null_last_read_treated_as_minus_one() -> None:
    """last_read_round=None → 모든 메시지 unread (R0 도 포함)."""
    m = [ChannelMembership("ch1", None, total_incoming=5, unread_incoming=5)]
    score, _ = compute_inbox_awareness(m)
    assert score == 0.0


# ─────────────────────────────────────────────────────────────
# compute_market_awareness
# ─────────────────────────────────────────────────────────────


def test_market_awareness_no_offers_perfect() -> None:
    score, detail = compute_market_awareness([], [])
    assert score == 1.0
    assert detail["n_offers_evaluated"] == 0


def test_market_awareness_no_market_data_default() -> None:
    """offer 있지만 자원의 market trades 0 → 평가 불가, 1.0 default."""
    offers = [OfferRecord(5, "buy", "flour", 6, 60, "sent", "A", "B")]
    score, detail = compute_market_awareness(offers, [])
    assert score == 1.0
    assert detail["n_offers_evaluated"] == 0


def test_market_awareness_perfect_alignment() -> None:
    """offer unit=10, market avg=10 → deviation 0, score 1.0."""
    offers = [OfferRecord(5, "buy", "flour", 6, 60, "sent", "A", "B")]
    trades = [
        TradeRecord(2, "flour", 3, 30, "X", "Y"),  # unit 10
        TradeRecord(3, "flour", 3, 30, "X", "Y"),  # unit 10
    ]
    score, detail = compute_market_awareness(offers, trades)
    assert score == 1.0
    assert detail["n_offers_evaluated"] == 1


def test_market_awareness_50pct_deviation() -> None:
    """market avg 10, offer unit 15 → 50% deviation → score 0.5."""
    offers = [OfferRecord(5, "buy", "flour", 6, 90, "sent", "A", "B")]
    trades = [TradeRecord(3, "flour", 3, 30, "X", "Y")]
    score, detail = compute_market_awareness(offers, trades)
    assert abs(score - 0.5) < 0.01
    assert detail["avg_deviation_pct"] == 50.0


def test_market_awareness_extreme_deviation_clamped() -> None:
    """offer 200% off → deviation clamped to 1.0 → score 0.0."""
    offers = [OfferRecord(5, "buy", "flour", 1, 100, "sent", "A", "B")]
    trades = [TradeRecord(3, "flour", 1, 10, "X", "Y")]
    score, _ = compute_market_awareness(offers, trades)
    assert score == 0.0


def test_market_awareness_excludes_offer_in_same_round_as_trade() -> None:
    """offer 와 trade 가 같은 round 면 lookback 에서 제외 (trade 가 offer 이후 가능)."""
    offers = [OfferRecord(5, "buy", "flour", 1, 10, "sent", "A", "B")]
    trades = [TradeRecord(5, "flour", 1, 30, "X", "Y")]  # 같은 round
    score, detail = compute_market_awareness(offers, trades)
    # market data 없음 → default 1.0
    assert score == 1.0
    assert detail["n_offers_evaluated"] == 0


def test_market_awareness_lookback_respected() -> None:
    """offer R10, lookback=3 → R7-R9 trades 만 봄. R5 trade 무시."""
    offers = [OfferRecord(10, "buy", "flour", 1, 10, "sent", "A", "B")]
    trades = [
        TradeRecord(5, "flour", 1, 30, "X", "Y"),  # 너무 옛날 — 무시
        TradeRecord(8, "flour", 1, 10, "X", "Y"),  # lookback 안 — 사용
    ]
    score, detail = compute_market_awareness(offers, trades, lookback_rounds=3)
    # market avg from R8 only = 10. offer unit 10 → score 1.0
    assert score == 1.0
    assert detail["n_offers_evaluated"] == 1


def test_market_awareness_different_resource_isolated() -> None:
    """다른 자원의 trades 는 무시."""
    offers = [OfferRecord(5, "buy", "flour", 1, 10, "sent", "A", "B")]
    trades = [TradeRecord(3, "wheat", 1, 100, "X", "Y")]  # wheat — 무관
    score, detail = compute_market_awareness(offers, trades)
    assert score == 1.0
    assert detail["n_offers_evaluated"] == 0


def test_market_awareness_multiple_offers_averaged() -> None:
    """여러 offer 의 deviation 평균. 하나 perfect + 하나 50% off → 0.75."""
    offers = [
        OfferRecord(5, "buy", "flour", 1, 10, "sent", "A", "B"),  # market=10, offer=10, dev=0
        OfferRecord(6, "buy", "flour", 1, 15, "sent", "A", "B"),  # market=10, offer=15, dev=0.5
    ]
    trades = [
        TradeRecord(2, "flour", 1, 10, "X", "Y"),
        TradeRecord(3, "flour", 1, 10, "X", "Y"),
    ]
    score, detail = compute_market_awareness(offers, trades)
    assert abs(score - 0.75) < 0.01
    assert detail["n_offers_evaluated"] == 2
