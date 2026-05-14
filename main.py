"""Solbot Entry Point.

Scalable async Solana trading bot for Pump.fun token monitoring
and Jupiter DEX execution.

Usage:
    python main.py
"""

import asyncio

from solbot.bot import run_bot


def main():
    """Launch the async event loop and run the bot."""
    print(
        """
    ╔══════════════════════════════════════════╗
    ║           SOLBOT v1.0.0                  ║
    ║  Pump.fun Monitor + Jupiter Executor     ║
    ║  Press Ctrl+C to stop                    ║
    ╚══════════════════════════════════════════╝
    """
    )
    asyncio.run(run_bot())


if __name__ == "__main__":
    main()
