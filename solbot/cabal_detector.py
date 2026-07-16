"""Cabal and developer cluster detection for pre-buy risk gating."""

import asyncio
import logging
from dataclasses import dataclass, field
from time import time
from typing import Optional

import aiohttp

from solbot.config import CabalConfig

logger = logging.getLogger("bot.cabal_detector")


@dataclass(frozen=True)
class HolderFunding:
    account: str
    owner: str
    share_pct: float
    root: str = ""


@dataclass(frozen=True)
class CabalReport:
    mint: str
    blocked: bool
    largest_cluster_pct: float
    largest_cluster_root: str
    cluster_size: int
    reason: str
    holders_checked: int
    clusters: dict[str, list[HolderFunding]] = field(default_factory=dict)


class CabalDetector:
    """Detects stealth-funded holder clusters before a token is bought."""

    def __init__(self, bot, config: CabalConfig | None = None):
        self._bot = bot
        self._config = config or bot._config.cabal
        self._cache: dict[str, tuple[float, CabalReport]] = {}

    async def stop(self):
        self._cache.clear()

    async def analyze_token(
        self,
        mint: str,
        creator: Optional[str] = None,
        rpc_url: Optional[str] = None,
    ) -> CabalReport:
        if not self._config.enabled:
            return CabalReport(
                mint=mint,
                blocked=False,
                largest_cluster_pct=0.0,
                largest_cluster_root="",
                cluster_size=0,
                reason="Cabal detector disabled.",
                holders_checked=0,
            )

        cached = self._cache.get(mint)
        now = time()
        if cached and now - cached[0] <= self._config.cache_ttl_seconds:
            return cached[1]

        rpc_url = rpc_url or await self._bot._pump_client._get_rpc_url()
        holders = await self._fetch_top_holder_funding(mint, rpc_url)
        roots = await asyncio.gather(
            *[
                self._trace_holder_root(holder.owner, rpc_url)
                for holder in holders
            ],
            return_exceptions=True,
        )
        clean_roots = [
            root if isinstance(root, str) and root else holder.owner
            for holder, root in zip(holders, roots)
        ]
        report = self._build_report(mint, holders, clean_roots, creator)
        self._cache[mint] = (now, report)

        level = "BLOCK" if report.blocked else "PASS"
        logger.info(
            "Cabal %s %s | largest_cluster=%.2f%% size=%s root=%s | %s",
            level,
            mint,
            report.largest_cluster_pct,
            report.cluster_size,
            _short(report.largest_cluster_root),
            report.reason,
        )
        return report

    async def _fetch_top_holder_funding(self, mint: str, rpc_url: str) -> list[HolderFunding]:
        supply = await self._fetch_token_supply(mint, rpc_url)
        accounts = await self._post_rpc(rpc_url, "getTokenLargestAccounts", [mint])
        if isinstance(accounts, dict):
            accounts = accounts.get("value", [])
        if not isinstance(accounts, list):
            return []

        holders: list[HolderFunding] = []
        for holder in accounts[: self._config.top_holders_limit]:
            account = holder.get("address")
            if not account:
                continue
            amount = _holder_amount(holder)
            share_pct = (amount / supply * 100.0) if supply > 0 else 0.0
            owner = await self._resolve_owner(account, rpc_url)
            if owner:
                holders.append(HolderFunding(account=account, owner=owner, share_pct=share_pct))
        return holders

    async def _fetch_token_supply(self, mint: str, rpc_url: str) -> float:
        data = await self._post_rpc(rpc_url, "getTokenSupply", [mint])
        if isinstance(data, dict):
            value = data.get("value", {})
            ui_amount = value.get("uiAmount")
            if ui_amount is not None:
                return float(ui_amount)
            amount = value.get("amount")
            decimals = int(value.get("decimals", 0) or 0)
            if amount is not None:
                return float(amount) / (10 ** decimals)
        return 1_000_000_000.0

    async def _resolve_owner(self, token_account: str, rpc_url: str) -> Optional[str]:
        data = await self._post_rpc(
            rpc_url,
            "getAccountInfo",
            [token_account, {"encoding": "jsonParsed"}],
        )
        if not isinstance(data, dict):
            return None
        value = data.get("value")
        if not value:
            return None
        parsed = value.get("data", {}).get("parsed", {})
        return parsed.get("info", {}).get("owner")

    async def _trace_holder_root(self, owner: str, rpc_url: str) -> str:
        mapper = getattr(self._bot, "_cluster_mapper", None)
        if mapper:
            return await mapper.trace_creator_genesis(
                owner,
                rpc_url,
                max_hops=self._config.max_trace_hops,
            )
        return await self._trace_funding_root(owner, rpc_url)

    async def _trace_funding_root(self, owner: str, rpc_url: str) -> str:
        current = owner
        visited = {owner}
        for _ in range(self._config.max_trace_hops):
            signatures = await self._post_rpc(
                rpc_url,
                "getSignaturesForAddress",
                [current, {"limit": 10}],
            )
            if not isinstance(signatures, list) or not signatures:
                break
            oldest_signature = signatures[-1].get("signature")
            if not oldest_signature:
                break
            tx = await self._post_rpc(
                rpc_url,
                "getTransaction",
                [
                    oldest_signature,
                    {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0},
                ],
            )
            parent = _extract_funding_source(tx, current)
            if not parent or parent in visited:
                break
            current = parent
            visited.add(current)
        return current

    def _build_report(
        self,
        mint: str,
        holders: list[HolderFunding],
        roots: list[str],
        creator: Optional[str] = None,
    ) -> CabalReport:
        clusters: dict[str, list[HolderFunding]] = {}
        for holder, root in zip(holders, roots):
            root_key = root or holder.owner
            if creator and holder.owner == creator:
                root_key = creator
            rooted_holder = HolderFunding(
                account=holder.account,
                owner=holder.owner,
                share_pct=holder.share_pct,
                root=root_key,
            )
            clusters.setdefault(root_key, []).append(rooted_holder)

        largest_root = ""
        largest_group: list[HolderFunding] = []
        largest_pct = 0.0
        for root, group in clusters.items():
            if len(group) < 2 and root != creator:
                continue
            total_pct = sum(holder.share_pct for holder in group)
            if total_pct > largest_pct:
                largest_root = root
                largest_group = group
                largest_pct = total_pct

        blocked = largest_pct >= self._config.max_cluster_supply_pct
        if blocked:
            reason = (
                f"CABAL CLUSTER DETECTED: {len(largest_group)} wallets funded by "
                f"{_short(largest_root)} control {largest_pct:.1f}% of supply."
            )
        elif largest_group:
            reason = (
                f"Largest funding cluster controls {largest_pct:.1f}% of supply "
                f"across {len(largest_group)} wallets."
            )
        else:
            reason = "No shared-funding cabal cluster above threshold."

        return CabalReport(
            mint=mint,
            blocked=blocked,
            largest_cluster_pct=largest_pct,
            largest_cluster_root=largest_root,
            cluster_size=len(largest_group),
            reason=reason,
            holders_checked=len(holders),
            clusters=clusters,
        )

    async def _post_rpc(self, rpc_url: str, method: str, params: list) -> Optional[dict | list]:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params,
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    rpc_url,
                    json=payload,
                    timeout=self._config.rpc_timeout_seconds,
                ) as resp:
                    if resp.status != 200:
                        logger.warning("RPC %s failed with status %s", method, resp.status)
                        return None
                    data = await resp.json()
                    if "error" in data:
                        logger.warning("RPC %s error: %s", method, data["error"])
                        return None
                    return data.get("result")
        except Exception as exc:
            logger.error("RPC %s request failed: %s", method, exc)
            return None


