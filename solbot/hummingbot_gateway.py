"""Hummingbot Gateway client for Solana DEX routing and order execution."""

import asyncio
import logging
import ssl
from typing import Any, Dict, List, Optional

import aiohttp

from solbot.config import HummingbotConfig

logger = logging.getLogger("bot.hummingbot_gateway")


class HummingbotGatewayClient:
    """Async client communicating with Hummingbot Gateway REST API."""

    def __init__(self, config: HummingbotConfig):
        self._config = config
        self._base_url = config.gateway_url.rstrip("/")
        self._network = config.network
        self._session: Optional[aiohttp.ClientSession] = None
        self._ssl_context: Optional[ssl.SSLContext] = None
        self._init_ssl()

    def _init_ssl(self):
        """Configure mTLS SSL context if client certificates are provided."""
        if self._config.cert_path and self._config.key_path:
            try:
                ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                ctx.load_cert_chain(
                    certfile=self._config.cert_path,
                    keyfile=self._config.key_path,
                    password=self._config.passphrase if self._config.passphrase else None,
                )
                self._ssl_context = ctx
                logger.info("Hummingbot Gateway mTLS SSL context initialized.")
            except Exception as e:
                logger.error("Failed to initialize Hummingbot Gateway SSL context: %s", e)
                self._ssl_context = None

    async def get_session(self) -> aiohttp.ClientSession:
        """Get or initialize the aiohttp ClientSession."""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=self._config.timeout_seconds)
            connector = aiohttp.TCPConnector(ssl=self._ssl_context) if self._ssl_context else None
            self._session = aiohttp.ClientSession(timeout=timeout, connector=connector)
        return self._session

    async def close(self):
        """Close the underlying aiohttp session."""
        if self._session and not self._session.closed:
            await self._session.close()

    async def _request(self, method: str, endpoint: str, **kwargs) -> Optional[Dict[str, Any]]:
        """Internal helper for dispatching HTTP requests to Hummingbot Gateway."""
        session = await self.get_session()
        url = f"{self._base_url}/{endpoint.lstrip('/')}"
        try:
            async with session.request(method, url, **kwargs) as resp:
                if resp.status in (200, 201):
                    return await resp.json()
                error_body = await resp.text()
                logger.warning(
                    "Hummingbot Gateway %s %s returned HTTP %s: %s",
                    method,
                    endpoint,
                    resp.status,
                    error_body[:200],
                )
                return None
        except asyncio.TimeoutError:
            logger.warning("Hummingbot Gateway request timed out on %s", endpoint)
            return None
        except Exception as e:
            logger.error("Hummingbot Gateway communication error on %s: %s", endpoint, e)
            return None

    async def is_healthy(self) -> bool:
        """Check if Hummingbot Gateway is running and reachable."""
        res = await self._request("GET", "/chains/solana/status")
        if res and res.get("status") == "ok":
            return True
        root_res = await self._request("GET", "/")
        return bool(root_res and root_res.get("status") in ("ok", "running"))

    async def get_status(self) -> Dict[str, Any]:
        """Fetch general Gateway status and Solana chain status."""
        status_info = {
            "gateway_url": self._base_url,
            "network": self._network,
            "reachable": False,
            "chain_status": {},
            "connectors": [],
        }
        res = await self._request("GET", "/chains/solana/status")
        if res:
            status_info["reachable"] = True
            status_info["chain_status"] = res
        connectors_res = await self._request("GET", "/connectors")
        if connectors_res and isinstance(connectors_res.get("connectors"), list):
            status_info["connectors"] = connectors_res["connectors"]
        return status_info

    async def get_balances(self, address: str, tokens: Optional[List[str]] = None) -> Dict[str, float]:
        """Fetch token and native SOL balances for a wallet address on Solana."""
        payload: Dict[str, Any] = {
            "chain": "solana",
            "network": self._network,
            "address": address,
        }
        if tokens:
            payload["tokenSymbols"] = tokens
        res = await self._request("POST", "/chains/solana/balances", json=payload)
        if res and "balances" in res:
            return {k: float(v) for k, v in res["balances"].items()}
        return {}

    async def get_price(
        self,
        connector: str,
        base_token: str,
        quote_token: str = "SOL",
    ) -> Optional[float]:
        """Get mid-price for a token pair on a specific Solana DEX connector."""
        payload = {
            "chain": "solana",
            "network": self._network,
            "connector": connector,
            "base": base_token,
            "quote": quote_token,
        }
        res = await self._request("POST", "/chains/solana/price", json=payload)
        if res and "price" in res:
            try:
                return float(res["price"])
            except (ValueError, TypeError):
                pass
        return None

    async def get_quote(
        self,
        connector: str,
        base_token: str,
        quote_token: str,
        amount: float,
        side: str = "BUY",
    ) -> Optional[Dict[str, Any]]:
        """Fetch execution swap quote from Hummingbot Gateway connector."""
        payload = {
            "chain": "solana",
            "network": self._network,
            "connector": connector,
            "base": base_token,
            "quote": quote_token,
            "amount": str(amount),
            "side": side.upper(),
        }
        return await self._request("POST", "/chains/solana/quote", json=payload)

    async def execute_swap(
        self,
        connector: str,
        base_token: str,
        quote_token: str,
        amount: float,
        side: str,
        wallet_address: str,
        slippage_pct: float = 1.0,
    ) -> Optional[Dict[str, Any]]:
        """Execute a swap via Hummingbot Gateway connector."""
        payload = {
            "chain": "solana",
            "network": self._network,
            "connector": connector,
            "base": base_token,
            "quote": quote_token,
            "amount": str(amount),
            "side": side.upper(),
            "address": wallet_address,
            "slippagePct": slippage_pct,
        }
        return await self._request("POST", "/chains/solana/swap", json=payload)

    async def poll_transaction(self, tx_hash: str) -> Optional[Dict[str, Any]]:
        """Poll confirmation status of a submitted transaction on Solana."""
        payload = {
            "chain": "solana",
            "network": self._network,
            "txHash": tx_hash,
        }
        return await self._request("POST", "/chains/solana/poll", json=payload)
