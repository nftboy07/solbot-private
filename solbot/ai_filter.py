"""AI-powered token safety filter with NVIDIA NIM, BluesMinds AI, MiniMax, or Amazon Bedrock."""

import aiohttp
import asyncio
import json
import logging
import os
import re
from typing import Dict, Optional

from solbot.config import BotConfig

logger = logging.getLogger("bot.ai_filter")

class AIFilter:
    """AI-powered token safety filter with fallbacks."""

    def __init__(self, config: Optional[BotConfig] = None):
        self._config = config or BotConfig()
        self._api_key = None
        self._base_url = None
        self._model = None

        # Prioritize NVIDIA NIM
        if self._config.ai.nvidia_api_key:
            self._api_key = self._config.ai.nvidia_api_key
            self._base_url = self._config.ai.nvidia_api_url
            self._model = self._config.ai.nvidia_model
            logger.info(f"Using NVIDIA NIM API (Primary): {self._model}")
        # Fallback to BluesMinds
        elif self._config.ai.bluesminds_api_key:
            self._api_key = self._config.ai.bluesminds_api_key
            self._base_url = "https://api.bluesminds.com/v1/chat/completions"
            self._model = "gpt-4-turbo"
            logger.info("Using BluesMinds AI Platform.")
        # Fallback to MiniMax
        elif self._config.ai.minimax_api_key:
            self._api_key = self._config.ai.minimax_api_key
            self._base_url = "https://api.minimax.io/v1/chat/completions"
            self._model = "minimax-m3"
            logger.info("Using MiniMax AI.")
        else:
            logger.info("No primary AI API keys found. Will attempt Bedrock fallback.")

    async def _score_with_bedrock(self, prompt: str) -> Optional[int]:
        """
        Fallback scoring using Amazon Bedrock Runtime.
        Supports AWS Bearer Token via direct HTTP request if configured,
        otherwise uses standard boto3 SigV4.
        """
        bearer_token = self._config.ai.aws_bearer_token_bedrock
        region = self._config.ai.aws_region
        model_id = self._config.ai.bedrock_model_id

        payload = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 10,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1
        }

        # Use Bearer Token if available (Direct HTTP)
        if bearer_token:
            logger.info("Using AWS Bearer Token for Bedrock.")
            url = f"https://bedrock-runtime.{region}.amazonaws.com/model/{model_id}/invoke"
            headers = {
                "Authorization": f"Bearer {bearer_token}",
                "Content-Type": "application/json",
                "X-Amzn-Bedrock-Accept": "application/json"
            }
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(url, json=payload, headers=headers) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            content = data["content"][0]["text"]
                            match = re.search(r"\d+", content)
                            return int(match.group()) if match else None
                        else:
                            error_text = await resp.text()
                            logger.error(f"Bedrock Bearer Token error: {resp.status} - {error_text}")
                            return None
            except Exception as e:
                logger.error(f"Bedrock HTTP call failed: {e}")
                return None

        # Standard boto3 SigV4 Fallback
        try:
            import boto3
            from botocore.config import Config
        except ImportError:
            logger.error("boto3 not installed and no Bearer Token provided.")
            return None

        def _invoke():
            config = Config(region_name=region)
            client = boto3.client("bedrock-runtime", config=config)
            
            body = json.dumps(payload)
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
            logger.error(f"Bedrock SigV4 scoring failed: {e}")
            return None

    async def score_token(self, token_data: Dict) -> int:
        """
        Score a token (0-100) based on metadata and sentiment.
        Attempts NVIDIA/BluesMinds/MiniMax first, then falls back to Amazon Bedrock.
        """
        prompt = f\"\"\"
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
        \"\"\"

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

        logger.info("Attempting Amazon Bedrock fallback...")
        bedrock_score = await self._score_with_bedrock(prompt)
        if bedrock_score is not None:
            return bedrock_score
        
        return 50
