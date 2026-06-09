"""AI-powered token safety filter using BluesMinds AI or MiniMax."""

import aiohttp
import json
import logging
import os
from typing import Dict, Optional

logger = logging.getLogger("bot.ai_filter")

class AIFilter:
    """AI-powered token safety filter using BluesMinds (OpenAI-compatible) or MiniMax."""

    def __init__(self, api_key: Optional[str] = None):
        # Prefer BluesMinds for $100 free credits, fallback to MiniMax
        self._api_key = os.getenv("BLUESMINDS_API_KEY") or api_key or os.getenv("MINIMAX_API_KEY")
        
        if os.getenv("BLUESMINDS_API_KEY"):
            self._base_url = "https://api.bluesminds.com/v1/chat/completions"
            self._model = "gpt-4-turbo" # Or any available BluesMinds model
            logger.info("Using BluesMinds AI Platform.")
        else:
            self._base_url = "https://api.minimax.io/v1/chat/completions"
            self._model = "minimax-m3"
            logger.info("Using MiniMax AI (Fallback).")

    async def score_token(self, token_data: Dict) -> int:
        """
        Score a token (0-100) based on metadata and sentiment.
        Higher score = Safer.
        """
        if not self._api_key:
            logger.warning("AI API key missing, skipping AI filter.")
            return 100

        # Enhanced prompt for better "non-degen" filtering
        prompt = f"""
        Analyze this Solana token for safety. Look for rugpull risks or supply splits.
        - Mint: {token_data.get('mint')}
        - Symbol: {token_data.get('symbol')}
        - Name: {token_data.get('name')}
        - Creator: {token_data.get('creator')}
        - Description: {token_data.get('description', 'N/A')}
        
        {token_data.get('sentiment_text', 'No recent tweets or context provided.')}
        
        Respond with ONLY a single integer score between 0 and 100.
        0-30: High risk/Rug
        31-70: Medium risk/Neutral
        71-100: Safe/Low risk
        """

        try:
            payload = {
                "model": self._model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1
            }
            headers = {
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json"
            }
            async with aiohttp.ClientSession() as session:
                async with session.post(self._base_url, json=payload, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        content = data['choices'][0]['message']['content'].strip()
                        # Extract first integer found in response
                        import re
                        match = re.search(r'\d+', content)
                        return int(match.group()) if match else 50
                    else:
                        logger.error(f"AI API error: {resp.status} - {await resp.text()}")
        except Exception as e:
            logger.error(f"AI scoring failed: {e}")
        
        return 50 # Default to neutral on error
