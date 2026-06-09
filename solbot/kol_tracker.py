import asyncio
import logging
from typing import Dict, Set, List

class KOLTracker:
    def __init__(self, telegram_client=None):
        self.wallets = {}  # {address: name}
        self.active_buys = {}  # {token_address: set(kol_addresses)}
        self.telegram_client = telegram_client
        self.logger = logging.getLogger("KOLTracker")
        self.threshold = 2

    def add_wallet(self, address: str, name: str):
        self.wallets[address] = name
        self.logger.info(f"Added KOL wallet for tracking: {name} ({address})")

    async def process_event(self, event: dict, bot_instance):
        """
        Processes a wallet event. 
        event shape: {'wallet': str, 'action': 'buy'|'sell'|'transfer', 'token': str, 'amount': float}
        """
        wallet = event['wallet']
        action = event['action']
        token = event['token']

        if wallet not in self.wallets:
            return

        kol_name = self.wallets[wallet]

        if action == 'buy':
            if token not in self.active_buys:
                self.active_buys[token] = set()
            
            self.active_buys[token].add(wallet)
            kol_count = len(self.active_buys[token])
            
            self.logger.info(f"KOL {kol_name} bought {token}. Total KOLs in this token: {kol_count}")

            # Trigger when threshold reached
            if kol_count >= self.threshold:
                self.logger.warning(f"COORDINATED KOL BUY DETECTED: {kol_count} KOLs in {token}. Executing copy-trade.")
                await bot_instance.execute_kol_snipe(token, f"{kol_count} KOLs Coordinated Buy")

        elif action in ['sell', 'transfer']:
            # ULTRA-AGGRESSIVE EXIT: If ANY KOL who bought this token sells/transfers, liquidate.
            if token in self.active_buys and wallet in self.active_buys[token]:
                self.logger.warning(f"EXIT SIGNAL: KOL {kol_name} is leaving {token}. Liquidating position immediately (Frontrunning KOLs).")
                # Trigger emergency exit in the bot
                if token in bot_instance._positions:
                    pos = bot_instance._positions[token]
                    await bot_instance._exit_position(pos, f"KOL EXIT ({kol_name})", 1.0)
                
                # Cleanup tracking for this token after liquidation
                self.active_buys[token].discard(wallet)
                if not self.active_buys[token]:
                    del self.active_buys[token]

    async def get_token_sentiment(self, token: str) -> float:
        """Returns a score based on how many KOLs are currently holding."""
        kols = self.active_buys.get(token, set())
        return len(kols) / 20.0  # Normalized score
