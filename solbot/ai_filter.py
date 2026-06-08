import os
import aiohttp
import json
import logging
from typing import Optional, Dict

logger = logging.getLogger("bot.ai_filter")

class AIFilter:
    """AI-powered token safety filter using MiniMax M3."""

    def __init__(self, api_key: Optional[str] = None):
        self._api_key = api_key or os.getenv("MINIMAX_API_KEY")
        self._base_url = "https://api.minimax.io/v1/chat/completions"
        self._model = "minimax-m3"

    async def score_token(self, token_data: Dict) -> int:
        """
        Score a token (0-100) based on metadata and sentiment.
        Higher is safer.
        """
        if not self._api_key:
            logger.warning("MiniMax API key missing, skipping AI filter.")
            return 100

        prompt = f\"\"\"
        Analyze this token for safety (rugpull risk, scam potential).
        Provide a safety score from 0 to 100 where 100 is perfectly safe.
        
        Token Metadata:
        - Mint: {token_data.get('mint')}
        - Symbol: {token_data.get('symbol')}
        - Name: {token_data.get('name')}
        - Creator: {token_data.get('creator')}
        - Description: {token_data.get('description', 'N/A')}
        
        Recent Context/Sentiment:
        {token_data.get('sentiment_text', 'No recent tweets or context provided.')}
        
        Respond ONLY with the numeric score.
        \"\"\"

        payload = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json"
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(self._base_url, json=payload, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        content = data['choices'][0]['message']['content'].strip()
                        # Extract first number found
                        import re
                        match = re.search(r'\\d+', content)
                        if match:
                            score = int(match.group())
                            return max(0, min(100, score))
                    else:
                        logger.error(f"MiniMax API error: {resp.status} - {await resp.text()}")
        except Exception as e:
            logger.error(f"AI scoring failed: {e}")
        
        return 50  # Default to neutral on error
