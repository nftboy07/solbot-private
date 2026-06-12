import asyncio
import logging
from typing import Dict, Set, List

class KOLTracker:
    def __init__(self, telegram_client=None):
        self.wallets = {}  # {address: name}
        self.active_buys = {}  # {token_address: set(kol_addresses)}
        # Track holding value in SOL equivalent: {token_address: {kol_address: peak_sol_balance}}
        self.kol_holdings = {} 
        # Track number of KOLs who reduced holdings for a token
        self.kol_reductions = {}  # {token_address: set(kol_addresses)}
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
        amount = float(event.get('amount') or 0.0)

        if wallet not in self.wallets:
            return

        kol_name = self.wallets[wallet]

        if action == 'buy':
            if token not in self.active_buys:
                self.active_buys[token] = set()
            self.active_buys[token].add(wallet)
            
            if token not in self.kol_holdings:
                self.kol_holdings[token] = {}
            self.kol_holdings[token][wallet] = self.kol_holdings[token].get(wallet, 0.0) + amount
            
            kol_count = len(self.active_buys[token])
            self.logger.info(f"KOL {kol_name} bought {token} with {amount} SOL. Peak holdings: {self.kol_holdings[token][wallet]} SOL. Total KOLs: {kol_count}")

            # Trigger when threshold reached
            if kol_count >= self.threshold:
                self.logger.warning(f"COORDINATED KOL BUY DETECTED: {kol_count} KOLs in {token}. Executing copy-trade.")
                await bot_instance.execute_kol_snipe(token, f"{kol_count} KOLs Coordinated Buy")

        elif action in ['sell', 'transfer']:
            if token in self.active_buys and wallet in self.active_buys[token]:
                peak_bal = self.kol_holdings.get(token, {}).get(wallet, 0.0)
                
                # Check exit triggers:
                # 1. KOL sells 20% or more of their peak balance
                is_20_percent_dump = (peak_bal > 0 and amount >= 0.20 * peak_bal)
                
                # 2. Track multiple KOL reductions
                if token not in self.kol_reductions:
                    self.kol_reductions[token] = set()
                self.kol_reductions[token].add(wallet)
                is_multiple_reductions = (len(self.kol_reductions[token]) >= 2)
                
                # Trigger immediate exit if either is true, or if it is a general sell/transfer (safety first)
                exit_triggered = is_20_percent_dump or is_multiple_reductions or (amount > 0.0)
                
                if exit_triggered:
                    reason_msg = f"KOL EXIT ({kol_name} sold {amount:.2f} SOL, peak was {peak_bal:.2f} SOL)"
                    if is_multiple_reductions:
                        reason_msg = f"KOL EXIT (Multiple KOLs reducing: {len(self.kol_reductions[token])} KOLs)"
                        
                    self.logger.warning(f"EXIT SIGNAL: {reason_msg}. Liquidating position immediately.")
                    
                    if token in bot_instance._positions:
                        pos = bot_instance._positions[token]
                        await bot_instance._exit_position(pos, reason_msg, 1.0)
                        
                    # Cleanup tracking
                    self.active_buys[token].discard(wallet)
                    if token in self.kol_holdings and wallet in self.kol_holdings[token]:
                        del self.kol_holdings[token][wallet]
                    if not self.active_buys[token]:
                        if token in self.active_buys: del self.active_buys[token]
                        if token in self.kol_holdings: del self.kol_holdings[token]
                        if token in self.kol_reductions: del self.kol_reductions[token]
                else:
                    # Update holding balance
                    if token in self.kol_holdings and wallet in self.kol_holdings[token]:
                        self.kol_holdings[token][wallet] = max(0.0, self.kol_holdings[token][wallet] - amount)

    async def get_token_sentiment(self, token: str) -> float:
        """Returns a score based on how many KOLs are currently holding."""
        kols = self.active_buys.get(token, set())
        return len(kols) / 20.0  # Normalized score
