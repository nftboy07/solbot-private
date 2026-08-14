"""On-chain Wallet Graph & Cabal Cluster Detection Engine."""

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Set, Tuple

logger = logging.getLogger("bot.cabal_graph")


@dataclass
class ClusterAnalysisResult:
    """Cluster detection breakdown for a group of wallets."""
    total_wallets: int
    connected_components_count: int
    largest_cluster_size: int
    cabal_detected: bool
    clusters: List[List[str]] = field(default_factory=list)
    common_funding_sources: List[str] = field(default_factory=list)
    risk_score: int = 0


class WalletGraphEngine:
    """Graph analyzer mapping transfer flows, common funder trees, and cabal rings."""

    def __init__(self, max_cabal_cluster_size: int = 3):
        self.max_cabal_cluster_size = max_cabal_cluster_size
        self._edges: Dict[str, Set[str]] = {}
        self._wallet_funders: Dict[str, str] = {}

    def add_transfer(self, from_wallet: str, to_wallet: str):
        """Add a directed funding or transfer edge between two wallets."""
        if from_wallet and to_wallet and from_wallet != to_wallet:
            self._edges.setdefault(from_wallet, set()).add(to_wallet)
            self._edges.setdefault(to_wallet, set()).add(from_wallet)
            self._wallet_funders[to_wallet] = from_wallet

    def analyze_holders(self, holder_addresses: List[str]) -> ClusterAnalysisResult:
        """
        Analyze a list of top token holders to identify connected clusters.
        Uses breadth-first search to find connected components within the holder subset,
        including common funder links.
        """
        # Link holders sharing common root funder
        funder_groups: Dict[str, List[str]] = {}
        for wallet in holder_addresses:
            f = self._wallet_funders.get(wallet)
            if f:
                funder_groups.setdefault(f, []).append(wallet)

        for f, group in funder_groups.items():
            for w1 in group:
                for w2 in group:
                    if w1 != w2:
                        self._edges.setdefault(w1, set()).add(w2)
                        self._edges.setdefault(w2, set()).add(w1)

        holder_set = set(holder_addresses)
        visited = set()
        clusters = []
        funders_found = []

        for wallet in holder_addresses:
            if wallet in visited:
                continue

            # BFS for component
            component = []
            queue = [wallet]
            visited.add(wallet)

            while queue:
                current = queue.pop(0)
                component.append(current)

                # Check common funder
                funder = self._wallet_funders.get(current)
                if funder:
                    funders_found.append(funder)

                # Traverse neighbors that are also in holder_set
                for neighbor in self._edges.get(current, set()):
                    if neighbor in holder_set and neighbor not in visited:
                        visited.add(neighbor)
                        queue.append(neighbor)

            if len(component) > 1:
                clusters.append(component)

        largest_cluster = max([len(c) for c in clusters], default=1)
        cabal_detected = largest_cluster >= self.max_cabal_cluster_size

        # Score (0 = clean, 100 = high cabal concentration)
        risk_score = min(100, largest_cluster * 25)

        return ClusterAnalysisResult(
            total_wallets=len(holder_addresses),
            connected_components_count=len(clusters),
            largest_cluster_size=largest_cluster,
            cabal_detected=cabal_detected,
            clusters=clusters,
            common_funding_sources=list(set(funders_found)),
            risk_score=risk_score,
        )

    def propagate_blacklist(self, seed_blacklisted_wallets: Set[str], max_hops: int = 2) -> Set[str]:
        """Find all wallets funded by or tightly connected to blacklisted addresses."""
        full_blacklist = set(seed_blacklisted_wallets)
        current_layer = set(seed_blacklisted_wallets)

        for _ in range(max_hops):
            next_layer = set()
            for wallet in current_layer:
                for neighbor in self._edges.get(wallet, set()):
                    if neighbor not in full_blacklist:
                        full_blacklist.add(neighbor)
                        next_layer.add(neighbor)
            current_layer = next_layer
            if not current_layer:
                break

        return full_blacklist
