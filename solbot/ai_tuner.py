import asyncio
import logging
import json
import re
import aiohttp
from typing import List, Dict, Optional, Tuple

logger = logging.getLogger("bot.ai_tuner")

class AITuner:
    """
    Analyzes historical trade metrics and invokes Gemini AI model
    to suggest and dynamically apply optimal strategy parameters.
    """
    def __init__(self, bot_instance):
        self._bot = bot_instance
        self.last_run_timestamp = 0

    async def get_closed_trades_summary(self) -> Tuple[List[dict], dict]:
        """Fetches recent closed positions from the database and returns a summary KPI dict."""
        db = getattr(self._bot, '_db', None)
        trades = []
        kpis = {
            "total_trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0.0,
            "total_pnl_sol": 0.0,
            "avg_pnl_sol": 0.0
        }
        
        if not db:
            return trades, kpis

        try:
            rows = await db._execute_read(
                "SELECT token_mint, buy_price, size, pnl, status, timestamp, reason FROM positions WHERE status = 'closed' ORDER BY timestamp DESC LIMIT 50"
            )
            if rows:
                trades = [dict(r) for r in rows]
                kpis["total_trades"] = len(trades)
                
                total_pnl = 0.0
                wins = 0
                for t in trades:
                    pnl = float(t.get("pnl") or 0.0)
                    total_pnl += pnl
                    if pnl > 0:
                        wins += 1

                kpis["wins"] = wins
                kpis["losses"] = len(trades) - wins
                kpis["win_rate"] = (wins / len(trades)) * 100.0 if trades else 0.0
                kpis["total_pnl_sol"] = total_pnl
                kpis["avg_pnl_sol"] = total_pnl / len(trades) if trades else 0.0
        except Exception as e:
            logger.error(f"Error reading positions for AI Tuner: {e}")

        return trades, kpis

    async def generate_suggestions(self) -> Optional[dict]:
        """Queries Gemini AI to generate optimized strategy parameters based on performance."""
        api_key = self._bot._config.ai.gemini_api_key
        if not api_key:
            logger.warning("No Gemini API key found for AI Tuner.")
            return None

        trades, kpis = await self.get_closed_trades_summary()
        if not trades:
            logger.info("No closed trades to analyze for autotuning.")
            return None

        # Format historical trades context for Gemini
        trades_str = ""
        for i, t in enumerate(trades[:15], 1):
            trades_str += f"Trade {i}: Mint={t['token_mint'][:8]}... | Size={t['size']} SOL | PnL={t['pnl']} SOL | Reason={t.get('reason') or 'None'}\n"

        current_config = {
            "buy_amount_sol": self._bot._config.jupiter.buy_amount_sol,
            "trailing_stop_pct": self._bot._config.strategy.trailing_stop_pct,
            "slippage_bps": self._bot._config.jupiter.slippage_bps,
            "ai_min_score": self._bot._ai_min_score,
            "kol_threshold": getattr(self._bot, "_kol_threshold", 2)
        }

        prompt = (
            f"You are the Solbot AGI Autotuner. Analyze recent trade performance and suggest parameter updates.\n\n"
            f"--- CURRENT CONFIGURATION ---\n"
            f"Buy Size: {current_config['buy_amount_sol']} SOL\n"
            f"Trailing Stop: {current_config['trailing_stop_pct'] * 100.0}%\n"
            f"Slippage: {current_config['slippage_bps']} BPS\n"
            f"Min AI Safety Score: {current_config['ai_min_score']}\n"
            f"KOL Sentiment Threshold: {current_config['kol_threshold']}\n\n"
            f"--- RECENT CLOSED TRADES KPI ---\n"
            f"Total Trades: {kpis['total_trades']}\n"
            f"Win Rate: {kpis['win_rate']:.1f}%\n"
            f"Total Realized PnL: {kpis['total_pnl_sol']:.3f} SOL\n"
            f"Avg PnL Per Trade: {kpis['avg_pnl_sol']:.3f} SOL\n\n"
            f"--- RECENT LOGS ---\n"
            f"{trades_str}\n"
            f"Task: Recommend new parameters clamped to these safety boundaries:\n"
            f"  - buy_amount_sol: [0.005 to 1.5]\n"
            f"  - trailing_stop_pct: [0.05 to 0.35]\n"
            f"  - slippage_bps: [100 to 1500]\n"
            f"  - ai_min_score: [60 to 95]\n"
            f"  - kol_threshold: [1 to 5]\n\n"
            f"Respond ONLY with a valid JSON object matching the schema below. Do not wrap in formatting text other than standard markdown code blocks. Do not explain your answer.\n"
            f"{{\n"
            f"  \"buy_amount_sol\": float,\n"
            f"  \"trailing_stop_pct\": float,\n"
            f"  \"slippage_bps\": int,\n"
            f"  \"ai_min_score\": int,\n"
            f"  \"kol_threshold\": int,\n"
            f"  \"reason\": \"string explanation of changes\"\n"
            f"}}"
        )

        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
        payload = {
            "contents": [{
                "parts": [{"text": prompt}]
            }]
        }
        headers = {"Content-Type": "application/json"}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
                        # Extract JSON block
                        json_match = re.search(r"\{.*\}", text, re.DOTALL)
                        if json_match:
                            return json.loads(json_match.group())
                    else:
                        logger.error(f"Gemini API returned error for autotuner: {resp.status}")
        except Exception as e:
            logger.error(f"Failed to call Gemini API for autotuner suggestions: {e}")
        return None

    async def autotune(self) -> Tuple[bool, str]:
        """Generates, clamps, and dynamically applies suggestions, then persists the state."""
        try:
            suggestions = await self.generate_suggestions()
            if not suggestions:
                return False, "Failed to generate suggestions. No trades or API error."

            # Clamping parameters to safety boundaries
            new_buy = max(0.005, min(1.5, float(suggestions.get("buy_amount_sol", 0.05))))
            new_stop = max(0.05, min(0.35, float(suggestions.get("trailing_stop_pct", 0.20))))
            new_slippage = max(100, min(1500, int(suggestions.get("slippage_bps", 300))))
            new_ai_min = max(60, min(95, int(suggestions.get("ai_min_score", 75))))
            new_kol_threshold = max(1, min(5, int(suggestions.get("kol_threshold", 2))))
            reason = suggestions.get("reason", "Autotuning triggered.")

            # Apply using object.__setattr__ to bypass dataclass freeze
            object.__setattr__(self._bot._config.jupiter, "buy_amount_sol", new_buy)
            object.__setattr__(self._bot._config.strategy, "trailing_stop_pct", new_stop)
            object.__setattr__(self._bot._config.jupiter, "slippage_bps", new_slippage)
            self._bot._ai_min_score = new_ai_min
            self._bot._kol_threshold = new_kol_threshold

            if hasattr(self._bot, "_save_state"):
                self._bot._save_state()

            report = (
                f"🧠 <b>AI AUTOTUNER OPTIMIZATION COMPLETE</b>\n\n"
                f"📝 <b>Decision Rationale:</b>\n"
                f"<i>\"{reason}\"</i>\n\n"
                f"⚙️ <b>UPDATED PARAMETERS:</b>\n"
                f"• Buy Size: <code>{new_buy:.3f} SOL</code>\n"
                f"• Trailing Stop-Loss: <code>{new_stop * 100.0:.1f}%</code>\n"
                f"• Jupiter Slippage: <code>{new_slippage} BPS</code> (<code>{new_slippage / 100:.1f}%</code>)\n"
                f"• AI Safety Min: <code>{new_ai_min} score</code>\n"
                f"• KOL Coordinated Threshold: <code>{new_kol_threshold} mentions</code>"
            )
            return True, report

        except Exception as e:
            logger.error(f"Error in autotuning execution: {e}")
            return False, f"Autotuner execution failed: {e}"
