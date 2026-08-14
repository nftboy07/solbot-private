"""Multi-Agent AI Consensus Voting Engine for Solbot."""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any

logger = logging.getLogger("bot.ai_consensus")


@dataclass
class AgentScore:
    """Individual AI agent score and rationale."""
    agent_name: str
    score: int
    is_premine: bool
    is_honeypot: bool
    weight: float
    reason: str
    latency_ms: float


@dataclass
class ConsensusVerdict:
    """Aggregated consensus verdict across multiple AI screening agents."""
    weighted_score: int
    passed: bool
    is_hard_rug: bool
    unanimous_pass: bool
    agent_votes: List[AgentScore] = field(default_factory=list)
    consensus_summary: str = ""


class MultiAgentConsensusEngine:
    """Combines evaluations from OpenAI, Gemini, and OpenRouter with Bayesian weighting."""

    AGENT_WEIGHTS = {
        "openai": 0.40,
        "gemini": 0.35,
        "openrouter": 0.25,
    }

    def __init__(self, ai_filter):
        self._ai_filter = ai_filter

    async def evaluate_token(
        self,
        token_mint: str,
        token_data: Dict[str, Any],
        min_consensus_score: int = 75,
    ) -> ConsensusVerdict:
        """Query multiple AI providers concurrently and calculate weighted consensus."""
        prompt = (
            f"Evaluate Solana token {token_data.get('symbol', 'UNKNOWN')} ({token_mint}). "
            f"Name: {token_data.get('name', 'N/A')}. "
            f"Description: {token_data.get('description', 'N/A')}. "
            f"Respond with JSON {{'score': 0-100, 'is_premine': bool, 'is_honeypot': bool, 'reason': str}}."
        )

        agent_scores: List[AgentScore] = []
        tasks = []

        # 1. OpenAI
        if getattr(self._ai_filter._config.ai, "openai_api_key", None):
            tasks.append(self._eval_agent("openai", self._ai_filter._analyze_safety_with_openai(prompt)))

        # 2. Gemini
        if getattr(self._ai_filter._config.ai, "gemini_api_key", None):
            tasks.append(self._eval_agent("gemini", self._ai_filter.detect_rug_risks(token_mint, token_data.get("creator", ""), [], [])))

        # 3. OpenRouter
        if getattr(self._ai_filter._config.ai, "openrouter_api_key", None):
            tasks.append(self._eval_agent("openrouter", self._ai_filter._analyze_safety_with_openrouter(prompt)))

        if not tasks:
            # Fallback to single ai_filter score
            single_score = await self._ai_filter.score_token(token_data)
            return ConsensusVerdict(
                weighted_score=single_score,
                passed=(single_score >= min_consensus_score),
                is_hard_rug=False,
                unanimous_pass=True,
                consensus_summary="Single agent fallback evaluation",
            )

        results = await asyncio.gather(*tasks, return_exceptions=True)
        for res in results:
            if isinstance(res, AgentScore):
                agent_scores.append(res)

        if not agent_scores:
            return ConsensusVerdict(
                weighted_score=80,
                passed=True,
                is_hard_rug=False,
                unanimous_pass=False,
                consensus_summary="Consensus degraded (all agents failed or rate limited)",
            )

        total_weight = sum(a.weight for a in agent_scores)
        weighted_score = int(sum(a.score * a.weight for a in agent_scores) / max(total_weight, 0.01))

        # Check for hard flags: any agent detecting honeypot or premine with high confidence
        is_hard_rug = any(a.is_honeypot or a.is_premine for a in agent_scores)
        passed = (weighted_score >= min_consensus_score) and not is_hard_rug
        unanimous = all(a.score >= min_consensus_score and not a.is_honeypot for a in agent_scores)

        reasons = [f"[{a.agent_name.upper()}: {a.score}/100 - {a.reason}]" for a in agent_scores]
        summary = f"Consensus score {weighted_score}/100 ({len(agent_scores)} agents). " + " ".join(reasons)

        return ConsensusVerdict(
            weighted_score=weighted_score,
            passed=passed,
            is_hard_rug=is_hard_rug,
            unanimous_pass=unanimous,
            agent_votes=agent_scores,
            consensus_summary=summary,
        )

    async def _eval_agent(self, name: str, coro) -> Optional[AgentScore]:
        import time
        start = time.perf_counter()
        try:
            res = await coro
            latency = (time.perf_counter() - start) * 1000
            if res and isinstance(res, dict):
                return AgentScore(
                    agent_name=name,
                    score=int(res.get("score", 70)),
                    is_premine=bool(res.get("is_premine", False)),
                    is_honeypot=bool(res.get("is_honeypot", False)),
                    weight=self.AGENT_WEIGHTS.get(name, 0.33),
                    reason=str(res.get("reason", "")),
                    latency_ms=latency,
                )
        except Exception as e:
            logger.debug("Agent %s evaluation failed: %s", name, e)
        return None
