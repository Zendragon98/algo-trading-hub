"""Per-symbol MM quote parameters (spreads and bps overrides)."""



from __future__ import annotations

import json
from dataclasses import dataclass

from common.config import Settings

from ...market_data.feature_store import Features
from ...market_data.symbol_calibration import SymbolCalibration, load_symbol_calibration
from .calibrated import calibration_path, mm2_fee_edge_floor_bps

# Fallback min venue spread (bps) when calibration has no min_spread_bps (MM2_SPREAD_GATE_MODE=calibrated).
_SYMBOL_CLASS_MIN_SPREAD_BPS: dict[str, float] = {
    "BTCUSDT": 0.5,
    "ETHUSDT": 0.5,
    "SOLUSDT": 1.0,
    "BNBUSDT": 1.0,
    "XRPUSDT": 1.0,
    "ARBUSDT": 2.0,
    "AVAXUSDT": 2.0,
    "OPUSDT": 2.0,
}

_MIDCAP_MIN_SPREAD_BPS = 2.0
_ALTCOIN_MIN_SPREAD_BPS = 3.0

_OVERRIDE_ALIASES: dict[str, str] = {

    "half_spread_bps": "half_spread_bps",

    "quote_half_spread_bps": "half_spread_bps",

    "spread_bps": "half_spread_bps",

    "reservation_inventory_bps": "reservation_inventory_bps",

    "res_inventory_bps": "reservation_inventory_bps",

    "inventory_bps": "reservation_inventory_bps",

    "inventory_spread_skew_bps": "inventory_spread_skew_bps",

    "spread_skew_bps": "inventory_spread_skew_bps",

    "toxic_widen_bps": "toxic_widen_bps",

    "depletion_widen_bps": "depletion_widen_bps",

    "min_spread_bps": "min_spread_bps",

    "venue_spread_mult": "venue_spread_mult",

    "size_pct": "quote_size_pct",

}





@dataclass(frozen=True, slots=True)

class MmSymbolQuoteParams:

    symbol: str

    half_spread_bps: float

    reservation_inventory_bps: float

    inventory_spread_skew_bps: float

    toxic_widen_bps: float

    depletion_widen_bps: float

    min_spread_bps: float | None

    venue_spread_mult: float

    size_pct: float | None

    venue_half_floor_bps: float = 0.0





def parse_symbol_float_map(value: object) -> dict[str, float]:

    if value is None:

        return {}

    if isinstance(value, dict):

        raw = value

    elif isinstance(value, str):

        text = value.strip()

        if not text:

            return {}

        if text.startswith("{"):

            raw = json.loads(text)

        else:

            raw = {}

            for part in text.split(","):

                part = part.strip()

                if not part or ":" not in part:

                    continue

                sym, val = part.split(":", 1)

                raw[sym.strip().upper()] = float(val.strip())

    else:

        return {}

    return {str(k).strip().upper(): float(v) for k, v in raw.items() if str(k).strip()}





def parse_symbol_override_map(value: object) -> dict[str, dict[str, float]]:

    if value is None:

        return {}

    if isinstance(value, str):

        text = value.strip()

        if not text:

            return {}

        value = json.loads(text)

    if not isinstance(value, dict):

        return {}

    out: dict[str, dict[str, float]] = {}

    for sym_key, fields in value.items():

        sym = str(sym_key).strip().upper()

        if not sym or not isinstance(fields, dict):

            continue

        normalized: dict[str, float] = {}

        for fk, fv in fields.items():

            canon = _OVERRIDE_ALIASES.get(str(fk).strip().lower())

            if canon is not None:

                normalized[canon] = float(fv)

        if normalized:

            out[sym] = normalized

    return out





def _pick(overrides: dict[str, float], cal: SymbolCalibration | None, key: str, default: float) -> float:

    if key in overrides:

        return float(overrides[key])

    if cal is not None:

        val = getattr(cal, key, None)

        if val is not None:

            return float(val)

    return default





