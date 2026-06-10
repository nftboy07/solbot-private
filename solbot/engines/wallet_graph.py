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

    def __post_init__(self):
        if self.tags is None:
            self.tags = []

class WalletGraphEngine:
    '''
    Robust, 100% async Smart Wallet Graph engine.
    Models relationships, cluster membership, and high-conviction co-buying signals.
    '''

    def __init__(self, db=None):
        self.db = db
        self.nodes: Dict[str, WalletNode] = {}
        self.clusters: Dict[str, Set[str]] = {}
        # Tracks mint -> {wallet_address: timestamp}
        self.activity_ledger: Dict[str, Dict[str, float]] = {}
        self._lock = asyncio.Lock()

    async def initialize(self):
        '''Load initial state from database if available.'''
        if not self.db:
            return

        try:
            # Note: We might need to add cluster-specific tables in future migrations
            # For now, we use the existing wallets table.
            rows = await self.db._execute_read('SELECT * FROM wallets')
            for row in rows:
                wallet_dict = dict(row)
                address = wallet_dict['address']
                node = WalletNode(
                    address=address,
                    expected_roi=wallet_dict.get('historical_roi', 0.0),
                    last_active=time.time()
                )
                self.nodes[address] = node
            logger.info(f'Initialized WalletGraphEngine with {len(self.nodes)} nodes.')
        except Exception as e:
            logger.error(f'Failed to initialize WalletGraphEngine: {e}')

    async def record_activity(self, wallet_address: str, mint_address: str, metadata: Dict[str, Any] = None):
        '''Record a wallet buying into a token and check for co-buying signals.'''
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
            
            # Update graph analytics periodically or on event
            await self._update_node_metrics(wallet_address)

    async def _detect_cobuying_cluster(self, mint: str, triggering_wallet: str, metadata: Dict[str, Any] = None):
        '''
        Detects if a cluster of high-conviction wallets are buying the same token.
        Window: 300 seconds (5 minutes).
        '''
        window = 300
        now = time.time()
        
        recent_buyers = {
            addr: t for addr, t in self.activity_ledger[mint].items() 
            if now - t <= window
        }

        if len(recent_buyers) >= 3:
            # Calculate collective conviction
            total_influence = sum(self.nodes[addr].influence_score for addr in recent_buyers if addr in self.nodes)
            avg_roi = sum(self.nodes[addr].expected_roi for addr in recent_buyers if addr in self.nodes) / len(recent_buyers)

            if total_influence > 0.5 or len(recent_buyers) >= 5:
                signal_data = {
                    'event_id': str(uuid.uuid4()),
                    'signal_id': f'COBUY_{mint}_{int(now)}',
                    'mint': mint,
                    'wallet_signal': 'HIGH_CONVICTION_COBUY',
                    'confidence': min(1.0, (len(recent_buyers) / 10.0) + total_influence),
                    'raw_signal_data': json.dumps({
                        'buyers': list(recent_buyers.keys()),
                        'avg_expected_roi': avg_roi,
                        'total_influence': total_influence,
                        'metadata': metadata
                    }),
                    'timestamp': now
                }
                
                if self.db:
                    await self.db.log_signal_event(signal_data)
                
                logger.info(f'Significant cluster movement detected for {mint}: {len(recent_buyers)} wallets.')

    async def _update_node_metrics(self, wallet_address: str):
        '''Asynchronously update centrality and influence scores.'''
        node = self.nodes.get(wallet_address)
        if not node:
            return

        # Simple centrality: how many shared tokens with other known smart wallets
        # In a real graph, this would use PageRank or similar on shared-token edges
        shared_count = 0
        for mint, buyers in self.activity_ledger.items():
            if wallet_address in buyers and len(buyers) > 1:
                shared_count += (len(buyers) - 1)

        node.centrality_score = min(1.0, shared_count / 100.0)
        node.influence_score = (node.centrality_score * 0.4) + (min(1.0, node.expected_roi / 5.0) * 0.6)
        
        # Persist to DB
        if self.db:
            await self.db.upsert_wallet(
                wallet_address,
                historical_roi=node.expected_roi,
                tier='ALPHA' if node.influence_score > 0.7 else 'BETA'
            )

    async def get_cluster_info(self, cluster_id: str) -> List[WalletNode]:
        '''Get all wallet nodes belonging to a specific cluster.'''
        addresses = self.clusters.get(cluster_id, set())
        return [self.nodes[addr] for addr in addresses if addr in self.nodes]

    async def assign_to_cluster(self, wallet_address: str, cluster_id: str):
        '''Assign a wallet to a relationship cluster.'''
        async with self._lock:
            if wallet_address not in self.nodes:
                self.nodes[wallet_address] = WalletNode(address=wallet_address)
            
            self.nodes[wallet_address].cluster_id = cluster_id
            
            if cluster_id not in self.clusters:
                self.clusters[cluster_id] = set()
            self.clusters[cluster_id].add(wallet_address)
            
            logger.debug(f'Assigned {wallet_address} to cluster {cluster_id}')
