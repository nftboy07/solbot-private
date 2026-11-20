"""PoC: /autobuy and /autorunner skip _require_admin in solbot/telegram.py.

A Telegram user who is not on TELEGRAM_ADMIN_IDS sends /autobuy or /autorunner
to the bot and changes its live trading switches. /risk, a guarded command,
rejects the same sender. No Telegram, RPC or wallet is contacted.
"""
import asyncio
import types

from solbot.config import TelegramConfig
import solbot.telegram as tg
from solbot.telegram import TelegramController

tg.SolanaAuditor = lambda cfg: None  # offline: no RPC client

ADMIN_ID = 111111
ATTACKER_ID = 666666


class FakeBot:
    def __init__(self):
        self._autobuy_enabled = False
        self._autorunner_enabled = False
        self._autorunner_amount = 0.01
        self.saves = 0
        self._config = None

    def _save_state(self):
        self.saves += 1


class FakeEvent:
    def __init__(self, text, sender_id):
        self.message = types.SimpleNamespace(text=text)
        self._sender = types.SimpleNamespace(id=sender_id)
        self.replies = []

    async def get_sender(self):
        return self._sender

    async def reply(self, msg, **kw):
        self.replies.append(msg)


def make_controller():
    cfg = TelegramConfig(token="x", chat_id=str(ADMIN_ID), api_id="1",
                         api_hash="x", admin_ids=(ADMIN_ID,))
    bot = FakeBot()
    ctl = TelegramController(cfg, bot)

    async def _noop(*a, **k):
        return None
    ctl.log_brain_event = _noop
    return ctl, bot


def test_guarded_command_rejects_attacker():
    ctl, bot = make_controller()
    ev = FakeEvent("/pause", ATTACKER_ID)
    asyncio.run(ctl._cmd_risk(ev))
    print("/risk replies to attacker:", ev.replies)
    assert any("Unauthorized" in r for r in ev.replies)


def test_attacker_enables_autobuy():
    ctl, bot = make_controller()
    assert ATTACKER_ID not in ctl._authorized_admin_ids()
    ev = FakeEvent("/autobuy on", ATTACKER_ID)
    asyncio.run(ctl._cmd_autobuy(ev))
    print("autobuy after attacker:", bot._autobuy_enabled, "saved:", bot.saves, ev.replies)
    assert bot._autobuy_enabled is True
    assert bot.saves == 1
    assert not any("Unauthorized" in r for r in ev.replies)


def test_attacker_enables_autorunner_max_size():
    ctl, bot = make_controller()
    ev = FakeEvent("/autorunner 0.1", ATTACKER_ID)
    asyncio.run(ctl._cmd_autorunner(ev))
    print("autorunner after attacker:", bot._autorunner_enabled, bot._autorunner_amount, ev.replies)
    assert bot._autorunner_enabled is True
    assert bot._autorunner_amount == 0.1


def test_attacker_sets_slippage_100pct():
    import dataclasses
    from solbot.config import JupiterConfig
    ctl, bot = make_controller()
    bot._config = types.SimpleNamespace(jupiter=JupiterConfig())
    ev = FakeEvent("/slippage 10000", ATTACKER_ID)
    asyncio.run(ctl._cmd_slippage(ev))
    print("slippage after attacker:", bot._config.jupiter.slippage_bps, ev.replies)
    assert bot._config.jupiter.slippage_bps == 10000
