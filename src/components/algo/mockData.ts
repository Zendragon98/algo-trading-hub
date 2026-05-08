export type AlgoStatus = "running" | "paused" | "stopped";

export type Position = {
  symbol: string;
  side: "long" | "short";
  size: number;
  entry: number;
  mark: number;
};

export type Trade = {
  id: string;
  ts: string;
  symbol: string;
  side: "buy" | "sell";
  qty: number;
  price: number;
  pnl: number | null;
};

export type LogEntry = {
  ts: string;
  level: "info" | "warn" | "error" | "signal";
  msg: string;
};

export const SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "ARB/USDT"] as const;

function rand(seed: number) {
  let s = seed;
  return () => {
    s = (s * 9301 + 49297) % 233280;
    return s / 233280;
  };
}

export function makeEquitySeries(n = 96, start = 10000): number[] {
  const r = rand(42);
  const out: number[] = [];
  let v = start;
  for (let i = 0; i < n; i++) {
    v += (r() - 0.46) * 120;
    out.push(Math.round(v * 100) / 100);
  }
  return out;
}

export const initialPositions: Position[] = [
  { symbol: "BTC/USDT", side: "long", size: 0.18, entry: 67_420.5, mark: 68_915.2 },
  { symbol: "ETH/USDT", side: "short", size: 1.4, entry: 3_512.8, mark: 3_488.1 },
  { symbol: "SOL/USDT", side: "long", size: 22, entry: 168.4, mark: 165.7 },
];

export const initialTrades: Trade[] = [
  { id: "T-10472", ts: "14:02:11", symbol: "BTC/USDT", side: "buy", qty: 0.05, price: 68_910.2, pnl: null },
  { id: "T-10471", ts: "13:58:44", symbol: "ETH/USDT", side: "sell", qty: 0.4, price: 3_488.0, pnl: 12.4 },
  { id: "T-10470", ts: "13:51:09", symbol: "SOL/USDT", side: "buy", qty: 8, price: 165.9, pnl: null },
  { id: "T-10469", ts: "13:42:55", symbol: "ARB/USDT", side: "sell", qty: 320, price: 0.812, pnl: -4.2 },
  { id: "T-10468", ts: "13:31:02", symbol: "BTC/USDT", side: "sell", qty: 0.02, price: 68_705.0, pnl: 24.8 },
  { id: "T-10467", ts: "13:20:17", symbol: "ETH/USDT", side: "sell", qty: 1.0, price: 3_510.5, pnl: 8.6 },
  { id: "T-10466", ts: "13:10:01", symbol: "SOL/USDT", side: "buy", qty: 14, price: 169.0, pnl: null },
];

export const initialLogs: LogEntry[] = [
  { ts: "14:02:11", level: "signal", msg: "MOMENTUM_BREAKOUT → BTC/USDT (score 0.82)" },
  { ts: "14:02:11", level: "info", msg: "Order filled: BUY 0.05 BTC/USDT @ 68,910.20" },
  { ts: "13:58:44", level: "info", msg: "Closed partial ETH short, PnL +12.40 USDT" },
  { ts: "13:55:30", level: "warn", msg: "Latency spike on ws-feed (412ms)" },
  { ts: "13:51:09", level: "signal", msg: "MEAN_REVERT → SOL/USDT (z=-2.13)" },
  { ts: "13:42:55", level: "info", msg: "Stop-loss hit ARB/USDT, PnL -4.20 USDT" },
  { ts: "13:31:02", level: "info", msg: "Take-profit BTC/USDT, PnL +24.80 USDT" },
  { ts: "13:10:00", level: "info", msg: "Strategy ALPHA-7 initialized · 4 symbols" },
];