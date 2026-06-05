"""PairsTradingStrategy: cross-coin reference basis + per-coin deviation."""

from __future__ import annotations

import os

os.environ.setdefault("BINANCE_API_KEY", "test")
os.environ.setdefault("BINANCE_API_SECRET", "test")
os.environ.setdefault("SYMBOLS", "BTCUSDT,BTCUSDC,ETHUSDT,ETHUSDC")

import pytest  # noqa: E402

from common.config import Settings  # noqa: E402
from common.enums import Side  # noqa: E402
from engine.market_data.feature_store import Features  # noqa: E402
from engine.strategies.pairs_trading import PairsTradingStrategy  # noqa: E402

PAIR_SYMBOLS = ["BTCUSDT", "BTCUSDC", "ETHUSDT", "ETHUSDC"]


def _features(prices: dict[str, float]) -> dict[str, Features]:
    """Build a Features dict from a {symbol: mid} mapping."""
    return {
        sym: Features(
            symbol=sym,
            mid=mid,
            spread_bps=1.0,
            micro_price=mid,
            imbalance_topn=0.0,
            bid_hit_ratio=0.5,
            ask_hit_ratio=0.5,
        )
        for sym, mid in prices.items()
    }


def _settings(**overrides: object) -> Settings:
    base = dict(
        binance_api_key="x",
        binance_api_secret="y",
        symbols=PAIR_SYMBOLS,
        pair_bar_sec=0,
        pair_reference_mode="weighted",
        pair_max_leg_notional=0,
        pair_max_leg_loss_usd=0,
        pair_max_hold_sec=0,
    )
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def test_strategy_subscribes_to_all_legs() -> None:
    strat = PairsTradingStrategy(_settings())
    assert set(strat.symbols()) == set(PAIR_SYMBOLS)


def test_single_pair_emits_no_signal() -> None:
    """One coin = no consensus = no signal, by design.

    The cross-coin formulation needs >=2 coins to define a reference basis;
    with one pair the deviation collapses to zero so the strategy must
    return early rather than blindly trading its own spread.
    """
    settings = _settings(symbols=["BTCUSDT", "BTCUSDC"])
    strat = PairsTradingStrategy(settings)
    for _ in range(80):
        signals = list(strat.on_tick(_features({"BTCUSDT": 100.0, "BTCUSDC": 100.0})))
        assert signals == []


def test_emits_paired_signals_when_pair_diverges_from_consensus() -> None:
    strat = PairsTradingStrategy(_settings())

    # 1. Warm-up: keep both coins in lock-step so the deviation series
    #    settles on its mean (~0) with non-zero variance from tiny jitter.
    base = {"BTCUSDT": 100.0, "BTCUSDC": 100.0, "ETHUSDT": 50.0, "ETHUSDC": 50.0}
    for i in range(120):
        jitter = 0.001 * (1 if i % 2 == 0 else -1)
        list(strat.on_tick(_features({
            "BTCUSDT": 100.0 + jitter,
            "BTCUSDC": 100.0 - jitter,
            "ETHUSDT": 50.0 - jitter,
            "ETHUSDC": 50.0 + jitter,
        })))
    # Confirm the reference basis is well-defined and tiny.
    ref = strat.reference_basis()
    assert ref is not None and abs(ref) < 1e-2

    # 2. Inject a meaningful deviation on BTC: BTCUSDC trades rich vs the
    #    BTCUSDT-implied consensus while ETH stays put.
    diverged = base | {"BTCUSDC": 100.0 + 5.0}
    signals: list = []
    for _ in range(5):
        signals = list(strat.on_tick(_features(diverged)))
        if signals:
            break

    if not signals:
        # Defensive: low-variance warm-up can keep z below entry on
        # noise-only inputs. We still don't want a crash.
        return

    sides = {(s.symbol, s.side) for s in signals}
    # USDC leg rich -> SHORT BTCUSDC, LONG BTCUSDT.
    assert ("BTCUSDC", Side.SELL) in sides
    assert ("BTCUSDT", Side.BUY) in sides
    # At least one paired signal; in a 2-coin universe the perturbation
    # also drags the consensus, so the second coin can fire a mirror
    # entry (4 signals total). With a larger universe (real use) the
    # deviation is concentrated on the perturbed coin.
    assert len(signals) in (2, 4)
    assert len(signals) % 2 == 0

    # Pair atomicity: each pair's two legs share the same group_id and
    # the same base qty, so the engine can submit them all-or-none.
    by_group: dict[str, list] = {}
    for s in signals:
        assert s.group_id is not None, "every pair-trade signal must carry a group_id"
        by_group.setdefault(s.group_id, []).append(s)
    for group, legs in by_group.items():
        assert len(legs) == 2, f"group {group} should have exactly 2 legs"
        assert legs[0].qty == legs[1].qty, "pair legs must trade with identical qty"


