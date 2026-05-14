"""Async Telegram alert client for qualified token notifications.

Uses aiohttp to send messages to Telegram Bot API without blocking
the main event loop.
"""

import asyncio
from typing import Optional

import aiohttp

from solbot.config import TelegramConfig
from solbot.logger import get_logger
from solbot.models import TokenEvent
from solbot.scoring import Confidence, TokenScore

logger = get_logger("telegram")


class TelegramAlert:
    """Async Telegram bot client for sending trade alerts.

    Sends formatted alerts for tokens that pass filters, including:
    - Mint address (clickable link)
    - Liquidity (SOL)
    - Market cap (USD)
    - Buy pressure score
    - Confidence classification
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

    async def send_token_alert(self, score: TokenScore):
        """Send a formatted alert for a qualified token.

        Args:
            score: TokenScore containing the evaluated token and scores.
        """
        if not self._enabled or not self._session:
            return

        token = score.token
        message = self._format_alert(token, score)

        async with self._rate_limiter:
            await self._send_message(message)

    async def send_trade_alert(self, score: TokenScore, tx_signature: Optional[str], success: bool):
        """Send alert when a trade is executed (or fails).

        Args:
            score: TokenScore for the traded token.
            tx_signature: Transaction signature (None if failed).
            success: Whether the trade succeeded.
        """
        if not self._enabled or not self._session:
            return

        token = score.token
        if success:
            message = self._format_trade_success(token, score, tx_signature)
        else:
            message = self._format_trade_failure(token, score)

        async with self._rate_limiter:
            await self._send_message(message)

    async def send_startup_message(self):
        """Send a bot startup notification."""
        if not self._enabled or not self._session:
            return

        message = (
            "🤖 <b>SOLBOT ONLINE</b>\n\n"
            "Monitoring Pump.fun for new tokens\n"
            "Jupiter execution ready"
        )
        await self._send_message(message)

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

    def _format_alert(self, token: TokenEvent, score: TokenScore) -> str:
        """Format a token alert message."""
        confidence_emoji = {
            Confidence.HIGH: "🟢",
            Confidence.MEDIUM: "🟡",
            Confidence.LOW: "🔴",
        }
        emoji = confidence_emoji.get(score.confidence, "⚪")

        flags_str = ""
        if score.flags:
            flag_emojis = []
            for f in score.flags[:5]:  # Limit displayed flags
                if f.startswith("VERY_") or f.startswith("DUST_") or f.startswith("INSTANT_"):
                    flag_emojis.append(f"⚠️ {f}")
                else:
                    flag_emojis.append(f"✅ {f}")
            flags_str = "\n".join(flag_emojis)

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

        # Solscan link
        message += (
            f"\n🔗 <a href=\"https://solscan.io/token/{token.mint}\">Solscan</a>"
            f" | <a href=\"https://pump.fun/{token.mint}\">Pump.fun</a>"
        )

        return message

    def _format_trade_success(
        self, token: TokenEvent, score: TokenScore, tx_signature: Optional[str]
    ) -> str:
        """Format a successful trade alert."""
        tx_link = ""
        if tx_signature:
            tx_link = f'\n🔗 <a href="https://solscan.io/tx/{tx_signature}">View TX</a>'

        return (
            f"✅ <b>BUY EXECUTED</b>\n\n"
            f"<b>Token:</b> {token.symbol} ({token.name})\n"
            f"<b>Mint:</b> <code>{token.mint}</code>\n"
            f"<b>Confidence:</b> {score.confidence.value}\n"
            f"<b>Score:</b> {score.composite_score:.1f}/100"
            f"{tx_link}"
        )

    def _format_trade_failure(self, token: TokenEvent, score: TokenScore) -> str:
        """Format a failed trade alert."""
        return (
            f"❌ <b>BUY FAILED</b>\n\n"
            f"<b>Token:</b> {token.symbol} ({token.name})\n"
            f"<b>Mint:</b> <code>{token.mint}</code>\n"
            f"<b>Confidence:</b> {score.confidence.value}\n"
            f"<b>Score:</b> {score.composite_score:.1f}/100"
        )
