# Solbot 🤖

A high-performance Solana trading bot built for speed, reliability, and intelligence.

## 🏛 Architecture
- **Dual-Node RPC Strategy**: Intelligent load balancing and fallback between multiple RPC providers.
- **Websocket Real-time Engine**: Sub-millisecond market data ingestion and transaction monitoring.
- **Proxy Fleet**: Distributed proxy management to avoid rate limits and enhance anonymity.
- **Metrics Pipeline**: Integrated monitoring and performance tracking for every trade.

## ✨ Features
- **AI Llama 3.1 405B Scoring**: Advanced token analysis and social sentiment scoring using state-of-the-art LLMs.
- **Jupiter & Pump.fun Sniping**: Lightning-fast execution on Solana's most popular DEXs and launchpads.
- **Secret Sanitization**: Built-in protection to ensure sensitive state and credentials are never leaked.
- **Asynchronous Architecture**: Fully non-blocking I/O for maximum throughput.

## 🚀 Getting Started

### Prerequisites
- Python 3.10+
- Solana RPC Endpoints
- Environment variables configured (see `.env.example`)

### Installation
1. Clone the repository:
   ```bash
   git clone https://github.com/nftboy07/solbot.git
   cd solbot
   ```
2. Set up a virtual environment:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows use `venv\Scripts\activate`
   ```
3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
4. Configure your environment:
   ```bash
   cp .env.example .env
   # Edit .env with your credentials
   ```

## Meme-token sniper

This is the same `python main.py` process that already runs on the Windows VPS. It is not a second bot. Phantom MCP is not used.

### What it watches, and how fast

| Source | Cadence | Role |
| --- | --- | --- |
| PumpPortal WebSocket `subscribeNewToken` (`PUMPFUN_WS_URL`, default `wss://pumpportal.fun/api/data`) | push / real-time | Primary new-mint stream. `solbot/pumpfun.py` + `Solbot._process_events`. |
| REST new-launch scanner | `SNIPER_SCAN_INTERVAL_SECONDS` (default **1.0s**) | Fallback so a dropped WS frame still appears within ~1s. Polls the live pump.fun host `GET https://frontend-api-v3.pump.fun/coins?sort=created_timestamp&order=DESC` (verified 200, JSON list, no auth on this host). |
| Raydium / pump AMM grads | same 1s poller when `SNIPER_SOURCES` includes `raydium` | Same coins API with `complete=true`. Buys still go through PumpPortal `pool=auto` (supports `pump`, `raydium`, `pump-amm`, …). |
| Pump.fun movers | `SNIPER_MOVERS_INTERVAL_SECONDS` (default 30s) | Trending coins only; now routed through the same filter chain, not a raw buy. |

Meteora is **not** a new-launch watcher in this repo (it is only an arbitrage quote venue). Do not expect a Meteora sniper here.

### Filters (existing checks, not a fake oracle)

A name only buys after `Solbot._schedule_token_evaluation` → `TokenFilter.is_qualified` and the existing safety screens:

- no/low LP (`min_liquidity_sol`, plus scanner skip when virtual+real reserves are 0)
- established-mint blocklist, age/mcap/creator-hold, optional mint/freeze authority, top-holder and bundle checks (`solbot/filters.py`, profile-gated)
- Mayhem / unsellable Token-2022 (`solbot/mayhem.py`, from WS payload or `frontend-api-v3.pump.fun/coins/{mint}`)
- optional AI honeypot/premine screen (`solbot/safety_decision.py`) — fail-closed on hard flags; degraded APIs are not treated as a green safety oracle

`FILTER_PROFILE` (default `alpha`) still chooses how strict those checks are. Bankroll knobs overlay clip size / max open / fee reserve on top of the profile.

### How a buy is actually placed

Verified in-repo (`solbot/pumpfun_client.py` → `execute_trade`) and against PumpPortal's local trading API:

1. `POST https://pumpportal.fun/api/trade-local` with `publicKey`, `action=buy`, `mint`, `amount` (SOL clip), `denominatedInSol=true`, `slippage` as **percent** (config is bps / 100), `priorityFee`, `pool=auto`.
2. Response body is a serialized unsigned `VersionedTransaction`.
3. Signed locally with the keypair from `WALLET_PRIVATE_KEY` or `WALLET_PRIVATE_KEY_FILE` (same file-or-env helper as `OPENAI_API_KEY`).
4. Optional pre-flight `simulateTransaction`, then Jito bundle (tip + trade) or fallback `sendTransaction` on `SOLANA_RPC_URL` / `SOLANA_RPC_POOL`.
5. `DRY_RUN=true` never signs or broadcasts; paper ledger only.

### Default bankroll (change via env, not code)

| Knob | Env | Default |
| --- | --- | --- |
| Total bankroll cap | `SNIPER_BANKROLL_SOL` | 1.3 SOL |
| Per-clip size | `SNIPER_CLIP_SOL` / `BUY_AMOUNT_SOL` | 0.25 SOL |
| Max open bags | `SNIPER_MAX_OPEN` / `MAX_ACTIVE_POSITIONS` | 3 |
| Fee reserve | `MIN_WALLET_SOL_RESERVE` | 0.1 SOL |
| Scan interval | `SNIPER_SCAN_INTERVAL_SECONDS` | 1.0 |

A fourth clip is refused. `3 × 0.25 + 0.1 = 0.85` fits inside 1.3 SOL.

### Start / stop (same entrypoint)

**Windows VPS** (existing path: `python main.py`):

```bat
copy .env.example .env
REM set WALLET_PRIVATE_KEY or WALLET_PRIVATE_KEY_FILE, RPC, Telegram, etc.
start.bat
```

Keep-alive (systemd-style restart loop):

```powershell
powershell -ExecutionPolicy Bypass -File ops\windows\watchdog.ps1
```

Stop:

```bat
stop.bat
```

**Linux** (unchanged): `python main.py`, or `ops/solbot` systemd unit via `tools/vps_bootstrap.sh` (`systemctl restart solbot.service` / `systemctl stop solbot.service`).

Ctrl+C / SIGTERM on `main.py` still runs the existing graceful shutdown.

Never commit `.env`, key files, or seeds. `.gitignore` already excludes them.

## 📜 License
This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
