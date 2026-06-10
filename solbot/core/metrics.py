import threading
import time
from dataclasses import dataclass, field
from typing import Dict

@dataclass
class RuntimeMetrics:
    _instance = None
    _lock = threading.Lock()

    start_time: float = field(default_factory=time.time)
    total_signals: int = 0
    filtered_signals: int = 0
    ai_rejected: int = 0
    total_buys: int = 0
    total_sells: int = 0
    
    # Latency (ms)
    avg_processing_latency: float = 0.0
    avg_rpc_latency: float = 0.0
    
    # Errors
    connection_drops: int = 0
    rate_limits: int = 0
    cloudflare_blocks: int = 0

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(RuntimeMetrics, cls).__new__(cls)
            return cls._instance

    def increment(self, metric: str, count: int = 1):
        with self._lock:
            current = getattr(self, metric, 0)
            setattr(self, metric, current + count)

    def update_latency(self, metric: str, new_value: float):
        with self._lock:
            current = getattr(self, metric, 0.0)
            # Simple moving average
            setattr(self, metric, (current * 0.9) + (new_value * 0.1))

    def get_report(self) -> Dict:
        uptime = time.time() - self.start_time
        return {
            "uptime_seconds": uptime,
            "total_signals": self.total_signals,
            "buy_rate": (self.total_buys / self.total_signals * 100) if self.total_signals > 0 else 0,
            "connection_health": "Stable" if self.connection_drops < 5 else "Unstable",
            "avg_proc_latency": self.avg_processing_latency,
            "errors": {
                "drops": self.connection_drops,
                "rate_limits": self.rate_limits,
                "cf_blocks": self.cloudflare_blocks
            }
        }
