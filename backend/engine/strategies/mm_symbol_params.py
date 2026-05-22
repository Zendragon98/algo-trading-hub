"""Per-symbol MM quote parameters (spreads and bps overrides)."""



from __future__ import annotations



import json

from dataclasses import dataclass



from common.config import Settings



from ..market_data.feature_store import Features

from ..market_data.symbol_calibration import SymbolCalibration, load_symbol_calibration

from .mm_calibrated import calibration_path



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


