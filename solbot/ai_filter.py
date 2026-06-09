"""AI-powered token safety filter using BluesMinds AI, MiniMax, or Amazon Bedrock."""

import aiohttp
import asyncio
import json
import logging
import os
import re
from typing import Dict, Optional

logger = logging.getLogger("bot.ai_filter")

class AIFilter:
    """AI-powered token safety filter with fallbacks."""

    def __init__(self, api_key: Optional[str] = None):
        # API Keys for primary/secondary providers
        self._api_key = os.getenv("BLUESMINDS_API_KEY") or api_key or os.getenv("MINIMAX_API_KEY")
        
        if os.getenv("BLUESMINDS_API_KEY"):
            self._base_url = "https://api.bluesminds.com/v1/chat/completions"
            self._model = "gpt-4-turbo"
            logger.info("Using BluesMinds AI Platform.")
        elif os.getenv("MINIMAX_API_KEY") or api_key:
            self._base_url = "https://api.minimax.io/v1/chat/completions"
            self._model = "minimax-m3"
            logger.info("Using MiniMax AI (Secondary).")
        else:
            self._api_key = None
            logger.info("No primary AI API keys found. Will attempt Bedrock fallback.")

    async def _score_with_bedrock(self, prompt: str) -> Optional[int]:
        """
        Fallback scoring using Amazon Bedrock Runtime.
        Uses asyncio.to_thread to keep the event loop non-blocking.
        """
        try:
            # Dynamic import to prevent crash if boto3 is missing
            import boto3
            from botocore.config import Config
        except ImportError:
            logger.error("boto3 not installed. Cannot use Bedrock fallback.")
            return None

        def _invoke():
            region = os.getenv("AWS_DEFAULT_REGION") or os.getenv("AWS_REGION") or "ap-south-1"
            model_id = os.getenv("BEDROCK_MODEL_ID") or "anthropic.claude-3-5-sonnet-20241022-v2:0"
            
            config = Config(region_name=region)
            client = boto3.client("bedrock-runtime", config=config)
            
            body = json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 10,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1
            })
            
            response = client.invoke_model(
                modelId=model_id,
                body=body
            )
            
            response_body = json.loads(response.get("body").read())
            return response_body["content"][0]["text"]

        try:
            content = await asyncio.to_thread(_invoke)
            match = re.search(r"\d+", content)
            return int(match.group()) if match else None
        except Exception as e:
            logger.error(f"Bedrock scoring failed: {e}")
            return None

    async def score_token(self, token_data: Dict) -> int:
        """
        Score a token (0-100) based on metadata and sentiment.
        Attempts primary providers first, then falls back to Amazon Bedrock.
        """
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

        # 1. Attempt Primary/Secondary HTTP Providers (BluesMinds/MiniMax)
        if self._api_key:
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
                            match = re.search(r'\d+', content)
                            if match:
                                return int(match.group())
                        else:
                            logger.error(f"Primary AI API error: {resp.status}")
            except Exception as e:
                logger.error(f"Primary AI scoring failed: {e}")

        # 2. Fallback to Amazon Bedrock
        logger.info("Attempting Amazon Bedrock fallback...")
        bedrock_score = await self._score_with_bedrock(prompt)
        if bedrock_score is not None:
            return bedrock_score
        
        # 3. Final neutral fallback
        return 50
