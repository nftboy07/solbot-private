import sys
import types
import unittest

sys.modules.setdefault("aiohttp", types.SimpleNamespace(ClientSession=object))
sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda: None))

from solbot.arbitrage_engine import (
    ArbitrageOpportunity,
    DEXArbitrageEngine,
    calculate_net_profit_sol,
)


class FakeArbitrageEngine(DEXArbitrageEngine):
    async def _get_two_leg_opportunity(self, mint, buy_dex, sell_dex, input_lamports):
        if buy_dex == "Raydium" and sell_dex == "Meteora":
            return ArbitrageOpportunity(
                mint=mint,
                buy_dex=buy_dex,
                sell_dex=sell_dex,
                input_sol=self._config.input_sol,
                output_sol=1.034,
                estimated_fees_sol=self._config.estimated_fees_sol,
                jito_tip_sol=self._config.jito_tip_sol,
                net_profit_sol=0.030,
                buy_quote={"outAmount": "100"},
                sell_quote={"outAmount": "1034000000"},
            )
        return None


class ArbitrageEngineTests(unittest.IsolatedAsyncioTestCase):
    def test_net_profit_calculation_includes_fees_and_jito_tip(self):
        profit = calculate_net_profit_sol(
            input_sol=1.0,
            output_sol=1.05,
            estimated_fees_sol=0.004,
            jito_tip_sol=0.001,
        )

        self.assertAlmostEqual(profit, 0.045)

    async def test_scan_once_filters_profitable_distinct_dex_route(self):
        config = types.SimpleNamespace(
            enabled=True,
            dry_run=True,
            watch_mints=[],
            route_dexes=["Raydium", "Meteora", "Orca"],
            input_sol=1.0,
            min_profit_sol=0.02,
            estimated_fees_sol=0.003,
            jito_tip_sol=0.001,
            scan_interval_seconds=15,
            slippage_bps=100,
            quote_timeout_seconds=6,
            log_file="arbitrage.log",
        )
        bot = types.SimpleNamespace(
            _config=types.SimpleNamespace(arbitrage=config),
            _positions={},
            _daily_runners={},
        )
        engine = FakeArbitrageEngine(bot, config)

        opportunities = await engine.scan_once(["mint"])

        self.assertEqual(len(opportunities), 1)
        self.assertEqual(opportunities[0].buy_dex, "Raydium")
        self.assertEqual(opportunities[0].sell_dex, "Meteora")
        self.assertGreaterEqual(opportunities[0].net_profit_sol, config.min_profit_sol)

    def test_bundle_plan_contains_buy_sell_and_tip_transactions(self):
        bundle = DEXArbitrageEngine._bundle_transactions("buy-tx", "sell-tx", "tip-tx")

        self.assertEqual(bundle, ["buy-tx", "sell-tx", "tip-tx"])


if __name__ == "__main__":
    unittest.main()
