"""Async Telegram alert client for Solbot notifications.

Uses aiohttp to send messages to Telegram Bot API without blocking
the main event loop. Supports alerts for:
- New qualified tokens
- Buy executions (success/failure)
- Sell executions (stop loss, take profit, trailing stop)
- Blacklist events
- Emergency kill switch activation
"""

import asyncio
from typing import Optional

import aiohttp

from solbot.config import TelegramConfig
from solbot.logger import get_logger
from solbot.models import PositionSnapshot, TokenEvent
from solbot.scoring import Confidence, TokenScore

logger = get_logger("telegram")


class TelegramAlert:
    """Async Telegram bot client for sending trade alerts.

    Sends formatted alerts for:
    - Tokens passing filters (with scores)
    - Buy executions
    - Sell executions (with P&L and reason)
    - Blacklist additions
    - Kill switch activations
    """

    def __init__(self, config: TelegramConfig):
        self._config = config
        self._session: Optional[aiohttp.ClientSession] = None
        self._enabled = config.enabled
        self._base_url = f"https://api.telegram.org/bot{config.bot_token}"
        self._rate_limiter = asyncio.Semaphore(config.max_messages_per_second)

    async def start(self):
        """Initialize the aiohttp session."""
        if not self._enabled:
            logger.info("Telegram alerts DISABLED")
            return

        if not self._config.bot_token or not self._config.chat_id:
            logger.warning("Telegram bot_token or chat_id missing - alerts disabled")
            self._enabled = False
            return

        timeout = aiohttp.ClientTimeout(total=10, connect=5)
        self._session = aiohttp.ClientSession(timeout=timeout)
        logger.info(f"Telegram alerts enabled | chat_id={self._config.chat_id}")

    async def stop(self):
        """Close the aiohttp session."""
        if self._session:
            await self._session.close()
            self._session = None
        logger.info("Telegram client closed")

    # ── Token Detection Alerts ──────────────────────────────────────────

    async def send_token_alert(self, score: TokenScore):
        """Send a formatted alert for a qualified token."""
        if not self._enabled or not self._session:
            return
        if not self._config.alert_on_qualified:
            return

        message = self._format_token_alert(score)
        async with self._rate_limiter:
            await self._send_message(message)

    # ── Buy Alerts ──────────────────────────────────────────────────────

    async def send_buy_alert(
        self,
        score: TokenScore,
        tx_signature: Optional[str],
        success: bool,
        amount_sol: float = 0.0,
        amount_tokens: float = 0.0,
        is_paper: bool = False,
    ):
        """Send alert when a buy is executed (or fails)."""
        if not self._enabled or not self._session:
            return
        if not self._config.alert_on_trade:
            return

        if success:
            message = self._format_buy_success(
                score, tx_signature, amount_sol, amount_tokens, is_paper
            )
        else:
            message = self._format_buy_failure(score)

        async with self._rate_limiter:
            await self._send_message(message)

    # ── Sell Alerts ─────────────────────────────────────────────────────

    async def send_sell_alert(self, snapshot: PositionSnapshot, is_paper: bool = False):
        """Send alert when a position is sold (any reason)."""
        if not self._enabled or not self._session:
            return
        if not self._config.alert_on_sell:
            return

        message = self._format_sell_alert(snapshot, is_paper)
        async with self._rate_limiter:
            await self._send_message(message)

    # ── Blacklist Alerts ────────────────────────────────────────────────

    async def send_blacklist_alert(
        self,
        creator_address: str,
        reason: str,
        related_symbol: str = "",
        related_mint: str = "",
    ):
        """Send alert when a creator is blacklisted."""
        if not self._enabled or not self._session:
            return
        if not self._config.alert_on_blacklist:
            return

        message = self._format_blacklist_alert(
            creator_address, reason, related_symbol, related_mint
        )
        async with self._rate_limiter:
            await self._send_message(message)

    # ── Kill Switch Alert ───────────────────────────────────────────────

    async def send_kill_switch_alert(self, reason: str, positions_closed: int):
        """Send alert when the emergency kill switch is triggered."""
        if not self._enabled or not self._session:
            return

        message = (
            f"🚨🚨🚨 <b>KILL SWITCH ACTIVATED</b> 🚨🚨🚨\n\n"
            f"<b>Reason:</b> {reason}\n"
            f"<b>Positions closed:</b> {positions_closed}\n\n"
            f"All trading has been halted.\n"
            f"Manual intervention required."
        )
        async with self._rate_limiter:
            await self._send_message(message)

    # ── Startup Alert ───────────────────────────────────────────────────

    async def send_startup_message(self, mode: str = "PAPER", positions: int = 0, blacklisted: int = 0):
        """Send a bot startup notification."""
        if not self._enabled or not self._session:
            return

        message = (
            f"🤖 <b>SOLBOT ONLINE</b>\n\n"
            f"<b>Mode:</b> {mode}\n"
            f"<b>Open positions:</b> {positions}\n"
            f"<b>Blacklisted creators:</b> {blacklisted}\n\n"
            f"Monitoring Pump.fun for new tokens\n"
            f"Jupiter execution ready"
        )
        await self._send_message(message)

    # ── Internal: Message Sending ───────────────────────────────────────

    async def _send_message(self, text: str):
        """Send a message via Telegram Bot API."""
        if not self._session:
            return

        payload = {
            "chat_id": self._config.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }

        try:
            async with self._session.post(
                f"{self._base_url}/sendMessage", json=payload
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.error(f"Telegram send failed ({resp.status}): {body[:200]}")
                else:
                    logger.debug("Telegram alert sent")
        except asyncio.TimeoutError:
            logger.warning("Telegram send timed out")
        except Exception as e:
            logger.error(f"Telegram error: {e}")

    # ── Internal: Message Formatting ────────────────────────────────────

    def _format_token_alert(self, score: TokenScore) -> str:
        """Format a token detection alert."""
        token = score.token
        emoji = self._confidence_emoji(score.confidence)

        flags_str = ""
        if score.flags:
            flag_lines = []
            for f in score.flags[:5]:
                if f.startswith("VERY_") or f.startswith("DUST_") or f.startswith("INSTANT_"):
                    flag_lines.append(f"  ⚠️ {f}")
                else:
                    flag_lines.append(f"  ✅ {f}")
            flags_str = "\n".join(flag_lines)

        message = (
            f"{emoji} <b>NEW TOKEN DETECTED</b>\n\n"
            f"<b>Name:</b> {token.name} ({token.symbol})\n"
            f"<b>Mint:</b> <code>{token.mint}</code>\n"
            f"<b>Creator:</b> <code>{token.creator or 'Unknown'}</code>\n\n"
            f"📊 <b>Metrics:</b>\n"
            f"  💧 Liquidity: {token.liquidity_sol:.2f} SOL\n"
            f"  💰 Market Cap: ${token.market_cap_usd:,.0f}\n"
            f"  📈 Buy Pressure: {score.buy_pressure_score:.1f}/100\n"
            f"  🎯 Composite: {score.composite_score:.1f}/100\n\n"
            f"🏷️ <b>Confidence:</b> {score.confidence.value} {emoji}\n"
        )

        if flags_str:
            message += f"\n<b>Flags:</b>\n{flags_str}\n"

        message += (
            f"\n🔗 <a href=\"https://solscan.io/token/{token.mint}\">Solscan</a>"
            f" | <a href=\"https://pump.fun/{token.mint}\">Pump.fun</a>"
        )

        return message

    def _format_buy_success(
        self,
        score: TokenScore,
        tx_signature: Optional[str],
        amount_sol: float,
        amount_tokens: float,
        is_paper: bool,
    ) -> str:
        """Format a successful buy alert."""
        token = score.token
        mode_tag = " [PAPER]" if is_paper else ""
        tx_link = ""
        if tx_signature and not tx_signature.startswith("PAPER_"):
            tx_link = f'\n🔗 <a href="https://solscan.io/tx/{tx_signature}">View TX</a>'

        return (
            f"✅ <b>BUY EXECUTED{mode_tag}</b>\n\n"
            f"<b>Token:</b> {token.symbol} ({token.name})\n"
            f"<b>Mint:</b> <code>{token.mint}</code>\n"
            f"<b>Amount:</b> {amount_sol:.4f} SOL → {amount_tokens:.0f} tokens\n"
            f"<b>Confidence:</b> {score.confidence.value}\n"
            f"<b>Score:</b> {score.composite_score:.1f}/100"
            f"{tx_link}"
        )

    def _format_buy_failure(self, score: TokenScore) -> str:
        """Format a failed buy alert."""
        token = score.token
        return (
            f"❌ <b>BUY FAILED</b>\n\n"
            f"<b>Token:</b> {token.symbol} ({token.name})\n"
            f"<b>Mint:</b> <code>{token.mint}</code>\n"
            f"<b>Confidence:</b> {score.confidence.value}\n"
            f"<b>Score:</b> {score.composite_score:.1f}/100"
        )

    def _format_sell_alert(self, snap: PositionSnapshot, is_paper: bool) -> str:
        """Format a sell execution alert with P&L."""
        mode_tag = " [PAPER]" if is_paper else ""

        # Emoji based on P&L
        if snap.pnl_pct >= 50:
            pnl_emoji = "🚀"
        elif snap.pnl_pct >= 0:
            pnl_emoji = "💚"
        else:
            pnl_emoji = "🔴"

        # Reason emoji
        reason_emojis = {
            "stop_loss": "🛑 Stop Loss",
            "take_profit_1": "🎯 Take Profit 1 (partial)",
            "take_profit_2": "🎯 Take Profit 2 (partial)",
            "take_profit_3": "🎯 Take Profit 3 (final)",
            "trailing_stop": "📉 Trailing Stop",
            "emergency": "🚨 Emergency",
            "rug_detected": "💀 Rug Detected",
            "manual": "👤 Manual",
        }
        reason_display = reason_emojis.get(snap.sell_reason or "", snap.sell_reason or "Unknown")

        tx_link = ""
        if snap.exit_tx and not snap.exit_tx.startswith("PAPER_"):
            tx_link = f'\n🔗 <a href="https://solscan.io/tx/{snap.exit_tx}">View TX</a>'

        return (
            f"{pnl_emoji} <b>SELL EXECUTED{mode_tag}</b>\n\n"
            f"<b>Token:</b> {snap.symbol} ({snap.name})\n"
            f"<b>Mint:</b> <code>{snap.mint}</code>\n\n"
            f"📊 <b>Trade Result:</b>\n"
            f"  Entry: {snap.entry_price_sol:.4f} SOL\n"
            f"  Exit:  {snap.exit_amount_sol:.4f} SOL\n"
            f"  P&L:   {snap.pnl_pct:+.1f}% ({snap.pnl_sol:+.4f} SOL)\n"
            f"  Peak:  {snap.highest_price_sol:.4f} SOL\n\n"
            f"<b>Reason:</b> {reason_display}\n"
            f"<b>Held:</b> {snap.age_seconds:.0f}s"
            f"{tx_link}"
        )

    def _format_blacklist_alert(
        self,
        creator_address: str,
        reason: str,
        related_symbol: str,
        related_mint: str,
    ) -> str:
        """Format a blacklist addition alert."""
        reason_display = {
            "rug_liquidity_pull": "Liquidity pulled",
            "rug_mint_authority": "Mint authority abuse",
            "rug_rapid_dump": "Rapid price dump",
            "rug_stop_loss_hit": "Stop loss triggered (auto-blacklist)",
            "repeated_rugs": "Multiple rug incidents",
            "suspicious_pattern": "Suspicious trading pattern",
            "manual": "Manual blacklist",
        }.get(reason, reason)

        token_info = ""
        if related_symbol:
            token_info = f"\n<b>Token:</b> {related_symbol}"
        if related_mint:
            token_info += f"\n<b>Mint:</b> <code>{related_mint}</code>"

        return (
            f"🚫 <b>CREATOR BLACKLISTED</b>\n\n"
            f"<b>Creator:</b> <code>{creator_address}</code>\n"
            f"<b>Reason:</b> {reason_display}"
            f"{token_info}\n\n"
            f"All future tokens from this creator will be blocked."
        )

    @staticmethod
    def _confidence_emoji(confidence: Confidence) -> str:
        """Get emoji for confidence level."""
        return {
            Confidence.HIGH: "🟢",
            Confidence.MEDIUM: "🟡",
            Confidence.LOW: "🔴",
        }.get(confidence, "⚪")
