# Signal Pipeline Audit - Solbot V3.1

## Execution Flow Trace
1. **Pump.fun Event** (`PumpFunMonitor` in `solbot/pumpfun.py`):
   - Background thread running `websocket-client`.
   - Subscribes to `subscribeNewToken` and `subscribeTrade`.
   - Bridges to `asyncio.Queue` via `asyncio.run_coroutine_threadsafe`.
   
2. **Event Bus** (`Solbot._process_events` in `solbot/bot.py`):
   - Consumes from `self._monitor.queue`.
   - Routes to `_handle_trade_event` or `_parse_token_event`.

3. **Filters** (`TokenFilter` in `solbot/filters.py`):
   - Checks `is_qualified(token)`.
   - Evaluates bonding curve progress, liquidity, and creator status.

4. **AI Score** (`AIFilter` in `solbot/ai_filter.py`):
   - If `_ai_enabled`, calls `score_token(token_data)`.
   - Skips if `score < _ai_min_score`.

5. **Risk Engine** (Integrated in `Solbot` & `TokenFilter`):
   - Dynamic fee calculation via `get_dynamic_fee`.
   - Blacklist checks for creators and traders.

6. **Buy Decision** (`Solbot._execute_snipe`):
   - Triggered if qualified and (autobuy ON or KOL match).
   - Priority fee applied.

7. **Jupiter Submit** (`JupiterClient` in `solbot/jupiter.py`):
   - Executes swap via Jupiter Aggregator API or PumpFun direct swap.

8. **Confirmation** (`TradeResult` model):
   - Success/Failure logged in `self._trades`.
   - Message sent to Telegram.

9. **Position Manager** (`Solbot._position_manager`):
   - Tracks `gain`, `drawdown`.
   - Handles `strat.tp_targets` and `stop_loss_pct`.
   - Implements `trailing_stop_pct`.

## Latency Measurement Points
- **Network Latency**: Tracked in `NetworkManager` per proxy.
- **Processing Latency**: To be implemented in `RuntimeMetrics`.
- **Execution Latency**: Tracked in `trade_events` table in SQLite.

## Event Counters
- `total_signals`: Incremented in `_process_events`.
- `filtered_signals`: Incremented in `TokenFilter`.
- `ai_rejected`: Incremented in `_process_events` after `score_token`.
- `total_buys`: Incremented in `_execute_snipe`.
- `total_sells`: Incremented in `_exit_position`.
