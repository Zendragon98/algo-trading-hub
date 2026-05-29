/** Circuit-breaker preset patches (code → enabled). */

export const LIVE_DISABLE_CONFIRM_TOKEN = "DISABLE LIVE BREAKERS";

export const BREAKER_PRESET_FULL: Record<string, boolean> = {
  stale_tick: true,
  wide_spread: true,
  stale_market_data: true,
  stale_user_data: true,
  md_crossed_book: true,
  repeat_reject: true,
  slippage_breach: true,
  exec_quality: true,
  max_drawdown: true,
  hwm_drawdown: true,
  daily_loss: true,
  consecutive_losses: true,
  reconcile_mismatch: true,
  order_reconcile_mismatch: true,
  group_unwind_failed: true,
  price_jump: true,
  toxic_flow: true,
  book_depleted: true,
  operator_halt: true,
};

/** Disable symbol-level entry guards; keep portfolio kills and reconcile. */
export const BREAKER_PRESET_NON_STOP_MM: Record<string, boolean> = {
  ...BREAKER_PRESET_FULL,
  stale_tick: false,
  wide_spread: false,
  repeat_reject: false,
  price_jump: false,
  toxic_flow: false,
  book_depleted: false,
  md_crossed_book: false,
};

/** Keep quoting through brief WS / order-book gaps. */
export const BREAKER_PRESET_CONNECTIVITY: Record<string, boolean> = {
  ...BREAKER_PRESET_FULL,
  stale_market_data: false,
  stale_user_data: false,
  order_reconcile_mismatch: false,
};

/** Disable all major kill switches; operator halt stays manual-only. */
export const BREAKER_PRESET_DISABLE_MAJORS: Record<string, boolean> = {
  ...BREAKER_PRESET_FULL,
  exec_quality: false,
  max_drawdown: false,
  hwm_drawdown: false,
  daily_loss: false,
  consecutive_losses: false,
  reconcile_mismatch: false,
  group_unwind_failed: false,
};
