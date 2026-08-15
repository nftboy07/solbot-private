import asyncio
import logging
import aiohttp
from typing import Optional, Dict

logger = logging.getLogger("bot.jito_estimator")

class JitoTipEstimator:
    """
    Periodically polls the public Jito bundle tip floor API
    to dynamically estimate bundle tipping requirements.
    """
    def __init__(self, interval: int = 15):
        self._interval = interval
        self._url = "https://bundles.jito.wtf/api/v1/bundles/tip_floor"
        self._percentiles: Dict[str, float] = {
            "25th": 0.00001,
            "50th": 0.00005,
            "75th": 0.0001,
            "95th": 0.001,
            "99th": 0.005
        }
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._session: Optional[aiohttp.ClientSession] = None

    async def start(self):
        """Starts the background estimation loop."""
        if self._running:
            return
        self._running = True
        self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5))
        self._task = asyncio.create_task(self._loop())
        logger.info("Jito Tip Estimator background task started.")

    async def stop(self):
        """Stops the background estimation loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._session:
            await self._session.close()
        logger.info("Jito Tip Estimator background task stopped.")

    async def _loop(self):
        while self._running:
            try:
                async with self._session.get(self._url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if isinstance(data, list) and len(data) > 0:
                            # Sometimes the API returns a list of objects or a single dict
                            metrics = data[0]
                        else:
                            metrics = data
                        
                        if isinstance(metrics, dict):
                            self._percentiles["25th"] = float(metrics.get("landed_tips_25th_percentile", self._percentiles["25th"]))
                            self._percentiles["50th"] = float(metrics.get("landed_tips_50th_percentile", self._percentiles["50th"]))
                            self._percentiles["75th"] = float(metrics.get("landed_tips_75th_percentile", self._percentiles["75th"]))
                            self._percentiles["95th"] = float(metrics.get("landed_tips_95th_percentile", self._percentiles["95th"]))
                            self._percentiles["99th"] = float(metrics.get("landed_tips_99th_percentile", self._percentiles["99th"]))
                            logger.debug(f"Updated Jito Tip floors: 50th={self._percentiles['50th']:.5f} SOL, 75th={self._percentiles['75th']:.5f} SOL")
                    else:
                        logger.warning(f"Jito Tip API returned status {resp.status}")
            except Exception as e:
                logger.error(f"Error querying Jito Tip API: {e}")
            await asyncio.sleep(self._interval)

    def get_tip(self, priority: str = "medium") -> float:
        """
        Returns the estimated Jito tip in SOL clamped to safe boundaries.
        priority can be: 'low' (25th), 'medium' (50th), 'high' (75th), 'max' (95th).
        """
        key = "50th"
        if priority == "low":
            key = "25th"
        elif priority == "high":
            key = "75th"
        elif priority == "max":
            key = "95th"

        tip = self._percentiles.get(key, 0.00003)
        # Enforce safety boundaries: min 0.00003 SOL, max 0.001 SOL to protect user wallet from fee drag
        return max(0.00003, min(0.001, tip))
