"""Guards for the trade path fixes: slippage units, orphaned bags, fail-closed AI."""

import asyncio
import sys
import types
import unittest
from unittest.mock import patch

sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda: None))

from solbot.bot import Position, Solbot
from solbot.config import BotConfig
from solbot.pumpfun_client import PumpFunClient


class _Resp:
    """Non-200 response so execute_trade returns before parsing a transaction."""

    status = 500

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def read(self):
        return b""


class _Session:
    def __init__(self):
        self.payload = None

    def post(self, url, json=None):
        self.payload = json
        return _Resp()


class SlippageUnitTests(unittest.TestCase):
    """PumpPortal's `slippage` field is a percent; config carries basis points."""

    def _payload_for(self, bps):
        with patch.dict("os.environ", {"SLIPPAGE_BPS": str(bps)}, clear=False):
            config = BotConfig()
        wallet = types.SimpleNamespace(pubkey_str="So11111111111111111111111111111111111111112")
        client = PumpFunClient(config, wallet)
        session = _Session()
        client._session = session

        async def _no_fee(_mint):
            return 0.0

        client.get_recent_prioritization_fee = _no_fee
        asyncio.run(client.execute_trade("MintAddress", "buy", amount=0.01))
        return session.payload

    def test_300_bps_is_sent_as_3_percent(self):
        payload = self._payload_for(300)
        self.assertEqual(payload["slippage"], 3.0)

    def test_slippage_is_never_sent_as_raw_basis_points(self):
        # The bug: 300 bps sent verbatim asks for 300% tolerance, i.e. no price
        # protection at all, on entries and exits alike.
        for bps in (100, 300, 1500):
            payload = self._payload_for(bps)
            self.assertLess(payload["slippage"], 100)
            self.assertAlmostEqual(payload["slippage"], bps / 100.0)


class OrphanedPositionRestoreTests(unittest.TestCase):
    """A bag open in the DB but missing from state.json must come back."""

    def setUp(self):
        self.bot = Solbot.__new__(Solbot)
        self.bot._positions = {}
        self.bot._state_file = "data/state.json"

    def test_open_db_row_missing_from_state_is_restored(self):
        statuses = {"MintA": "open"}
        rows = {"MintA": {"entry_price": 4764.3, "size": 0.019, "timestamp": 1783329323}}

        restored = self.bot._restore_orphaned_open_positions(statuses, rows)

        self.assertEqual(restored, 1)
        self.assertIn("MintA", self.bot._positions)
        pos = self.bot._positions["MintA"]
        self.assertTrue(pos.active)
        self.assertEqual(pos.size, 0.019)
        self.assertEqual(pos.entry_price, 4764.3)

    def test_closed_rows_and_already_tracked_bags_are_left_alone(self):
        tracked = Position(
            mint="MintB", symbol="B", entry_price=1.0, entry_liq=0.0, creator="", size=5.0
        )
        self.bot._positions["MintB"] = tracked
        statuses = {"MintB": "open", "MintC": "closed"}
        rows = {
            "MintB": {"entry_price": 9.9, "size": 9.9, "timestamp": 1},
            "MintC": {"entry_price": 1.0, "size": 1.0, "timestamp": 1},
        }

        restored = self.bot._restore_orphaned_open_positions(statuses, rows)

        self.assertEqual(restored, 0)
        self.assertNotIn("MintC", self.bot._positions)
        self.assertIs(self.bot._positions["MintB"], tracked)
        self.assertEqual(self.bot._positions["MintB"].size, 5.0)

    def test_a_bad_row_does_not_abort_the_rest(self):
        statuses = {"Bad": "open", "Good": "open"}
        rows = {
            "Bad": {"entry_price": "not-a-number", "size": 1.0, "timestamp": 1},
            "Good": {"entry_price": 2.0, "size": 1.0, "timestamp": 1},
        }

        restored = self.bot._restore_orphaned_open_positions(statuses, rows)

        self.assertEqual(restored, 1)
        self.assertIn("Good", self.bot._positions)


class AIFilterFailClosedTests(unittest.TestCase):
    """An outage must not wave every launch through."""

    def test_exhausted_providers_return_a_rejecting_score(self):
        from solbot.ai_filter import AIFilter

        with patch.dict(
            "os.environ",
            {"OPENAI_API_KEY": "", "OPENAI_API_KEY_FILE": "", "GEMINI_API_KEY": "",
             "NVIDIA_API_KEY": "", "OPENROUTER_API_KEY": "", "AI_FAIL_OPEN_SCORE": "0"},
            clear=False,
        ):
            config = BotConfig()
        ai = AIFilter(config)

        async def _no_bedrock(_prompt):
            return None

        ai._score_with_bedrock = _no_bedrock
        score = asyncio.run(ai.score_token({"mint": "M", "symbol": "S", "name": "N", "creator": "C"}))

        self.assertEqual(score, 0)
        self.assertLess(score, 60, "must sit below the min_confidence_score gate")


