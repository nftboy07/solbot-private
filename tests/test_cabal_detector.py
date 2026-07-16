import sys
import types
import unittest

sys.modules.setdefault("aiohttp", types.SimpleNamespace(ClientSession=object))
sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda: None))

from solbot.cabal_detector import CabalDetector, HolderFunding, _extract_funding_source


class CabalDetectorTests(unittest.TestCase):
    def test_shared_funding_root_above_threshold_blocks(self):
        bot = types.SimpleNamespace(_config=types.SimpleNamespace())
        config = types.SimpleNamespace(
            enabled=True,
            max_cluster_supply_pct=30.0,
            cache_ttl_seconds=180,
            top_holders_limit=20,
            max_trace_hops=3,
            rpc_timeout_seconds=8,
        )
        detector = CabalDetector(bot, config)
        holders = [
            HolderFunding("acct1", "wallet1", 12.0),
            HolderFunding("acct2", "wallet2", 11.5),
            HolderFunding("acct3", "wallet3", 9.0),
            HolderFunding("acct4", "wallet4", 4.0),
        ]

        report = detector._build_report(
            "mint",
            holders,
            ["creator-root", "creator-root", "creator-root", "independent"],
            creator="creator-root",
        )

        self.assertTrue(report.blocked)
        self.assertEqual(report.cluster_size, 3)
        self.assertAlmostEqual(report.largest_cluster_pct, 32.5)
        self.assertIn("CABAL CLUSTER DETECTED", report.reason)

    def test_shared_funding_root_below_threshold_passes(self):
        bot = types.SimpleNamespace(_config=types.SimpleNamespace())
        config = types.SimpleNamespace(
            enabled=True,
            max_cluster_supply_pct=30.0,
            cache_ttl_seconds=180,
            top_holders_limit=20,
            max_trace_hops=3,
            rpc_timeout_seconds=8,
        )
        detector = CabalDetector(bot, config)

        report = detector._build_report(
            "mint",
            [
                HolderFunding("acct1", "wallet1", 8.0),
                HolderFunding("acct2", "wallet2", 7.5),
            ],
            ["root", "root"],
        )

        self.assertFalse(report.blocked)
        self.assertEqual(report.cluster_size, 2)
        self.assertAlmostEqual(report.largest_cluster_pct, 15.5)

    def test_extracts_system_transfer_funding_source(self):
        tx = {
            "transaction": {
                "message": {
                    "instructions": [
                        {
                            "programId": "11111111111111111111111111111111",
                            "parsed": {
                                "type": "transfer",
                                "info": {
                                    "source": "parent-wallet",
                                    "destination": "child-wallet",
                                },
                            },
                        }
                    ]
                }
            }
        }

        self.assertEqual(_extract_funding_source(tx, "child-wallet"), "parent-wallet")


if __name__ == "__main__":
    unittest.main()
