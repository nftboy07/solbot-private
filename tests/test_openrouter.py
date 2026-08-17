import unittest
from unittest.mock import patch, MagicMock, AsyncMock
from solbot.config import BotConfig
from solbot.ai_filter import AIFilter


class TestOpenRouterIntegration(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.config_data = {
            "OPENROUTER_API_KEY": "test_openrouter_key",
            "OPENROUTER_API_URL": "https://test.openrouter.ai/api/v1/chat/completions",
            "OPENROUTER_MODEL": "meta-llama/llama-3-8b-instruct:free"
        }
        self.patchers = []
        for k, v in self.config_data.items():
            p = patch.dict("os.environ", {k: v})
            p.start()
            self.patchers.append(p)
        self.config = BotConfig()
        self.filter = AIFilter(self.config)

    def tearDown(self):
        for p in self.patchers:
            p.stop()

    async def test_score_token_openrouter_success(self):
        # Mock a successful JSON response from the primary model
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={
            "choices": [{
                "message": {
                    "content": "85"
                }
            }]
        })

        class MockPostContext:
            async def __aenter__(self):
                return mock_response
            async def __aexit__(self, exc_type, exc, tb):
                pass

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=MockPostContext())

        class MockSessionContext:
            async def __aenter__(self):
                return mock_session
            async def __aexit__(self, exc_type, exc, tb):
                pass

        with patch("aiohttp.ClientSession", return_value=MockSessionContext()):
            score = await self.filter.score_token({"mint": "testmint", "symbol": "TEST", "name": "TestToken"})
            self.assertEqual(score, 85)
            mock_session.post.assert_called_once()
            _, kwargs = mock_session.post.call_args
            self.assertEqual(kwargs["json"]["model"], "meta-llama/llama-3-8b-instruct:free")

    async def test_score_token_openrouter_rate_limit_fallback(self):
        # Mock rate limit (429) for the primary model, then success (200) for the second model
        mock_response_1 = MagicMock()
        mock_response_1.status = 429
        mock_response_1.text = AsyncMock(return_value="Rate limit exceeded")

        mock_response_2 = MagicMock()
        mock_response_2.status = 200
        mock_response_2.json = AsyncMock(return_value={
            "choices": [{
                "message": {
                    "content": "92"
                }
            }]
        })

        responses = [mock_response_1, mock_response_2]
        call_count = 0

        class MockPostContext:
            async def __aenter__(self):
                nonlocal call_count
                res = responses[call_count]
                call_count += 1
                return res
            async def __aexit__(self, exc_type, exc, tb):
                pass

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=MockPostContext())

        class MockSessionContext:
            async def __aenter__(self):
                return mock_session
            async def __aexit__(self, exc_type, exc, tb):
                pass

        with patch("aiohttp.ClientSession", return_value=MockSessionContext()):
            score = await self.filter.score_token({"mint": "testmint", "symbol": "TEST", "name": "TestToken"})
            self.assertEqual(score, 92)
            self.assertEqual(mock_session.post.call_count, 2)
            
            # Verify the second call used the first fallback model in the pool
            args_list = mock_session.post.call_args_list
            model_1 = args_list[0][1]["json"]["model"]
            model_2 = args_list[1][1]["json"]["model"]
            self.assertEqual(model_1, "meta-llama/llama-3-8b-instruct:free")
            self.assertEqual(model_2, "meta-llama/llama-3.1-8b-instruct:free")

    async def test_detect_rug_risks_openrouter_success(self):
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={
            "choices": [{
                "message": {
                    "content": '{"score": 95, "is_premine": false, "is_honeypot": false, "reason": "Looks safe."}'
                }
            }]
        })

        class MockPostContext:
            async def __aenter__(self):
                return mock_response
            async def __aexit__(self, exc_type, exc, tb):
                pass

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=MockPostContext())

        class MockSessionContext:
            async def __aenter__(self):
                return mock_session
            async def __aexit__(self, exc_type, exc, tb):
                pass

        with patch("aiohttp.ClientSession", return_value=MockSessionContext()):
            result = await self.filter.detect_rug_risks(
                token_mint="testmint",
                creator="devwallet",
                holders=[],
                creator_history=[]
            )
            self.assertEqual(result["score"], 95)
            self.assertFalse(result["is_premine"])
            self.assertFalse(result["is_honeypot"])
            self.assertEqual(result["reason"], "Looks safe.")
            self.assertEqual(result["provider"], "openrouter")


if __name__ == "__main__":
    unittest.main()
