import logging
import asyncio
import time
import json
import uuid
from typing import Dict, List, Set, Optional, Any
from dataclasses import dataclass, asdict

logger = logging.getLogger(__name__)

@dataclass
class WalletNode:
    address: str
    cluster_id: Optional[str] = None
    centrality_score: float = 0.0
    influence_score: float = 0.0
    expected_roi: float = 0.0
    last_active: float = 0.0
    tags: List[str] = None
    
    # V4 metrics
    win_rate: float = 0.0
    avg_hold_time: float = 0.0
    avg_multiple: float = 0.0
    rug_pct: float = 0.0
    avg_entry_mcap: float = 0.0
    avg_exit_mcap: float = 0.0
    wallet_score: float = 0.0
    weight: float = 0.0

    def __post_init__(self):
        if self.tags is None:
            self.tags = []

class WalletGraphEngine:
    """
    Robust, 100% async Smart Wallet Graph engine.
    Models relationships, cluster membership, and V4 copytrade triggers.
    """

    def __init__(self, db=None, bot=None):
        self.db = db
        self.bot = bot
        self.nodes: Dict[str, WalletNode] = {}
        self.clusters: Dict[str, Set[str]] = {}
        # Tracks mint -> {wallet_address: timestamp}
        self.activity_ledger: Dict[str, Dict[str, float]] = {}
        self._lock = asyncio.Lock()

    async def initialize(self):
        """Load initial state from database if available."""
        if not self.db:
            return

        try:
            rows = await self.db._execute_read('SELECT * FROM wallets')
            for row in rows:
                wallet_dict = dict(row)
                address = wallet_dict['address']
                node = WalletNode(
                    address=address,
                    expected_roi=wallet_dict.get('historical_roi', 0.0),
                    last_active=time.time(),
                    win_rate=wallet_dict.get('win_rate', 0.0),
                    avg_hold_time=wallet_dict.get('avg_hold_time', 0.0),
                    avg_multiple=wallet_dict.get('avg_multiple', 0.0),
                    rug_pct=wallet_dict.get('rug_pct', 0.0),
                    avg_entry_mcap=wallet_dict.get('avg_entry_mcap', 0.0),
                    avg_exit_mcap=wallet_dict.get('avg_exit_mcap', 0.0),
                    wallet_score=wallet_dict.get('wallet_score', 0.0),
                    weight=wallet_dict.get('weight', 0.0)
                )
                self.nodes[address] = node
            logger.info(f'Initialized WalletGraphEngine with {len(self.nodes)} nodes.')
        except Exception as e:
            logger.error(f'Failed to initialize WalletGraphEngine: {e}')

    async def record_activity(self, wallet_address: str, mint_address: str, metadata: Dict[str, Any] = None):
        """Record a wallet buying into a token and check for co-buying signals."""
        async with self._lock:
            ts = time.time()
            if wallet_address not in self.nodes:
                self.nodes[wallet_address] = WalletNode(address=wallet_address, last_active=ts)
            else:
                self.nodes[wallet_address].last_active = ts

            if mint_address not in self.activity_ledger:
                self.activity_ledger[mint_address] = {}
            
            self.activity_ledger[mint_address][wallet_address] = ts

            # Trigger co-buying detection
            await self._detect_cobuying_cluster(mint_address, wallet_address, metadata)
            
            # Update graph analytics periodically
            await self._update_node_metrics(wallet_address)

    async def _detect_cobuying_cluster(self, mint: str, triggering_wallet: str, metadata: Dict[str, Any] = None):
        """
        Detects if a cluster of smart/elite wallets are buying the same token.
        V4 smart wallet copy conditions: 3 smart wallets OR 2 elite wallets buy within 120s.
        """
        window = 120
        now = time.time()
        
        recent_buyers = {
            addr: t for addr, t in self.activity_ledger[mint].items() 
            if now - t <= window
        }

        smart_count = 0
        elite_count = 0
        smart_buyers_list = []
        elite_buyers_list = []
        
        for addr in recent_buyers:
            node = self.nodes.get(addr)
            if node:
                # Elite wallet check: score >= 85 or weight >= 80
                if node.wallet_score >= 85 or node.weight >= 80:
                    elite_count += 1
                    elite_buyers_list.append(addr)
                # Smart wallet check: score >= 60 or weight >= 50
                if node.wallet_score >= 60 or node.weight >= 50:
                    smart_count += 1
                    smart_buyers_list.append(addr)

        trigger_copy = (smart_count >= 3) or (elite_count >= 2)

        if trigger_copy:
            confidence = min(1.0, (smart_count / 10.0) + (elite_count / 5.0))
            avg_roi = sum(self.nodes[addr].expected_roi for addr in recent_buyers if addr in self.nodes) / len(recent_buyers) if recent_buyers else 0.0
            
            signal_data = {
                'event_id': str(uuid.uuid4()),
                'signal_id': f'COBUY_{mint}_{int(now)}',
                'mint': mint,
                'wallet_signal': 'SMART_MONEY_COPYTRADE',
                'confidence': confidence,
                'raw_signal_data': json.dumps({
                    'buyers': list(recent_buyers.keys()),
                    'smart_buyers': smart_buyers_list,
                    'elite_buyers': elite_buyers_list,
                    'avg_expected_roi': avg_roi,
                    'metadata': metadata
                }),
                'timestamp': now
            }
            
            if self.db:
                await self.db.log_signal_event(signal_data)
            
            logger.info(f'V4 Copytrade Triggered for {mint}: {smart_count} smart, {elite_count} elite.')

            # Send Telegram alert for co-buying cluster
            if self.bot and self.bot._telegram:
                alert_msg = (
                    f"🚀 <b>SMART MONEY COPYTRADE TRIGGERED!</b> 🚀\n\n"
                    f"Token: <b>{mint[:8]}...</b>\n"
                    f"Mint: <code>{mint}</code>\n"
                    f"Confidence: <code>{confidence*100:.1f}%</code>\n"
                    f"Smart Buyers: <code>{smart_count} wallets</code>\n"
                    f"Elite Buyers: <code>{elite_count} wallets</code>\n"
                    f"Avg ROI of Buyers: <code>+{avg_roi:.2f} SOL</code>\n\n"
                    f"👉 <a href='https://pump.fun/{mint}'>Buy on pump.fun</a>"
                )
                asyncio.create_task(self.bot._telegram.send_message(alert_msg))

            # Auto-trade co-buying signals
            if self.bot and getattr(self.bot, '_autobuy_enabled', False):
                logger.info(f'Triggering buy for copytrade {mint} with confidence {confidence:.2f}')
                asyncio.create_task(self.bot.execute_kol_snipe(mint, f"Smart Copy ({smart_count}S/{elite_count}E)"))

    async def _update_node_metrics(self, wallet_address: str):
        """Asynchronously update centrality, influence, and V4 wallet score/weight."""
        node = self.nodes.get(wallet_address)
        if not node:
            return

        shared_count = 0
        for mint, buyers in self.activity_ledger.items():
            if wallet_address in buyers and len(buyers) > 1:
                shared_count += (len(buyers) - 1)

        node.centrality_score = min(1.0, shared_count / 100.0)
        
        # Calculate V4 score & weight based on Win Rate, Avg Multiple, Rug %
        # Formula: Win rate (up to 40 pts), Avg Multiple (up to 40 pts), Rug % penalty (up to 20 pts)
        win_rate_score = node.win_rate * 40.0
        multiple_score = min(40.0, (node.avg_multiple / 18.0) * 40.0)
        rug_penalty = min(20.0, (node.rug_pct / 50.0) * 20.0)
        
        node.wallet_score = max(0.0, min(100.0, win_rate_score + multiple_score + 20.0 - rug_penalty))
        node.weight = node.wallet_score
        
        # Influence score blends centrality and expected ROI
        node.influence_score = (node.centrality_score * 0.4) + (min(1.0, node.expected_roi / 5.0) * 0.6)
        
        # Persist to DB
        if self.db:
            await self.db.upsert_wallet(
                wallet_address,
                historical_roi=node.expected_roi,
                win_rate=node.win_rate,
                avg_hold_time=node.avg_hold_time,
                avg_multiple=node.avg_multiple,
                rug_pct=node.rug_pct,
                avg_entry_mcap=node.avg_entry_mcap,
                avg_exit_mcap=node.avg_exit_mcap,
                wallet_score=node.wallet_score,
                weight=node.weight,
                tier='ALPHA' if node.wallet_score >= 85 else 'BETA'
            )

    async def get_cluster_info(self, cluster_id: str) -> List[WalletNode]:
        """Get all wallet nodes belonging to a specific cluster."""
        addresses = self.clusters.get(cluster_id, set())
        return [self.nodes[addr] for addr in addresses if addr in self.nodes]

    async def assign_to_cluster(self, wallet_address: str, cluster_id: str):
        """Assign a wallet to a relationship cluster."""
        async with self._lock:
            if wallet_address not in self.nodes:
                self.nodes[wallet_address] = WalletNode(address=wallet_address)
            
            self.nodes[wallet_address].cluster_id = cluster_id
            
            if cluster_id not in self.clusters:
                self.clusters[cluster_id] = set()
            self.clusters[cluster_id].add(wallet_address)
            
            logger.debug(f'Assigned {wallet_address} to cluster {cluster_id}')
