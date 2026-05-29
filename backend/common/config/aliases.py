def normalize_strategy_name(value: str) -> str:
    """Map short aliases to ``StrategyBase.name`` ids (same as ``main.py`` boot logic)."""

    aliases: dict[str, str] = {
        "pairs": "pairs_trading_usdt_usdc",
        "pairs_trading": "pairs_trading_usdt_usdc",
        "sma": "sma_crossover",
        "blend": "blended_signals",
        "blended": "blended_signals",
        "blended_signals": "blended_signals",
        "mm": "market_making_v2",
        "market_making": "market_making_v2",
        "mm2": "market_making_v2",
        "market_making_v2": "market_making_v2",
        "flow": "flow_momentum",
        "flow_momentum": "flow_momentum",
        "momentum": "flow_momentum",
        "all": "all",
        "multi": "all",
    }
    k = (value or "").strip().lower()
    return aliases.get(k, k)