class PaperTradingLifecycleTests(unittest.TestCase):
    """DRY_RUN must run a whole position lifecycle without touching the network."""

    def _client(self, start_sol="1.0"):
        with patch.dict(
            "os.environ", {"DRY_RUN": "true", "DRY_RUN_START_SOL": start_sol}, clear=False
        ):
            config = BotConfig()
        wallet = types.SimpleNamespace(pubkey_str="So11111111111111111111111111111111111111112")
        client = PumpFunClient(config, wallet)
        # Left as None deliberately: any real HTTP attempt raises instead of
        # silently reaching the network from a test.
        client._session = None
        return client

    def test_dry_run_is_off_unless_asked_for(self):
        with patch.dict("os.environ", {"DRY_RUN": ""}, clear=False):
            config = BotConfig()
        self.assertFalse(config.strategy.dry_run)

    def test_buy_take_profit_moonbag_and_final_exit(self):
        client = self._client()
        mint = "MoonMint"

        buy = asyncio.run(client.execute_trade(mint, "buy", amount=0.02))
        self.assertTrue(buy.success)
        self.assertTrue(buy.tx_signature.startswith("DRYRUN-buy"))
        self.assertAlmostEqual(client._paper_sol, 0.98)
        self.assertGreater(asyncio.run(client.get_token_balance(mint)), 0)

        # The bag triples; the ladder sells half.
        client.set_paper_mark(mint, 3.0)
        held = asyncio.run(client.get_token_balance(mint))
        tp = asyncio.run(
            client.execute_trade(mint, "sell", amount=held * 0.5, denominated_in_sol=False)
        )
        self.assertTrue(tp.success)
        self.assertAlmostEqual(tp.amount_out, 0.03)          # 0.01 basis at 3x
        self.assertAlmostEqual(client._paper_sol, 1.01)

        # Moonbag rides to 5x, then a trailing stop closes it.
        client.set_paper_mark(mint, 5.0)
        rest = asyncio.run(client.get_token_balance(mint))
        exit_fill = asyncio.run(
            client.execute_trade(mint, "sell", amount=rest, denominated_in_sol=False)
        )
        self.assertTrue(exit_fill.success)
        self.assertAlmostEqual(exit_fill.amount_out, 0.05)   # 0.01 basis at 5x
        self.assertAlmostEqual(client._paper_sol, 1.06)

        # Position fully closed and the run is up on the round trip.
        self.assertEqual(asyncio.run(client.get_token_balance(mint)), 0.0)
        self.assertEqual(asyncio.run(client.get_all_token_balances()), {})
        self.assertGreater(client.paper_summary()["equity_sol"], 1.0)

    def test_a_rug_loses_money_rather_than_quietly_breaking_even(self):
        client = self._client()
        mint = "RugMint"
        asyncio.run(client.execute_trade(mint, "buy", amount=0.05))
        client.set_paper_mark(mint, 0.1)  # down 90%
        held = asyncio.run(client.get_token_balance(mint))
        asyncio.run(client.execute_trade(mint, "sell", amount=held, denominated_in_sol=False))
        self.assertAlmostEqual(client._paper_sol, 0.955)
        self.assertLess(client.paper_summary()["equity_sol"], 1.0)

    def test_paper_wallet_cannot_overspend(self):
        client = self._client(start_sol="0.01")
        result = asyncio.run(client.execute_trade("BigMint", "buy", amount=5.0))
        self.assertFalse(result.success)
        self.assertIn("Paper wallet short", result.error)
        self.assertAlmostEqual(client._paper_sol, 0.01)

    def test_paper_mode_needs_no_private_key_but_live_mode_still_does(self):
        with patch.dict("os.environ", {"WALLET_PRIVATE_KEY": "", "DRY_RUN": "true"}, clear=False):
            self.assertEqual(BotConfig().validate(), [])
        with patch.dict("os.environ", {"WALLET_PRIVATE_KEY": "", "DRY_RUN": "false"}, clear=False):
            self.assertIn("WALLET_PRIVATE_KEY is required", BotConfig().validate())

    def test_open_paper_bags_survive_the_startup_reconcile(self):
        client = self._client()
        asyncio.run(client.execute_trade("KeepMint", "buy", amount=0.02))
        balances = asyncio.run(client.get_all_token_balances())
        self.assertIn("KeepMint", balances)
        self.assertGreater(balances["KeepMint"]["balance"], 0)
        # Must not look like Token-2022, which the reconcile purges as unsellable.
        self.assertNotEqual(balances["KeepMint"]["program"], "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb")


if __name__ == "__main__":
    unittest.main()
