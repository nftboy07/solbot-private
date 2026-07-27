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

    def __init__(self, config: SolanaConfig, allow_ephemeral: bool = False):
        if allow_ephemeral and not config.private_key:
            # Paper trading: generate a throwaway keypair so the real private key
            # never has to be present, let alone loaded into the process.
            self._keypair = Keypair()
            logger.warning(f"Ephemeral paper-trading wallet: {self.pubkey}")
            return
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
