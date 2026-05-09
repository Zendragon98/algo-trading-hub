# Algo Trading Hub

End-to-end trading console: a React frontend that observes and controls the **ALPHA-7** Python trading engine running against the Binance USDT-M Futures Testnet.

## Architecture

```mermaid
%%{init: {"flowchart": {"useMaxWidth": false, "htmlLabels": true, "nodeSpacing": 45, "rankSpacing": 60}, "themeVariables": {"fontSize": "18px"}}}%%
flowchart TB
    BinanceWs["Binance Futures WS<br/>depth + aggTrade + user-data"]
    BinanceRest["Binance Futures REST<br/>orders + account + klines"]
    IbkrTws["IBKR TWS / IB Gateway<br/>(7497 paper · 7496 live)"]

    subgraph gw ["gateways/ (pluggable via factory)"]
        direction LR
        Factory["factory.create_gateway(VENUE)"]
        BinanceGw["binance/<br/>BinanceGateway"]
        IbkrGw["ibkr/<br/>IBKRGateway (skeleton)"]
        Factory -.-> BinanceGw
        Factory -.-> IbkrGw
    end
    BinanceWs --> BinanceGw
    BinanceRest <--> BinanceGw
    IbkrTws <--> IbkrGw

    subgraph eng ["engine/"]
        direction TB
        MarketData["market_data<br/>OrderBook + TradeTape"]
        Features["FeatureStore"]
        Strategy["strategies/<br/>PairsTrading — cross-coin USDT/USDC basis"]
        Risk["risk/<br/>RiskManager (pre-trade)"]
        Router["execution/<br/>ExecutionRouter"]
        Wheel["execution/<br/>AlgoWheel — FRONTLOAD · NORMAL · BACKLOAD"]
        Slicer["execution/<br/>Slicer + VwapExecutor"]
        OrderMgr["orders/<br/>OrderManager (OMS)"]
        Tracker["execution/<br/>ExecutionTracker (arrival, vwap, slippage)"]
        Impact["execution/<br/>ImpactModel (paper-only)"]
        Position["position/<br/>PositionTracker"]
        Portfolio["portfolio/<br/>Portfolio + PnLTracker"]
        RiskMon["risk/<br/>StopLoss / TakeProfit monitor"]

        MarketData --> Features --> Strategy --> Risk --> Router
        Router --> Wheel --> Slicer --> OrderMgr
        Router --> Tracker
        Impact --> Position --> Portfolio --> RiskMon
        Impact --> Tracker
        RiskMon -->|exit ParentOrder| Router
    end

    BinanceGw --> MarketData
    IbkrGw -.-> MarketData
    OrderMgr --> BinanceGw
    OrderMgr -.-> IbkrGw
    BinanceGw -->|fills + balances| Impact
    IbkrGw -.->|fills + balances| Impact

    Mode["TRADING_MODE<br/>paper · live"] -.->|live disables| Impact

    Bus["common/<br/>EventBus"]
    Portfolio --> Bus
    OrderMgr --> Bus
    Tracker --> Bus
    MarketData --> Bus

    Recorder["persistence/<br/>EventRecorder<br/>data/runs/&lt;id&gt;/*.jsonl"]
    API["api/<br/>FastAPI REST + WebSocket"]
    FE["React console<br/>OMS + Execution Quality panels"]
    Bus --> Recorder
    Bus --> API
    API <--> FE

    Analytics["analytics/<br/>data_loader · pair_analyzer · orderbook_analyzer"]
    Analytics -.->|calibrates thresholds| Strategy
    Analytics -.->|calibrates| Wheel
```

Source kept editable at `backend/docs/architecture.mmd`. Full architecture deep-dive in `backend/README.md`.

## Layout

```
algo-trading-hub/
  src/                     React + TanStack Start frontend (Cloudflare Workers SSR)
    routes/index.tsx       the dashboard
    hooks/useAlgoStream.ts REST + WS client hook bound to the dashboard
    lib/api.ts             typed fetch + WS helpers
  backend/                 Python trading engine + FastAPI surface
    main.py                runs engine + uvicorn in one event loop
    engine/                strategy-agnostic core (orders, exec, risk, ...)
    gateways/              venue adapters (Binance Futures Testnet)
    api/                   FastAPI REST + /ws WebSocket
    analytics/             offline calibration jobs
    docs/architecture.png  the image above
```

## Prerequisites

- Node.js 20+ (or Bun 1.2+) for the frontend
- Python 3.11+ for the backend
- A Binance Futures **Testnet** API key + secret (https://testnet.binancefuture.com)

## Run it locally

Two terminals.

**Backend** (one-shot on Windows):

```powershell
cd backend
copy .env.example .env
# paste your testnet API key + secret into .env
.\run.bat
```

POSIX or manual:

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # then edit
python main.py
# -> serving on http://127.0.0.1:8000
```

**Frontend**:

```bash
bun install        # or: npm install
bun run dev        # or: npm run dev
# -> http://localhost:5173
```

The dashboard hydrates from `GET /api/state` on mount and stays live over `/ws`. Control buttons (Start / Pause / Stop / Flatten) and the risk slider issue REST calls; the engine fans the resulting status changes back over the WebSocket.

## Backend deep-dive

See `backend/README.md` for the full architecture, module-by-module walk-through, env var reference, REST + WS contract, testing notes, and troubleshooting.
