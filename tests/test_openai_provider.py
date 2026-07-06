import asyncio
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.modules.setdefault("aiohttp", types.SimpleNamespace(ClientSession=object))
sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda: None))

from solbot.ai_filter import AIFilter
from solbot.config import BotConfig


class OpenAIProviderTests(unittest.TestCase):
    def test_openai_key_can_load_from_file_env(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            key_file = Path(temp_dir) / "openai-api-key.txt"
            key_file.write_text("sk-test-local-file-key\n", encoding="utf-8")

            with patch.dict(
                "os.environ",
                {
                    "OPENAI_API_KEY": "",
                    "OPENAI_API_KEY_FILE": str(key_file),
                },
                clear=False,
            ):
                config = BotConfig()

        self.assertEqual(config.ai.openai_api_key, "sk-test-local-file-key")

    def test_openai_score_parser_uses_responses_output_text(self):
        filter_obj = AIFilter(BotConfig())

        async def fake_call(prompt, max_output_tokens):
            return "82"

        filter_obj._openai_api_key = "sk-test"
        filter_obj._call_openai_response = fake_call

        score = asyncio.run(filter_obj._score_with_openai("score prompt"))

        self.assertEqual(score, 82)

    def test_openai_safety_parser_normalizes_json(self):
        filter_obj = AIFilter(BotConfig())

        async def fake_call(prompt, max_output_tokens):
            return '{"score": 91, "is_premine": false, "is_honeypot": false, "reason": "Healthy distribution."}'

        filter_obj._openai_api_key = "sk-test"
        filter_obj._call_openai_response = fake_call

        analysis = asyncio.run(filter_obj._analyze_safety_with_openai("json prompt"))

        self.assertEqual(analysis["score"], 91)
        self.assertFalse(analysis["is_premine"])
        self.assertFalse(analysis["is_honeypot"])
        self.assertEqual(analysis["scan_status"], "ok")
        self.assertEqual(analysis["provider"], "openai")


if __name__ == "__main__":
    unittest.main()