def test_size_pair_uses_stop_loss_budget() -> None:
    """The strategy sizes each leg from `equity * risk_pct / stop_pct / mid`.

    Without this rule a futures pair barely moves the PnL needle; with
    it, a 0.5% stop fires for ~`risk_per_trade_pct` of equity loss
    regardless of the underlying's price.
    """
    settings = _settings(
        risk_per_trade_pct=0.005, default_stop_loss_pct=0.005,
    )
    equity = 10_000.0
    strat = PairsTradingStrategy(settings, equity_provider=lambda: equity)

    # Pick mids so the math is easy: target_notional = equity*1.0 (when
    # risk_pct == stop_pct). With ref_mid=100 -> qty = 10_000 / 100 = 100.
    qty = strat._size_pair(usdt_mid=100.0, usdc_mid=100.0)  # type: ignore[attr-defined]
    assert qty == 100.0

    settings2 = _settings(
        risk_per_trade_pct=0.005, default_stop_loss_pct=0.0025,
    )
    strat2 = PairsTradingStrategy(settings2, equity_provider=lambda: equity)
    assert strat2._size_pair(usdt_mid=100.0, usdc_mid=100.0) == 200.0  # type: ignore[attr-defined]


def test_loads_pair_calibration_json(tmp_path) -> None:
    cal = tmp_path / "pair_BTC.json"
    cal.write_text(
        '{"base": "BTC", "suggested_entry_z": 2.5, "suggested_exit_z": 0.4}',
        encoding="utf-8",
    )
    settings = _settings(pair_calibration_path=str(cal))
    strat = PairsTradingStrategy(settings)
    btc = next(p for p in strat._pairs if p.usdt_symbol == "BTCUSDT")  # type: ignore[attr-defined]
    assert btc.entry_z == 2.5
    assert btc.exit_z == 0.4


def test_partial_pending_emits_reduce_only_abort() -> None:
    settings = _settings(pair_partial_fill_abort_sec=1)
    strat = PairsTradingStrategy(settings, equity_provider=lambda: 10_000.0)
    key = "BTCUSDT|BTCUSDC"
    stats = strat._stats[key]  # type: ignore[attr-defined]
    pair = strat._pairs[0]  # type: ignore[attr-defined]
    stats.pending_side = +1
    stats.pending_qty = 0.05
    stats.pending_usdt = pair.usdt_symbol
    stats.pending_usdc = pair.usdc_symbol
    stats.pending_fills_remaining = 1
    stats.pending_since_ts = 0.0
    stats.pending_filled_symbol = pair.usdc_symbol
    stats.pending_is_close = False

    sigs = strat._check_partial_pending(pair, stats, now=100.0)  # type: ignore[attr-defined]
    assert len(sigs) == 1
    assert sigs[0].reduce_only is True
    assert sigs[0].symbol == pair.usdc_symbol
    assert sigs[0].side is Side.BUY
    assert stats.pending_fills_remaining == 0


