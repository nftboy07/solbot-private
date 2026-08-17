"""AI-powered token safety filter with OpenAI and provider fallbacks."""

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
        self._openai_api_key = self._config.ai.openai_api_key
        self._openai_api_url = self._config.ai.openai_api_url
        self._openai_model = self._config.ai.openai_model

        if self._openai_api_key:
            logger.info(f"Using OpenAI Responses API for safety analysis: {self._openai_model}")

        # Primary High-Speed AI Providers (Groq / NVIDIA / BluesMinds)
        groq_key = getattr(self._config.ai, "groq_api_key", None) or os.environ.get("GROQ_API_KEY")
        if groq_key:
            self._api_key = groq_key
            self._base_url = "https://api.groq.com/openai/v1/chat/completions"
            self._model = "llama-3.3-70b-versatile"
            logger.info(f"Using Groq High-Speed API (Primary): {self._model}")
        elif self._config.ai.nvidia_api_key:
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
        elif not self._openai_api_key:
            logger.info("No primary AI API keys found. Will attempt Bedrock fallback.")

    async def _call_openai_response(self, prompt: str, max_output_tokens: int) -> Optional[str]:
        if not self._openai_api_key:
            return None

        payload = {
            "model": self._openai_model,
            "input": prompt,
            "max_output_tokens": max_output_tokens,
        }
        headers = {
            "Authorization": f"Bearer {self._openai_api_key}",
            "Content-Type": "application/json",
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self._openai_api_url,
                    json=payload,
                    headers=headers,
                    timeout=20,
                ) as resp:
                    if resp.status != 200:
                        logger.error(f"OpenAI Responses API error: {resp.status}")
                        return None
                    data = await resp.json()
                    return self._extract_response_text(data)
        except Exception as e:
            logger.error(f"OpenAI Responses API call failed: {e}")
            return None

    @staticmethod
    def _extract_response_text(data: dict) -> str:
        output_text = data.get("output_text")
        if isinstance(output_text, str) and output_text.strip():
            return output_text.strip()

        chunks = []
        for item in data.get("output", []) or []:
            for content in item.get("content", []) or []:
                text = content.get("text")
                if isinstance(text, str):
                    chunks.append(text)
        return "\n".join(chunks).strip()

    async def _score_with_openai(self, prompt: str) -> Optional[int]:
        content = await self._call_openai_response(prompt, max_output_tokens=20)
        if not content:
            return None
        match = re.search(r"\d+", content)
        if not match:
            return None
        score = max(0, min(100, int(match.group())))
        logger.info(f"OpenAI API scored token: {score}")
        return score

    async def _analyze_safety_with_openai(self, prompt: str) -> Optional[dict]:
        content = await self._call_openai_response(prompt, max_output_tokens=300)
        if not content:
            return None
        json_match = re.search(r"\{.*\}", content, re.DOTALL)
        if not json_match:
            return None
        try:
            res = json.loads(json_match.group())
        except json.JSONDecodeError as e:
            logger.error(f"OpenAI Safety Analysis returned invalid JSON: {e}")
            return None
        logger.info(f"OpenAI Safety Analysis: {res}")
        return _normalize_safety_result(res, provider="openai")

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

    async def _score_with_gemini(self, prompt: str) -> Optional[int]:
        api_key = self._config.ai.gemini_api_key
        if not api_key:
            return None
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
        payload = {
            "contents": [{
                "parts": [{"text": prompt}]
            }]
        }
        headers = {
            "Content-Type": "application/json"
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
                        match = re.search(r"\d+", text)
                        if match:
                            score = int(match.group())
                            logger.info(f"Gemini API scored token: {score}")
                            return score
                    else:
                        error_text = await resp.text()
                        logger.error(f"Gemini API error: {resp.status} - {error_text}")
        except Exception as e:
            logger.error(f"Gemini API call failed: {e}")
        return None

    async def _call_openrouter(self, prompt: str) -> Optional[str]:
        if not self._config.ai.openrouter_api_key:
            return None

        # Build fallback model pool with active free endpoints
        primary = self._config.ai.openrouter_model
        fallbacks = [
            "meta-llama/llama-3.1-8b-instruct:free",
            "meta-llama/llama-3.2-3b-instruct:free",
            "meta-llama/llama-3.2-1b-instruct:free",
            "mistralai/mistral-7b-instruct:free",
            "deepseek/deepseek-r1:free",
            "google/gemini-2.0-flash-exp:free",
            "google/gemini-flash-1.5-exp:free",
            "qwen/qwen-2.5-coder-32b-instruct:free",
        ]
        
        # Ensure primary model is first, and deduplicate
        models = [primary] if primary else []
        for fb in fallbacks:
            if fb not in models:
                models.append(fb)

        headers = {
            "Authorization": f"Bearer {self._config.ai.openrouter_api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/oblien/openship",
            "X-Title": "Solbot Sniper"
        }

        for model in models:
            payload = {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1
            }
            logger.info(f"[OpenRouter] Querying model {model}...")
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        self._config.ai.openrouter_api_url,
                        json=payload,
                        headers=headers,
                        timeout=15
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            content = data['choices'][0]['message']['content'].strip()
                            if content:
                                logger.info(f"[OpenRouter] Success with model: {model}")
                                return content
                        elif resp.status == 429:
                            logger.warning(f"[OpenRouter] Rate limited (429) for model: {model}. Trying fallback...")
                        else:
                            error_text = await resp.text()
                            logger.warning(f"[OpenRouter] Error {resp.status} for model {model}: {error_text}. Trying fallback...")
            except Exception as e:
                logger.error(f"[OpenRouter] Failed calling model {model}: {e}")

        logger.error("[OpenRouter] All models in the pool failed or returned rate limits.")
        return None

    async def _score_with_openrouter(self, prompt: str) -> Optional[int]:
        content = await self._call_openrouter(prompt)
        if not content:
            return None
        match = re.search(r"\d+", content)
        if not match:
            return None
        score = max(0, min(100, int(match.group())))
        logger.info(f"OpenRouter API scored token: {score}")
        return score

    async def _analyze_safety_with_openrouter(self, prompt: str) -> Optional[dict]:
        content = await self._call_openrouter(prompt)
        if not content:
            return None
        json_match = re.search(r"\{.*\}", content, re.DOTALL)
        if not json_match:
            return None
        try:
            res = json.loads(json_match.group())
        except json.JSONDecodeError as e:
            logger.error(f"OpenRouter Safety Analysis returned invalid JSON: {e}")
            return None
        logger.info(f"OpenRouter Safety Analysis: {res}")
        return _normalize_safety_result(res, provider="openrouter")

    async def score_token(self, token_data: Dict) -> int:
        """
        Score a token (0-100) based on metadata and sentiment.
        Attempts OpenAI first if key is present, then Gemini, NVIDIA/BluesMinds/MiniMax,
        and falls back to Amazon Bedrock.
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

        # 1. High-Speed Direct API (Groq / NVIDIA / BluesMinds)
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
                    async with session.post(self._base_url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=5.0)) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            content = data['choices'][0]['message']['content'].strip()
                            match = re.search(r'\d+', content)
                            if match:
                                score = int(match.group())
                                logger.info(f"AI API ({self._model}) scored token: {score}")
                                return score
                        else:
                            logger.error(f"Primary AI API error: {resp.status}")
            except Exception as e:
                logger.error(f"Primary AI scoring failed: {e}")

        # 2. OpenAI Responses API Fallback
        openai_score = await self._score_with_openai(prompt)
        if openai_score is not None:
            return openai_score

        # 3. OpenRouter Fallback
        if self._config.ai.openrouter_api_key:
            openrouter_score = await self._score_with_openrouter(prompt)
            if openrouter_score is not None:
                return openrouter_score

        # 4. Gemini Fallback
        if self._config.ai.gemini_api_key:
            gemini_score = await self._score_with_gemini(prompt)
            if gemini_score is not None:
                return gemini_score

        logger.info("Attempting Amazon Bedrock fallback...")
        bedrock_score = await self._score_with_bedrock(prompt)
        if bedrock_score is not None:
            return bedrock_score
        
        # AI_FAIL_OPEN_SCORE defaults to 0 so an outage rejects rather than waves
        # every launch through: a passing score here means a dead provider chain
        # silently disables the safety filter entirely.
        fail_score = int(getattr(self._config.ai, "fail_open_score", 0))
        logger.warning(
            "AI scoring failed (API keys invalid or service down). Falling back to score %s.",
            fail_score,
        )
        return fail_score

    async def detect_rug_risks(self, token_mint: str, creator: str, holders: list, creator_history: list) -> dict:
        """
        Analyze Solana token distribution, dev history, and freeze authority for rug pull risks.
        Returns a dict: {"score": int (0-100), "is_premine": bool, "is_honeypot": bool, "reason": str}
        """
        # 1. Tracing Funding Source Recursively (Creator Graph Blacklisting)
        if hasattr(self, '_bot') and self._bot:
            db = getattr(self._bot, '_db', None)
            mapper = getattr(self._bot, '_cluster_mapper', None)
            if db and mapper:
                try:
                    rpc_url = await self._bot._pump_client._get_rpc_url()
                    current = creator
                    for hop in range(3):
                        parent = await mapper.trace_creator_genesis(current, rpc_url, max_hops=1)
                        if not parent or parent == current:
                            break
                        creator_data = await db.get_creator(parent)
                        if creator_data:
                            blacklist_score = float(creator_data.get("blacklist_score", 0.0) or 0.0)
                            rug_count = int(creator_data.get("rug_count", 0) or 0)
                            if blacklist_score > 80.0 or rug_count > 0:
                                logger.warning(f"🚫 Creator Graph Blacklist Triggered! Creator {creator} funded by blacklisted wallet: {parent} (Score: {blacklist_score}, Rugs: {rug_count})")
                                return {
                                    "score": 10,
                                    "is_premine": False,
                                    "is_honeypot": False,
                                    "reason": f"Creator funded by blacklisted ancestor wallet: {parent[:6]}...{parent[-4:]}"
                                }
                        current = parent
                except Exception as e:
                    logger.error(f"Error checking creator graph blacklist: {e}")

        # Format holders & creator history for prompt
        holders_str = "\n".join([f"- Account: {h.get('account', 'unknown')[:8]}... | Share: {h.get('share_pct', 0.0):.2f}%" for h in holders[:10]])
        history_str = "\n".join([f"- Token: {h.get('mint', 'unknown')[:8]}... | Peak Mcap: ${h.get('peak_mcap_usd', 0.0):,.0f} | Rugged: {h.get('rugged', False)}" for h in creator_history[:5]])

        prompt = f"""
        Analyze the following Solana token metrics for rugpull, supply split, and honeypot risks:
        - Mint: {token_mint}
        - Creator: {creator}
        
        TOP HOLDERS:
        {holders_str or 'No holder data provided.'}
        
        CREATOR LAUNCH HISTORY:
        {history_str or 'No history data provided.'}
        
        Strict Evaluation Criteria:
        1. Premine / Supply Split: If top 10 holders (excluding raydium/bonding curve pool) own > 50% combined, or a single wallet holds > 20%, flag is_premine = true.
        2. Honeypot: If freeze authority exists or any indicator of locked trading is present, flag is_honeypot = true.
        3. Rug History: If the creator has rugged previous launches, score must be below 40.
        
        Respond with a valid JSON object only. No markdown code blocks, no other text.
        Structure:
        {{
            "score": <0-100 integer representing safety score. 0-30: High risk, 31-70: Medium risk, 71-100: Safe>,
            "is_premine": <true/false>,
            "is_honeypot": <true/false>,
            "reason": "<one sentence explanation of risk factors>"
        }}
        """

        openai_result = await self._analyze_safety_with_openai(prompt)
        if openai_result is not None:
            return openai_result

        if self._config.ai.openrouter_api_key:
            openrouter_result = await self._analyze_safety_with_openrouter(prompt)
            if openrouter_result is not None:
                return openrouter_result

        # 1. Try Gemini
        api_key = self._config.ai.gemini_api_key
        if api_key:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
            payload = {
                "contents": [{
                    "parts": [{"text": prompt}]
                }]
            }
            headers = {"Content-Type": "application/json"}
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(url, json=payload, headers=headers) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
                            json_match = re.search(r"\{.*\}", text, re.DOTALL)
                            if json_match:
                                res = json.loads(json_match.group())
                                logger.info(f"Gemini Safety Analysis for {token_mint}: {res}")
                                return _normalize_safety_result(res, provider="gemini")
                        else:
                            error_text = await resp.text()
                            logger.error(f"Gemini Safety API error: {resp.status} - {error_text}")
            except Exception as e:
                logger.error(f"Gemini Safety API failed: {e}")

        # 2. Try Primary AI Provider (NVIDIA/BluesMinds/MiniMax)
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
                            json_match = re.search(r"\{.*\}", content, re.DOTALL)
                            if json_match:
                                res = json.loads(json_match.group())
                                logger.info(f"Primary AI Safety Analysis for {token_mint}: {res}")
                                return _normalize_safety_result(res, provider="primary")
                        else:
                            logger.error(f"Primary AI Safety API error: {resp.status}")
            except Exception as e:
                logger.error(f"Primary AI Safety Analysis failed: {e}")

        return {
            "score": 80,
            "is_premine": False,
            "is_honeypot": False,
            "reason": "Safety scan fallback used (APIs rate-limited or unavailable).",
            "scan_status": "degraded",
            "is_fallback": True,
        }


def _normalize_safety_result(res: dict, provider: str) -> dict:
    return {
        "score": _coerce_score(res.get("score", 80)),
        "is_premine": bool(res.get("is_premine", False)),
        "is_honeypot": bool(res.get("is_honeypot", False)),
        "reason": str(res.get("reason", "Analyzed successfully.")),
        "scan_status": "ok",
        "is_fallback": False,
        "provider": provider,
    }


def _coerce_score(value) -> int:
    try:
        return max(0, min(100, int(value)))
    except (TypeError, ValueError):
        return 80
