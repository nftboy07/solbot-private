import asyncio
import logging
import aiohttp
from typing import List, Dict, Set, Optional, Tuple

logger = logging.getLogger("bot.cluster_mapper")

class ClusterMapper:
    """
    Traces creator funding genesis and maps top holder clusters
    to identify stealth pre-mines and cabal operations on pump.fun.
    """
    def __init__(self, bot_instance=None):
        self._bot = bot_instance
        self._cache = {} # {address: root_parent}

    async def _post_rpc(self, rpc_url: str, method: str, params: list) -> Optional[dict]:
        """Utility method to perform a raw JSON-RPC query."""
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(rpc_url, json=payload, timeout=8) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if "error" in data:
                            logger.warning(f"RPC error for {method}: {data['error']}")
                            return None
                        return data.get("result")
        except Exception as e:
            logger.error(f"RPC connection error for {method}: {e}")
        return None

    async def trace_creator_genesis(self, address: str, rpc_url: str, max_hops: int = 3) -> str:
        """
        Recursively traces the parent address that funded the target address.
        Returns the root parent address found.
        """
        if address in self._cache:
            return self._cache[address]

        current = address
        visited = {current}

        for hop in range(max_hops):
            # 1. Fetch transaction signatures for the current address
            sig_data = await self._post_rpc(rpc_url, "getSignaturesForAddress", [current, {"limit": 10}])
            if not sig_data or not isinstance(sig_data, list) or len(sig_data) == 0:
                break

            # Find the oldest transaction signature (represents the funding/creation tx)
            # signatures are returned in reverse chronological order (newest first), so the last signature is the oldest
            oldest_sig = sig_data[-1].get("signature")
            if not oldest_sig:
                break

            # 2. Fetch the transaction details
            tx_data = await self._post_rpc(rpc_url, "getTransaction", [
                oldest_sig, 
                {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}
            ])
            if not tx_data:
                break

            parent = None
            # 3. Parse the SystemProgram transfers to identify the sender
            transaction = tx_data.get("transaction", {})
            message = transaction.get("message", {})
            instructions = message.get("instructions", [])

            for inst in instructions:
                if inst.get("programId") == "11111111111111111111111111111111": # System Program
                    parsed = inst.get("parsed")
                    if parsed and parsed.get("type") == "transfer":
                        info = parsed.get("info", {})
                        if info.get("destination") == current:
                            parent = info.get("source")
                            break

            if not parent:
                # Fallback: check inner instructions
                meta = tx_data.get("meta", {})
                inner_insts = meta.get("innerInstructions", [])
                for inner in inner_insts:
                    for inst in inner.get("instructions", []):
                        if inst.get("programId") == "11111111111111111111111111111111":
                            parsed = inst.get("parsed")
                            if parsed and parsed.get("type") == "transfer":
                                info = parsed.get("info", {})
                                if info.get("destination") == current:
                                    parent = info.get("source")
                                    break
                    if parent:
                        break

            if parent and parent != current and parent not in visited:
                logger.info(f"Hop {hop+1}: Address {current} was funded by {parent}")
                current = parent
                visited.add(current)
            else:
                break

        self._cache[address] = current
        return current

    async def analyze_token_cluster(self, mint: str, rpc_url: str) -> Tuple[float, int, List[dict]]:
        """
        Maps top holder accounts of a token, traces their genesis sources, 
        and calculates a cluster risk score based on shared funding parent roots.
        Returns (risk_score, cluster_size, cluster_details).
        """
        # 1. Fetch top 10 largest token accounts
        accounts_data = await self._post_rpc(rpc_url, "getTokenLargestAccounts", [mint])
        if not accounts_data or not isinstance(accounts_data, list):
            # Try parsing from "value" key inside result dict
            if isinstance(accounts_data, dict):
                accounts_data = accounts_data.get("value", [])
            else:
                return 0.0, 0, []

        top_holders = accounts_data[:10]
        if not top_holders:
            return 0.0, 0, []

        total_supply = 1_000_000_000.0 # Standard pump.fun supply
        holder_shares = []
        
        # We need to resolve the owner address of each token account
        owner_tasks = []
        for holder in top_holders:
            acc_address = holder.get("address")
            amount = float(holder.get("amount", 0)) / 1e6 # 6 decimals
            share_pct = (amount / total_supply) * 100.0
            owner_tasks.append(self._resolve_owner(acc_address, rpc_url, share_pct))

        resolved_holders = await asyncio.gather(*owner_tasks)
        resolved_holders = [rh for rh in resolved_holders if rh]

        # 2. Trace genesis for all resolved holder owner wallets
        trace_tasks = []
        for rh in resolved_holders:
            trace_tasks.append(self.trace_creator_genesis(rh["owner"], rpc_url, max_hops=2))

        roots = await asyncio.gather(*trace_tasks)

        # 3. Associate holder owners with their roots
        root_groups = {} # {root_parent: [holder_dicts]}
        for rh, root in zip(resolved_holders, roots):
            if root:
                rh["root"] = root
                if root not in root_groups:
                    root_groups[root] = []
                root_groups[root].append(rh)

        # 4. Identify the largest cluster (excluding system/exchange roots if any)
        largest_cluster_pct = 0.0
        cluster_wallets = []
        best_root = None

        for root, group in root_groups.items():
            # Skip common exchange hotwallets or known system roots if necessary
            # e.g., standard Raydium authority or pump.fun fee wallets
            if len(group) >= 2:
                total_pct = sum(g["share_pct"] for g in group)
                if total_pct > largest_cluster_pct:
                    largest_cluster_pct = total_pct
                    cluster_wallets = group
                    best_root = root

        # Risk Calculation:
        # If a single root funding parent funded top holders controlling > 25% of the supply
        risk_score = 0.0
        if largest_cluster_pct > 0:
            risk_score = min(100.0, largest_cluster_pct * 2.0)

        logger.info(f"Token {mint} Cluster Analysis: Risk={risk_score:.1f}% | Largest Cluster={largest_cluster_pct:.1f}% ({len(cluster_wallets)} wallets)")
        return risk_score, len(cluster_wallets), cluster_wallets

    async def _resolve_owner(self, token_account: str, rpc_url: str, share_pct: float) -> Optional[dict]:
        """Resolves the owner address of a Solana Token Account."""
        data = await self._post_rpc(rpc_url, "getAccountInfo", [
            token_account,
            {"encoding": "jsonParsed"}
        ])
        if not data:
            return None
        value = data.get("value")
        if value:
            parsed = value.get("data", {}).get("parsed", {})
            info = parsed.get("info", {})
            owner = info.get("owner")
            if owner:
                return {
                    "account": token_account,
                    "owner": owner,
                    "share_pct": share_pct
                }
        return None

    async def get_cluster_report(self, mint_address: str, rpc_url: str) -> str:
        """Returns a string formatted report for the Telegram interface."""
        risk, size, wallets = await self.analyze_token_cluster(mint_address, rpc_url)
        lines = [
            f"<b>🧬 DEVELOPER CLUSTER GENESIS REPORT</b>",
            f"🪙 Token: <code>{mint_address}</code>",
            f"⚠️ Risk Level: <b>{'HIGH RISK 🚨' if risk >= 40.0 else 'MEDIUM RISK ⚠️' if risk >= 15.0 else 'SAFE 🟢'}</b> (<code>{risk:.1f}/100</code>)\n",
            f"📊 <b>CLUSTER DETAILS:</b>",
            f"  • Coordinated Wallets: <code>{size}</code>",
            f"  • Stealth Supply Share: <code>{sum(w['share_pct'] for w in wallets):.1f}%</code>\n"
        ]
        if size > 0:
            lines.append("👥 <b>CLUSTERED WALLETS:</b>")
            for w in wallets:
                lines.append(f"  • Owner: <code>{w['owner'][:6]}...{w['owner'][-4:]}</code> ({w['share_pct']:.2f}%)")
            lines.append(f"\nFunding Root: <code>{wallets[0]['root']}</code>")
        else:
            lines.append("No stealth-funded clusters detected in the top holders list.")
        return "\n".join(lines)

    async def get_holder_relationship_map(self, mint: str, rpc_url: str) -> str:
        """
        Maps all top 10 holders, groups them by their genesis roots,
        and renders a beautiful ASCII relationship tree.
        """
        accounts_data = await self._post_rpc(rpc_url, "getTokenLargestAccounts", [mint])
        if not accounts_data or not isinstance(accounts_data, list):
            if isinstance(accounts_data, dict):
                accounts_data = accounts_data.get("value", [])
            else:
                return "❌ Could not fetch top holders data."

        top_holders = accounts_data[:10]
        if not top_holders:
            return "❌ No holders found for this token."

        total_supply = 1_000_000_000.0
        
        # 1. Resolve owners
        owner_tasks = []
        for holder in top_holders:
            acc_address = holder.get("address")
            amount = float(holder.get("amount", 0)) / 1e6
            share_pct = (amount / total_supply) * 100.0
            owner_tasks.append(self._resolve_owner(acc_address, rpc_url, share_pct))

        resolved_holders = await asyncio.gather(*owner_tasks)
        resolved_holders = [rh for rh in resolved_holders if rh]

        # 2. Trace genesis roots
        trace_tasks = []
        for rh in resolved_holders:
            trace_tasks.append(self.trace_creator_genesis(rh["owner"], rpc_url, max_hops=2))

        roots = await asyncio.gather(*trace_tasks)

        # 3. Associate owners with their roots
        root_groups = {}  # {root: [holders]}
        for rh, root in zip(resolved_holders, roots):
            if not root:
                root = "Unknown / Direct"
            rh["root"] = root
            if root not in root_groups:
                root_groups[root] = []
            root_groups[root].append(rh)

        # 4. Format ASCII Output
        lines = [
            "🧬 <b>HOLDER RELATIONSHIP MAP</b>",
            f"🪙 Token: <code>{mint}</code>\n",
            "<code>"
        ]

        # Process roots with multiple wallets (Clusters) first
        for root, group in root_groups.items():
            if len(group) >= 2 and root != "Unknown / Direct":
                total_pct = sum(g["share_pct"] for g in group)
                lines.append(f"[Cluster Root] ──> Funder ({root[:6]}...{root[-4:]}) | {total_pct:.1f}% supply")
                for i, g in enumerate(group):
                    is_last = (i == len(group) - 1)
                    branch = "└──>" if is_last else "├──>"
                    lines.append(f"  {branch} Wallet {i+1} ({g['owner'][:6]}...{g['owner'][-4:]}): {g['share_pct']:.2f}%")
                lines.append("")

        # Process independent or single-holder roots next
        lines.append("[Independent / Other Top Holders]")
        ind_count = 0
        for root, group in root_groups.items():
            if len(group) == 1 or root == "Unknown / Direct":
                for g in group:
                    ind_count += 1
                    lines.append(f"  ├── Wallet {ind_count} ({g['owner'][:6]}...{g['owner'][-4:]}): {g['share_pct']:.2f}%")
        
        if ind_count == 0:
            lines.append("  (None - 100% of top holders are clustered!)")
            
        lines.append("</code>")
        return "\n".join(lines)
