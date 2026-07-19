import unittest
from unittest.mock import patch, mock_open, MagicMock, AsyncMock
from solbot.paste_trade import PasteTradeClient


class TestPasteTradeClient(unittest.IsolatedAsyncioTestCase):
    async def test_ensure_key_existing(self):
        client = PasteTradeClient(key="existing_key")
        key = await client.ensure_key()
        self.assertEqual(key, "existing_key")
        await client.close()

    async def test_ensure_key_provisioning(self):
        client = PasteTradeClient(key="", url="https://test.paste.trade")

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={"api_key": "new_api_key_123", "handle": "CalmHeron"})

        class MockPostContext:
            async def __aenter__(self):
                return mock_response
            async def __aexit__(self, exc_type, exc, tb):
                pass

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=MockPostContext())

        with patch.object(client, "get_session", return_value=mock_session):
            with patch("os.path.exists", return_value=True):
                with patch("builtins.open", mock_open(read_data="")) as mock_file:
                    key = await client.ensure_key()
                    self.assertEqual(key, "new_api_key_123")
                    self.assertEqual(client.key, "new_api_key_123")
                    # Check that it appended to .env file
                    mock_file().write.assert_called_once_with("\nPASTE_TRADE_KEY=new_api_key_123\n")

        await client.close()

    async def test_post_trade_success(self):
        client = PasteTradeClient(key="my_secret_key", url="https://test.paste.trade", handle="@mybot")

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.text = AsyncMock(return_value='{"id": "trade123"}')
        mock_response.json = AsyncMock(return_value={"id": "trade123", "warnings": ["low liquidity"]})

        class MockPostContext:
            async def __aenter__(self):
                return mock_response
            async def __aexit__(self, exc_type, exc, tb):
                pass

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=MockPostContext())

        with patch.object(client, "get_session", return_value=mock_session):
            success = await client.post_trade(
                ticker="SOL",
                direction="long",
                author_price=150.5,
                thesis="Snipe buy triggered"
            )
            self.assertTrue(success)

            # Check payload fields passed to session.post
            mock_session.post.assert_called_once()
            args, kwargs = mock_session.post.call_args
            url = args[0]
            self.assertEqual(url, "https://test.paste.trade/api/trades")
            payload = kwargs["json"]
            self.assertEqual(payload["ticker"], "SOL")
            self.assertEqual(payload["direction"], "long")
            self.assertEqual(payload["author_price"], 150.5)
            self.assertEqual(payload["thesis"], "Snipe buy triggered")
            self.assertEqual(payload["author_handle"], "@mybot")
            self.assertEqual(payload["platform"], "solana")
            self.assertEqual(payload["instrument"], "spot")

        await client.close()

    async def test_post_trade_failure(self):
        client = PasteTradeClient(key="my_secret_key", url="https://test.paste.trade")

        mock_response = MagicMock()
        mock_response.status = 400
        mock_response.text = AsyncMock(return_value="Bad Request")

        class MockPostContext:
            async def __aenter__(self):
                return mock_response
            async def __aexit__(self, exc_type, exc, tb):
                pass

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=MockPostContext())

        with patch.object(client, "get_session", return_value=mock_session):
            success = await client.post_trade(
                ticker="SOL",
                direction="long",
                author_price=150.5,
                thesis="Snipe buy triggered"
            )
            self.assertFalse(success)

        await client.close()


if __name__ == "__main__":
    unittest.main()
