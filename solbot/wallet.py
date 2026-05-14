"""Wallet management for Solana transactions."""

import base58
from solders.keypair import Keypair
from solders.pubkey import Pubkey

from solbot.config import SolanaConfig
from solbot.logger import get_logger

logger = get_logger("wallet")

# Native SOL mint
SOL_MINT = "So11111111111111111111111111111111111111112"


class Wallet:
    """Manages wallet keypair and signing."""

    def __init__(self, config: SolanaConfig):
        self._keypair = self._load_keypair(config.private_key)
        logger.info(f"Wallet loaded: {self.pubkey}")

    @staticmethod
    def _load_keypair(private_key: str) -> Keypair:
        """Load keypair from base58-encoded private key."""
        try:
            secret = base58.b58decode(private_key)
            return Keypair.from_bytes(secret)
        except Exception as e:
            raise ValueError(f"Invalid private key: {e}") from e

    @property
    def keypair(self) -> Keypair:
        return self._keypair

    @property
    def pubkey(self) -> Pubkey:
        return self._keypair.pubkey()

    @property
    def pubkey_str(self) -> str:
        return str(self._keypair.pubkey())