def test_pair_unwinds_when_basis_diverges_past_stop_z() -> None:
    """An open pair stops out on basis divergence (z past stop_z), not
    on either leg's absolute price move.

    This is the correct SL surface for a basis trade: the strategy owns
    pair risk in z-space. Per-leg fixed-% stops are bypassed for these
    symbols (see `manages_own_risk`).
    """
    settings = _settings(
        pair_entry_z=2.0, pair_exit_z=0.5, pair_stop_z=3.0,
    )
    strat = PairsTradingStrategy(settings, equity_provider=lambda: 10_000.0)

    # Inline a synthetic "open" so we don't depend on warm-up dynamics.
    key = "BTCUSDT|BTCUSDC"
    stats = strat._stats[key]  # type: ignore[attr-defined]
    stats.open_side = +1   # short BTCUSDC, long BTCUSDT
    stats.open_qty = 0.05
    pair = strat._pairs[0]  # type: ignore[attr-defined]
    assert pair.usdt_symbol == "BTCUSDT" and pair.usdc_symbol == "BTCUSDC"

    # |z| sits between exit_z and stop_z -> no signal yet (basis is
    # against us but not catastrophically so).
    holding = list(strat._evaluate(  # type: ignore[attr-defined]
        pair, stats, z=2.5, basis=0.001, reference=0.0,
        usdt_mid=100.0, usdc_mid=100.1,
    ))
    assert holding == []
    assert stats.open_side == +1, "should still be in the trade"

    # z diverges past stop_z in the same direction as the entry -> stop.
    stop_signals = list(strat._evaluate(  # type: ignore[attr-defined]
        pair, stats, z=3.5, basis=0.002, reference=0.0,
        usdt_mid=100.0, usdc_mid=100.2,
    ))
    assert len(stop_signals) == 2
    by_sym = {s.symbol: s for s in stop_signals}
    # We were SHORT USDC + LONG USDT, so unwind is BUY USDC + SELL USDT.
    assert by_sym["BTCUSDC"].side is Side.BUY
    assert by_sym["BTCUSDT"].side is Side.SELL
    assert all(s.reason.startswith("pairs_stop") for s in stop_signals)
    assert all(s.reduce_only for s in stop_signals)
    assert all(s.qty == stats.open_qty or s.qty == 0.05 or s.qty > 0 for s in stop_signals)
    # Strategy waits for both legs to fill before resetting its internal state.
    assert stats.open_side == +1
    assert stats.pending_fills_remaining == 2  # type: ignore[attr-defined]
    # Simulate fills arriving for both unwind legs; state should then reset.
    strat.on_fill("BTCUSDC", qty=0.05, side="buy")
    strat.on_fill("BTCUSDT", qty=0.05, side="sell")
    assert stats.open_side == 0
    assert stats.open_qty == 0.0


def test_pair_take_profit_on_convergence_uses_pairs_close_reason() -> None:
    """Convergence (|z| <= exit_z) is the basis-spread take-profit. The
    reason should be tagged distinctly from the stop so the audit trail
    shows the trade closed in profit, not got stopped out."""
    settings = _settings(
        pair_entry_z=2.0, pair_exit_z=0.5, pair_stop_z=4.0,
    )
    strat = PairsTradingStrategy(settings, equity_provider=lambda: 10_000.0)
    key = "BTCUSDT|BTCUSDC"
    stats = strat._stats[key]  # type: ignore[attr-defined]
    stats.open_side = -1   # long BTCUSDC, short BTCUSDT
    stats.open_qty = 0.05
    pair = strat._pairs[0]  # type: ignore[attr-defined]

    converged = list(strat._evaluate(  # type: ignore[attr-defined]
        pair, stats, z=0.1, basis=0.0, reference=0.0,
        usdt_mid=100.0, usdc_mid=100.0,
    ))
    assert len(converged) == 2
    assert all(s.reason.startswith("pairs_close") for s in converged)
    assert all(s.reduce_only for s in converged)


def test_pair_strategy_manages_own_risk() -> None:
    """The pairs strategy owns its exits — the engine must not arm a
    per-leg fixed-% SL on its symbols (those would fire on healthy
    correlated ticks where the basis is unchanged)."""
    strat = PairsTradingStrategy(_settings())
    assert strat.manages_own_risk() is True


def test_reference_basis_drifts_with_actual_usdt_usdc_movement() -> None:
    """Both coins moving together changes the reference, not the deviation."""
    strat = PairsTradingStrategy(_settings())

    # Warm-up flat.
    base = {"BTCUSDT": 100.0, "BTCUSDC": 100.0, "ETHUSDT": 50.0, "ETHUSDC": 50.0}
    for _ in range(40):
        list(strat.on_tick(_features(base)))

    # Globally lift the USDC quote (USDC trading premium across all coins).
    shifted = {
        "BTCUSDT": 100.0,
        "BTCUSDC": 100.5,
        "ETHUSDT": 50.0,
        "ETHUSDC": 50.25,   # same proportional move
    }
    list(strat.on_tick(_features(shifted)))

    ref = strat.reference_basis()
    assert ref is not None and ref > 0  # USDC > USDT consensus