def resolve_mm_params(

    symbol: str,

    settings: Settings,

    feat: Features | None = None,

) -> MmSymbolQuoteParams:

    sym = symbol.upper()

    spread_map = parse_symbol_float_map(getattr(settings, "mm_symbol_half_spread_bps", {}))

    full_map = parse_symbol_override_map(getattr(settings, "mm_symbol_quote_overrides", {}))

    ov = dict(full_map.get(sym, {}))

    if sym in spread_map:

        ov.setdefault("half_spread_bps", spread_map[sym])



    path = calibration_path(settings)

    cal = load_symbol_calibration(path).get(sym) if path else None



    half = _pick(ov, cal, "half_spread_bps", float(settings.mm_quote_half_spread_bps))

    venue_floor = 0.0

    sym_mult = _pick(ov, cal, "venue_spread_mult", float(settings.mm_quote_venue_spread_mult))

    if settings.mm_quote_use_venue_spread_floor and feat is not None and feat.spread_bps is not None:

        if sym_mult > 0:

            venue_floor = sym_mult * max(0.0, float(feat.spread_bps) * 0.5)

            half = max(half, venue_floor)



    min_spread = ov.get("min_spread_bps")

    if min_spread is None and cal is not None and cal.min_spread_bps is not None:

        min_spread = cal.min_spread_bps

    size = ov.get("quote_size_pct")

    if size is None and cal is not None and cal.quote_size_pct is not None:

        size = cal.quote_size_pct



    return MmSymbolQuoteParams(

        symbol=sym,

        half_spread_bps=half,

        reservation_inventory_bps=_pick(

            ov, cal, "reservation_inventory_bps", float(settings.mm_reservation_inventory_bps)

        ),

        inventory_spread_skew_bps=_pick(

            ov, cal, "inventory_spread_skew_bps", float(settings.mm_inventory_spread_skew_bps)

        ),

        toxic_widen_bps=_pick(ov, cal, "toxic_widen_bps", float(settings.mm_quote_toxic_widen_bps)),

        depletion_widen_bps=_pick(

            ov, cal, "depletion_widen_bps", float(settings.mm_depletion_widen_bps)

        ),

        min_spread_bps=float(min_spread) if min_spread is not None else None,

        venue_spread_mult=sym_mult,

        size_pct=float(size) if size is not None else None,

        venue_half_floor_bps=venue_floor,

    )


def symbol_class_min_spread_bps(symbol: str) -> float | None:
    """Tiered physical-quote floor when calibration omits min_spread_bps."""
    sym = symbol.upper()
    if sym in _SYMBOL_CLASS_MIN_SPREAD_BPS:
        return _SYMBOL_CLASS_MIN_SPREAD_BPS[sym]
    base = sym.replace("USDT", "").replace("USDC", "")
    majors = frozenset({"BTC", "ETH"})
    large = frozenset({"SOL", "BNB", "XRP"})
    midcaps = frozenset({"ARB", "AVAX", "OP", "LINK", "DOT", "MATIC", "POL", "ADA", "DOGE"})
    if base in majors:
        return 0.5
    if base in large:
        return 1.0
    if base in midcaps:
        return _MIDCAP_MIN_SPREAD_BPS
    return _ALTCOIN_MIN_SPREAD_BPS


def required_min_spread_bps(
    symbol: str,
    settings: Settings,
    feat: Features | None = None,
    *,
    explicit_min_spread_bps: float = 0.0,
    explicit_min_edge_bps: float = 0.0,
    calibrated_only: bool = False,
) -> float:
    """Minimum venue spread (bps) before posting two-sided MM quotes.

    When ``calibrated_only`` is True (MM2_SPREAD_GATE_MODE=calibrated), uses
    per-symbol ``min_spread_bps`` from calibration/overrides/tier defaults —
    not fee round-trip (tight testnet books can be < fee RT; edge is in quote width).

    Otherwise aligns the spread gate with quote width: ``max(fee, 2 × half_spread)``.
    """
    fee_floor = mm2_fee_edge_floor_bps(symbol, settings)
    params = resolve_mm_params(symbol, settings, feat)
    if calibrated_only:
        floor = params.min_spread_bps
        if floor is None and explicit_min_spread_bps > 0:
            floor = explicit_min_spread_bps
        if floor is None:
            floor = symbol_class_min_spread_bps(symbol)
        if floor is not None:
            return max(0.01, float(floor))
    if params.min_spread_bps is not None:
        return max(fee_floor, params.min_spread_bps)
    if explicit_min_spread_bps > 0:
        return max(fee_floor, explicit_min_spread_bps)
    if calibrated_only:
        return fee_floor
    if explicit_min_edge_bps > 0:
        return max(fee_floor, explicit_min_edge_bps)
    quote_width = 2.0 * params.half_spread_bps
    return max(fee_floor, quote_width)