def _holder_amount(holder: dict) -> float:
    ui_amount = holder.get("uiAmount")
    if ui_amount is not None:
        return float(ui_amount)
    ui_amount_string = holder.get("uiAmountString")
    if ui_amount_string:
        return float(ui_amount_string)
    amount = holder.get("amount")
    decimals = int(holder.get("decimals", 6) or 6)
    return float(amount or 0) / (10 ** decimals)


def _extract_funding_source(tx: Optional[dict | list], destination: str) -> Optional[str]:
    if not isinstance(tx, dict):
        return None
    instructions = tx.get("transaction", {}).get("message", {}).get("instructions", [])
    for instruction in instructions:
        source = _transfer_source(instruction, destination)
        if source:
            return source
    for inner in tx.get("meta", {}).get("innerInstructions", []) or []:
        for instruction in inner.get("instructions", []) or []:
            source = _transfer_source(instruction, destination)
            if source:
                return source
    return None


def _transfer_source(instruction: dict, destination: str) -> Optional[str]:
    if instruction.get("programId") != "11111111111111111111111111111111":
        return None
    parsed = instruction.get("parsed") or {}
    if parsed.get("type") != "transfer":
        return None
    info = parsed.get("info") or {}
    if info.get("destination") == destination:
        return info.get("source")
    return None


def _short(address: str) -> str:
    if not address:
        return "unknown"
    if len(address) <= 12:
        return address
    return f"{address[:6]}...{address[-4:]}"
