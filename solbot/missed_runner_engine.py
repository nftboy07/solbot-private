"""Missed Runner Pattern Engine & Smart Early Wallet Harvester for Solbot."""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Any, Tuple

logger = logging.getLogger("bot.missed_runner")


@dataclass
class PumpedTokenRecord:
    """Historical record of a token that generated 5x - 7500x profit."""
    symbol: str
    name: str
    mint: str
    alert_mcap: float
    peak_mcap: float
    multiplier: float
    time_to_peak_mins: int
    early_buyers: List[str] = field(default_factory=list)
    common_patterns: List[str] = field(default_factory=list)


@dataclass
class RunnerPatternSignature:
    """Statistical pattern signature extracted from pumped tokens."""
    min_mcap_usd: float
    max_mcap_usd: float
    min_unique_buyers: int
    min_buy_volume_ratio: float
    max_dev_holding_pct: float
    optimal_entry_window_mins: int
    smart_wallets_whitelist: Set[str] = field(default_factory=set)


class MissedRunnerEngine:
    """
    Harvests missed runner data, extracts multi-bagger patterns,
    collects early profitable wallets, and filters new tokens to match winning profiles.
    """

    # Core historical pumped tokens harvested from live alerts
    SEED_RUNNERS = [
        PumpedTokenRecord(
            symbol="PARKIFY",
            name="South Park Mode",
            mint="7Syw6tu4Jx692uhoryjok5yBwTqje1oftit9E3LHpump",
            alert_mcap=189_881.0,
            peak_mcap=1_431_035_025.0,
            multiplier=7536.5,
            time_to_peak_mins=291,
            early_buyers=["7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU", "B4G24zZRUjZcuu4d5QTLpGFcaptXUUNmrLL4VBEmpump"],
            common_patterns=["Viral South Park narrative", "Bonding curve graduated < 5 mins", "Massive wallet distribution"],
        ),
        PumpedTokenRecord(
            symbol="GENTLE",
            name="Gentle Giant",
            mint="Gepjas79VptWRYEVM4cUvET9RAyEEFrF4XhukZakpump",
            alert_mcap=328_442.0,
            peak_mcap=1_820_739.0,
            multiplier=5.5,
            time_to_peak_mins=1253,
            early_buyers=["Gepjas79VptWRYEVM4cUvET9RAyEEFrF4XhukZakpump"],
            common_patterns=["Steady accumulation post-migration", "Sustained buy volume > 65%"],
        ),
        PumpedTokenRecord(
            symbol="BULLWHALE",
            name="BULLWHALE",
            mint="B4G24zZRUjZcuu4d5QTLpGFcaptXUUNmrLL4VBEmpump",
            alert_mcap=227_567.0,
            peak_mcap=1_204_291.0,
            multiplier=5.3,
            time_to_peak_mins=113,
            early_buyers=[],
            common_patterns=["Fast 113 min breakout", "Whale accumulation pre-graduation"],
        ),
        PumpedTokenRecord(
            symbol="TOADZ",
            name="Toadz Family",
            mint="9sa9nwDeoFMYShhGgVxBJTHTXHF6jbQnxRXF1Pqrpump",
            alert_mcap=93_814.0,
            peak_mcap=474_990.0,
            multiplier=5.1,
            time_to_peak_mins=1131,
            early_buyers=[],
            common_patterns=["Micro-cap entry < $100k", "Gradual bonding curve completion"],
        ),
        PumpedTokenRecord(
            symbol="Modi",
            name="56 inch ka chhota bandar",
            mint="EWqybQSYa93Wjm9D5VVqh1Z99cCvThNS6Kyesaakpump",
            alert_mcap=766_875.0,
            peak_mcap=3_865_312.0,
            multiplier=5.0,
            time_to_peak_mins=894,
            early_buyers=[],
            common_patterns=["Political meme narrative", "High volume breakout on Raydium"],
        ),
    ]

    def __init__(self, bot_instance=None):
        self._bot = bot_instance
        self._pumped_tokens: Dict[str, PumpedTokenRecord] = {r.mint: r for r in self.SEED_RUNNERS}
        self._smart_early_wallets: Set[str] = set()
        self._pattern_signature: Optional[RunnerPatternSignature] = None
        self._recalculate_pattern_signature()

    def add_missed_token(
        self,
        symbol: str,
        name: str,
        mint: str,
        alert_mcap: float,
        current_mcap: float,
        multiplier: float,
        elapsed_mins: int,
        early_wallets: Optional[List[str]] = None,
    ):
        """Add a newly discovered missed runner to the learning dataset."""
        record = PumpedTokenRecord(
            symbol=symbol,
            name=name,
            mint=mint,
            alert_mcap=alert_mcap,
            peak_mcap=current_mcap,
            multiplier=multiplier,
            time_to_peak_mins=elapsed_mins,
            early_buyers=early_wallets or [],
            common_patterns=["Live missed tracker capture"],
        )
        self._pumped_tokens[mint] = record
        if early_wallets:
            for w in early_wallets:
                self._smart_early_wallets.add(w)
        self._recalculate_pattern_signature()
        logger.info(f"🚀 MissedRunnerEngine: Ingested {symbol} ({multiplier:.1f}x) into pattern database.")

    def _recalculate_pattern_signature(self):
        """Extract optimal parameter bounds from all harvested 5x-7500x tokens."""
        if not self._pumped_tokens:
            return

        records = list(self._pumped_tokens.values())
        alert_mcaps = [r.alert_mcap for r in records if r.alert_mcap > 0]
        
        # Calculate sweet-spot entry bounds
        min_mcap = min(alert_mcaps) * 0.85 if alert_mcaps else 80_000.0
        max_mcap = max(alert_mcaps) * 1.15 if alert_mcaps else 850_000.0

        # Harvest all early buyers into smart wallet set
        for r in records:
            for w in r.early_buyers:
                if len(w) >= 32:
                    self._smart_early_wallets.add(w)

        self._pattern_signature = RunnerPatternSignature(
            min_mcap_usd=round(min_mcap, 0),
            max_mcap_usd=round(max_mcap, 0),
            min_unique_buyers=20,
            min_buy_volume_ratio=0.65,
            max_dev_holding_pct=0.03,
            optimal_entry_window_mins=30,
            smart_wallets_whitelist=set(self._smart_early_wallets),
        )

    def matches_runner_pattern(
        self,
        mcap_usd: float,
        buy_ratio: float,
        unique_buyers: int,
        dev_holding_pct: float,
        buyer_wallets: Optional[Set[str]] = None,
    ) -> Tuple[bool, float, str]:
        """
        Evaluate if a candidate token matches the winning missed-runner pattern.
        Returns: (matches: bool, match_score: float, reason: str)
        """
        sig = self._pattern_signature
        if not sig:
            return False, 0.0, "No signature model built"

        reasons = []
        score = 0.0

        # 1. Market Cap Sweet Spot ($80k - $880k)
        if sig.min_mcap_usd <= mcap_usd <= sig.max_mcap_usd:
            score += 35.0
            reasons.append(f"MCap ${mcap_usd:,.0f} in sweet-spot (${sig.min_mcap_usd:,.0f}-${sig.max_mcap_usd:,.0f})")
        else:
            reasons.append(f"MCap ${mcap_usd:,.0f} outside sweet-spot")

        # 2. Buy/Sell Ratio (>= 65% Buy Volume)
        if buy_ratio >= sig.min_buy_volume_ratio:
            score += 25.0
            reasons.append(f"Strong buy pressure ({buy_ratio*100:.1f}%)")
        else:
            reasons.append(f"Weak buy pressure ({buy_ratio*100:.1f}%)")

        # 3. Unique Buyer Velocity (>= 20 wallets)
        if unique_buyers >= sig.min_unique_buyers:
            score += 20.0
            reasons.append(f"High buyer count ({unique_buyers})")

        # 4. Low Dev Retention (<= 3%)
        if dev_holding_pct <= sig.max_dev_holding_pct:
            score += 10.0
            reasons.append("Dev holding safe (<3%)")

        # 5. Smart Wallet Overlap
        if buyer_wallets and sig.smart_wallets_whitelist:
            overlap = buyer_wallets.intersection(sig.smart_wallets_whitelist)
            if overlap:
                score += 10.0
                reasons.append(f"Smart whale overlap ({len(overlap)} wallets)")

        matches = score >= 70.0
        reason_str = " | ".join(reasons)
        return matches, score, reason_str

    def get_smart_wallets(self) -> List[str]:
        """Returns all collected smart wallets from 5x-7500x runners."""
        return list(self._smart_early_wallets)

    def get_summary_report(self) -> str:
        """Render a formatted Telegram report of harvested missed runners."""
        lines = [
            "🏆 <b>MISSED RUNNER PATTERN HARVESTER</b> 🏆\n",
            f"• <b>Harvested Pumped Tokens:</b> <code>{len(self._pumped_tokens)}</code>",
            f"• <b>Smart Early Wallets:</b> <code>{len(self._smart_early_wallets)}</code>",
        ]
        if self._pattern_signature:
            sig = self._pattern_signature
            lines.extend([
                f"• <b>Optimal Entry MCAP:</b> <code>${sig.min_mcap_usd:,.0f} - ${sig.max_mcap_usd:,.0f}</code>",
                f"• <b>Min Buy Pressure:</b> <code>{sig.min_buy_volume_ratio*100:.0f}%</code>",
                f"• <b>Min Unique Buyers:</b> <code>{sig.min_unique_buyers}</code>\n",
            ])

        lines.append("<b>Top Pumped Tokens Ingested:</b>")
        for r in list(self._pumped_tokens.values())[:5]:
            lines.append(f"• <b>{r.symbol}</b>: <code>{r.multiplier:.1f}x</code> (${r.alert_mcap:,.0f} ➔ ${r.peak_mcap:,.0f})")

        return "\n".join(lines)
