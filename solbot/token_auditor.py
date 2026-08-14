"""Automated Token Website, Social Velocity, and Metadata Auditor."""

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Dict, Optional, Any

import aiohttp

logger = logging.getLogger("bot.token_auditor")


@dataclass
class TokenAuditResult:
    """Audit breakdown for token website and external footprint."""
    mint: str
    has_website: bool
    has_twitter: bool
    has_telegram: bool
    website_status_code: Optional[int]
    ssl_valid: bool
    copycat_risk: bool
    audit_score: int
    flags: list


class TokenAuditor:
    """Audits external token footprints and social credibility."""

    FAMOUS_BRANDS = [
        "BITCOIN", "ETHEREUM", "SOLANA", "BINANCE", "COINBASE", "TETHER", "USDC",
        "JUPITER", "RAYDIUM", "PUMP", "METAMASK", "PHANTOM", "OPENAI", "DEEPMIND",
    ]

    def __init__(self, timeout_seconds: float = 4.0):
        self._timeout = timeout_seconds

    async def audit_token(self, mint: str, metadata: Dict[str, Any]) -> TokenAuditResult:
        """Analyze website availability, social URLs, and brand impersonations."""
        flags = []
        name = str(metadata.get("name", "")).upper()
        symbol = str(metadata.get("symbol", "")).upper()
        uri = str(metadata.get("uri", ""))
        website_url = str(metadata.get("website", "") or metadata.get("twitter", ""))

        has_website = bool(website_url and website_url.startswith("http"))
        has_twitter = "twitter.com" in website_url or "x.com" in website_url or "twitter" in metadata
        has_telegram = "t.me" in website_url or "telegram" in metadata

        # 1. Brand Impersonation / Copycat check
        copycat_risk = False
        scam_suffixes = ["REWARD", "AIRDROP", "CLAIM", "OFFICIAL", "TEST", "FREE", "2.0", "GIFT", "WINNER"]
        for brand in self.FAMOUS_BRANDS:
            has_brand_symbol = brand in symbol and symbol != brand
            has_brand_name = (brand in name and any(kw in name for kw in scam_suffixes)) or (brand in name and not name.startswith(brand))
            if has_brand_symbol or has_brand_name:
                flags.append(f"Potential trademark copycat: {brand}")
                copycat_risk = True

        # 2. Check website responsiveness if present
        website_status = None
        ssl_valid = False
        if has_website and not ("twitter.com" in website_url or "x.com" in website_url):
            try:
                timeout = aiohttp.ClientTimeout(total=self._timeout)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(website_url, allow_redirects=True) as resp:
                        website_status = resp.status
                        ssl_valid = website_url.startswith("https")
                        if resp.status != 200:
                            flags.append(f"Website unreachable (HTTP {resp.status})")
            except Exception:
                flags.append("Website connection failed")

        # Score computation (0-100)
        score = 80
        if has_website and website_status == 200:
            score += 10
        if has_twitter or has_telegram:
            score += 10
        if copycat_risk:
            score -= 40
        if len(flags) > 2:
            score -= 20

        score = max(0, min(100, score))

        return TokenAuditResult(
            mint=mint,
            has_website=has_website,
            has_twitter=has_twitter,
            has_telegram=has_telegram,
            website_status_code=website_status,
            ssl_valid=ssl_valid,
            copycat_risk=copycat_risk,
            audit_score=score,
            flags=flags,
        )