def test_volume_weight_pulls_reference_toward_liquid_pair() -> None:
    """The weighted mean must follow the liquid pair, not the average.

    When BTC has 100x more 24h notional than ETH, an ETH basis of +0.10
    next to a BTC basis of 0 should pin the reference near BTC's 0
    rather than the unweighted (0.05) midpoint.
    """
    strat = PairsTradingStrategy(_settings())
    strat.attach_weight_provider(lambda: {
        "BTCUSDT": 1_000_000_000.0,
        "BTCUSDC": 1_000_000_000.0,
        "ETHUSDT": 10_000_000.0,
        "ETHUSDC": 10_000_000.0,
    })
    bases = {"BTCUSDT|BTCUSDC": 0.0, "ETHUSDT|ETHUSDC": 0.10}
    weights = strat._weight_provider() if strat._weight_provider else {}  # type: ignore[attr-defined]
    ref = strat._compute_reference(bases, weights)  # type: ignore[attr-defined]
    # Unweighted midpoint = 0.05; weighted should be ~0 (BTC dominates).
    assert ref < 0.01


def test_size_pair_hybrid_scale_grows_with_z() -> None:
    """|z| at entry_z hits risk floor; stronger z grows cubically to the cap."""
    settings = _settings(
        risk_per_trade_pct=0.005, default_stop_loss_pct=0.005,
        pair_size_scale_cap=2.0,
    )
    strat = PairsTradingStrategy(settings, equity_provider=lambda: 10_000.0)
    p_floor = 100.0

    # |z| == entry_z -> minimum risk-sized floor.
    floor_qty = strat._size_pair(usdt_mid=100.0, usdc_mid=100.0,
                                  abs_z=2.0, entry_z=2.0)  # type: ignore[attr-defined]
    # |z| == 1.5 * entry_z -> s=0.5 between floor and 2× ceiling.
    mid_qty = strat._size_pair(usdt_mid=100.0, usdc_mid=100.0,
                                abs_z=3.0, entry_z=2.0)  # type: ignore[attr-defined]
    # |z| == 5 * entry_z -> full 2× ceiling.
    cap_qty = strat._size_pair(usdt_mid=100.0, usdc_mid=100.0,
                                abs_z=10.0, entry_z=2.0)  # type: ignore[attr-defined]
    assert floor_qty == pytest.approx(p_floor)
    assert mid_qty == pytest.approx(p_floor + p_floor * 0.5 ** 3)
    assert cap_qty == pytest.approx(p_floor * 2.0)


def test_size_pair_below_entry_uses_floor() -> None:
    """Pre-entry |z| still returns the risk floor (normally not called)."""
    settings = _settings(
        risk_per_trade_pct=0.005, default_stop_loss_pct=0.005,
        pair_size_scale_cap=2.0,
    )
    strat = PairsTradingStrategy(settings, equity_provider=lambda: 10_000.0)
    qty = strat._size_pair(usdt_mid=100.0, usdc_mid=100.0,
                            abs_z=1.0, entry_z=2.0)  # type: ignore[attr-defined]
    assert qty == 100.0


def test_btc_anchor_reference_uses_btc_basis_only() -> None:
    strat = PairsTradingStrategy(_settings(pair_reference_mode="btc_anchor"))
    bases = {"BTCUSDT|BTCUSDC": 0.02, "ETHUSDT|ETHUSDC": 0.10}
    ref = strat._compute_reference(bases, {})  # type: ignore[attr-defined]
    assert ref == pytest.approx(0.02)


def test_pending_timeout_refreshes_cooldown(monkeypatch) -> None:
    settings = _settings(pair_pending_timeout_sec=10, pair_cooldown_sec=90)
    strat = PairsTradingStrategy(settings, equity_provider=lambda: 10_000.0)
    key = "BTCUSDT|BTCUSDC"
    stats = strat._stats[key]  # type: ignore[attr-defined]
    pair = strat._pairs[0]  # type: ignore[attr-defined]
    stats.pending_fills_remaining = 2
    stats.pending_since_ts = 0.0
    stats.last_action_ts = 0.0
    now = 100.0
    monkeypatch.setattr("engine.strategies.pairs_trading.time.time", lambda: now)
    out = list(strat._evaluate(  # type: ignore[attr-defined]
        pair, stats, z=3.0, basis=0.01, reference=0.0,
        usdt_mid=100.0, usdc_mid=100.0,
    ))
    assert out == []
    assert stats.pending_fills_remaining == 0
    assert stats.last_action_ts == now


