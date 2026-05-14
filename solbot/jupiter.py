"""Jupiter DEX aggregator client for swap execution.

Uses aiohttp for async HTTP requests to Jupiter's quote and swap APIs.
"""

import asyncio
import time
from typing import Optional

import aiohttp
import base58
from solders.keypair import Keypair
from solders.transaction import VersionedTransaction

from solbot.config import JupiterConfig
from solbot.logger import get_logger
from solbot.models import SwapQuote, TradeResult
from solbot.wallet import SOL_MINT, Wallet

logger = get_logger("jupiter")

# 1 SOL = 1_000_000_000 lamports
LAMPORTS_PER_SOL = 1_000_000_000


class JupiterClient:
    """Async client for Jupiter V6 API - quote, swap, and execute."""

    def __init__(self, config: JupiterConfig, wallet: Wallet):
        self._config = config
        self._wallet = wallet
        self._session: Optional[aiohttp.ClientSession] = None

    async def start(self):
        """Initialize the aiohttp session."""
        timeout = aiohttp.ClientTimeout(total=30, connect=10)
        self._session = aiohttp.ClientSession(
            timeout=timeout,
            headers={"Accept": "application/json"},
        )
        logger.info("Jupiter client initialized")

    async def stop(self):
        """Close the aiohttp session."""
        if self._session:
            await self._session.close()
            self._session = None
        logger.info("Jupiter client closed")

    async def get_quote(
        self,
        output_mint: str,
        amount_sol: Optional[float] = None,
        input_mint: str = SOL_MINT,
    ) -> Optional[SwapQuote]:
        """Fetch a swap quote from Jupiter.

        Args:
            output_mint: Target token mint address.
            amount_sol: Amount of SOL to swap (uses config default if None).
            input_mint: Input token mint (defaults to SOL).

        Returns:
            SwapQuote if successful, None otherwise.
        """
        if not self._session:
            raise RuntimeError("JupiterClient not started - call start() first")

        amount = amount_sol or self._config.buy_amount_sol
        amount_lamports = int(amount * LAMPORTS_PER_SOL)

        params = {
            "inputMint": input_mint,
            "outputMint": output_mint,
            "amount": str(amount_lamports),
            "slippageBps": str(self._config.slippage_bps),
            "onlyDirectRoutes": "false",
            "asLegacyTransaction": "false",
        }

        try:
            async with self._session.get(
                f"{self._config.api_url}/quote", params=params
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.error(f"Quote failed ({resp.status}): {body[:200]}")
                    return None

                data = await resp.json()
                return SwapQuote(
                    input_mint=input_mint,
                    output_mint=output_mint,
                    in_amount=int(data["inAmount"]),
                    out_amount=int(data["outAmount"]),
                    price_impact_pct=float(data.get("priceImpactPct", 0)),
                    slippage_bps=self._config.slippage_bps,
                    route_plan=data.get("routePlan", []),
                )

        except asyncio.TimeoutError:
            logger.error("Quote request timed out")
            return None
        except Exception as e:
            logger.error(f"Quote error: {e}")
            return None

    async def execute_swap(self, output_mint: str) -> TradeResult:
        """Execute a full swap: quote -> build tx -> sign -> send.

        Args:
            output_mint: Target token to buy.

        Returns:
            TradeResult with success status and transaction details.
        """
        start_time = time.time()

        # Step 1: Get quote with retries
        quote = None
        for attempt in range(self._config.max_retries):
            quote = await self.get_quote(output_mint)
            if quote:
                break
            delay = self._config.retry_delay_ms / 1000.0
            logger.warning(f"Quote retry {attempt + 1}/{self._config.max_retries}")
            await asyncio.sleep(delay)

        if not quote:
            return TradeResult(
                success=False,
                token_mint=output_mint,
                error="Failed to get quote after retries",
                latency_ms=(time.time() - start_time) * 1000,
            )

        logger.info(
            f"Quote: {quote.in_amount / LAMPORTS_PER_SOL:.4f} SOL -> "
            f"{quote.out_amount} tokens | impact={quote.price_impact_pct:.4f}%"
        )

        # Step 2: Build swap transaction
        tx_data = await self._build_swap_transaction(quote)
        if not tx_data:
            return TradeResult(
                success=False,
                token_mint=output_mint,
                error="Failed to build swap transaction",
                latency_ms=(time.time() - start_time) * 1000,
            )

        # Step 3: Sign and send
        tx_signature = await self._sign_and_send(tx_data)
        latency = (time.time() - start_time) * 1000

        if tx_signature:
            logger.info(f"SWAP SUCCESS | tx={tx_signature} | {latency:.0f}ms")
            return TradeResult(
                success=True,
                token_mint=output_mint,
                tx_signature=tx_signature,
                amount_in=quote.in_amount / LAMPORTS_PER_SOL,
                amount_out=quote.out_amount,
                latency_ms=latency,
            )
        else:
            return TradeResult(
                success=False,
                token_mint=output_mint,
                error="Transaction send failed",
                latency_ms=latency,
            )

    async def _build_swap_transaction(self, quote: SwapQuote) -> Optional[bytes]:
        """Request Jupiter to build the swap transaction."""
        if not self._session:
            return None

        payload = {
            "quoteResponse": {
                "inputMint": quote.input_mint,
                "outputMint": quote.output_mint,
                "inAmount": str(quote.in_amount),
                "outAmount": str(quote.out_amount),
                "priceImpactPct": str(quote.price_impact_pct),
                "routePlan": quote.route_plan,
                "slippageBps": quote.slippage_bps,
            },
            "userPublicKey": self._wallet.pubkey_str,
            "wrapAndUnwrapSol": True,
            "dynamicComputeUnitLimit": True,
            "prioritizationFeeLamports": "auto",
        }

        try:
            async with self._session.post(
                f"{self._config.api_url}/swap", json=payload
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.error(f"Swap build failed ({resp.status}): {body[:200]}")
                    return None

                data = await resp.json()
                swap_tx = data.get("swapTransaction")
                if not swap_tx:
                    logger.error("No swapTransaction in response")
                    return None

                import base64
                return base64.b64decode(swap_tx)

        except Exception as e:
            logger.error(f"Build swap tx error: {e}")
            return None

    async def _sign_and_send(self, tx_data: bytes) -> Optional[str]:
        """Deserialize, sign, and send the transaction via RPC."""
        try:
            # Deserialize versioned transaction
            tx = VersionedTransaction.from_bytes(tx_data)

            # Sign with wallet keypair
            signed_tx = VersionedTransaction(tx.message, [self._wallet.keypair])
            raw_tx = bytes(signed_tx)

            # Send via RPC (using aiohttp to Solana RPC)
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "sendTransaction",
                "params": [
                    base58.b58encode(raw_tx).decode("utf-8"),
                    {
                        "skipPreflight": True,
                        "preflightCommitment": "processed",
                        "encoding": "base58",
                        "maxRetries": 2,
                    },
                ],
            }

            # Use the configured RPC URL
            rpc_url = "https://api.mainnet-beta.solana.com"
            async with self._session.post(rpc_url, json=payload) as resp:
                result = await resp.json()
                if "error" in result:
                    logger.error(f"RPC error: {result['error']}")
                    return None
                return result.get("result")

        except Exception as e:
            logger.error(f"Sign/send error: {e}")
            return None