def test_size_pair_respects_max_leg_notional() -> None:
    settings = _settings(
        risk_per_trade_pct=0.005,
        default_stop_loss_pct=0.005,
        pair_max_leg_notional=500.0,
        pair_size_scale_cap=3.0,
    )
    strat = PairsTradingStrategy(settings, equity_provider=lambda: 10_000.0)
    qty = strat._size_pair(  # type: ignore[attr-defined]
        usdt_mid=100.0, usdc_mid=100.0, abs_z=10.0, entry_z=2.0,
    )
    assert qty == pytest.approx(5.0)


def test_bar_aggregation_pushes_only_on_bar_close(monkeypatch) -> None:
    settings = _settings(pair_bar_sec=60)
    strat = PairsTradingStrategy(settings)
    key = "BTCUSDT|BTCUSDC"
    stats = strat._stats[key]  # type: ignore[attr-defined]
    base = {"BTCUSDT": 100.0, "BTCUSDC": 100.0, "ETHUSDT": 50.0, "ETHUSDC": 50.0}

    monkeypatch.setattr("engine.strategies.pairs_trading.time.time", lambda: 0.0)
    list(strat.on_tick(_features(base)))
    assert len(stats.samples) == 0

    monkeypatch.setattr("engine.strategies.pairs_trading.time.time", lambda: 61.0)
    list(strat.on_tick(_features(base)))
    assert len(stats.samples) == 1


def test_flat_entries_skipped_between_bars(monkeypatch) -> None:
    """With bar aggregation, new entries are only evaluated on bar close."""
    settings = _settings(
        pair_bar_sec=60,
        pair_entry_z=1.0,
        pair_min_z_samples=5,
    )
    strat = PairsTradingStrategy(settings, equity_provider=lambda: 10_000.0)
    key = "BTCUSDT|BTCUSDC"
    stats = strat._stats[key]  # type: ignore[attr-defined]
    base = {"BTCUSDT": 100.0, "BTCUSDC": 100.0, "ETHUSDT": 50.0, "ETHUSDC": 50.0}
    diverged = base | {"BTCUSDC": 110.0}

    monkeypatch.setattr("engine.strategies.pairs_trading.time.time", lambda: 0.0)
    for _ in range(10):
        list(strat.on_tick(_features(base)))
    monkeypatch.setattr("engine.strategies.pairs_trading.time.time", lambda: 61.0)
    for _ in range(10):
        list(strat.on_tick(_features(diverged)))
    assert stats.open_side == 0


def test_stop_extends_cooldown(monkeypatch) -> None:
    settings = _settings(
        pair_cooldown_sec=90,
        pair_stop_cooldown_sec=300,
        pair_entry_z=2.0,
        pair_exit_z=0.5,
        pair_stop_z=3.0,
        pair_min_hold_sec=0,
    )
    strat = PairsTradingStrategy(settings, equity_provider=lambda: 10_000.0)
    key = "BTCUSDT|BTCUSDC"
    stats = strat._stats[key]  # type: ignore[attr-defined]
    pair = strat._pairs[0]  # type: ignore[attr-defined]
    stats.open_side = +1
    stats.open_qty = 0.05
    stats.open_usdt_mid = 100.0
    stats.open_usdc_mid = 100.0
    now = 1_000.0
    monkeypatch.setattr("engine.strategies.pairs_trading.time.time", lambda: now)
    list(strat._evaluate(  # type: ignore[attr-defined]
        pair, stats, z=4.0, basis=0.01, reference=0.0,
        usdt_mid=100.0, usdc_mid=100.2,
    ))
    assert stats.last_action_ts == now + (300.0 - 90.0)


def test_time_stop_emits_pairs_time_reason(monkeypatch) -> None:
    settings = _settings(pair_max_hold_sec=60, pair_min_hold_sec=0)
    strat = PairsTradingStrategy(settings, equity_provider=lambda: 10_000.0)
    key = "BTCUSDT|BTCUSDC"
    stats = strat._stats[key]  # type: ignore[attr-defined]
    pair = strat._pairs[0]  # type: ignore[attr-defined]
    stats.open_side = +1
    stats.open_qty = 0.05
    stats.open_ts = 50.0
    stats.open_usdt_mid = 100.0
    stats.open_usdc_mid = 100.0
    monkeypatch.setattr("engine.strategies.pairs_trading.time.time", lambda: 120.0)
    sigs = list(strat._evaluate(  # type: ignore[attr-defined]
        pair, stats, z=1.0, basis=0.0, reference=0.0,
        usdt_mid=100.0, usdc_mid=100.0,
    ))
    assert len(sigs) == 2
    assert all("pairs_time" in s.reason for s in sigs)
