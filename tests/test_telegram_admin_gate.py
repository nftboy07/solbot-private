import asyncio
import copy
import inspect
import logging
from types import SimpleNamespace

import pytest
from telethon import events
from telethon.client import updates

from solbot.config import JupiterConfig, TelegramConfig
import solbot.telegram as tg


ADMIN_ID = 111111
ATTACKER_ID = 666666


class FakeBot:
    def __init__(self):
        self._config = SimpleNamespace(
            jupiter=JupiterConfig(), strategy=SimpleNamespace(tp_preset="aggressive")
        )
        self._autobuy_enabled = False
        self._autorunner_enabled = False
        self._autorunner_amount = 0.01
        self._ai_enabled = False
        self._kol_threshold = 3
        self._blacklisted_wallets = {"blocked"}
        self._processed_mints = {"old"}
        self._positions = {}
        self.saves = 0

    def _save_state(self):
        self.saves += 1


class FakeEvent:
    def __init__(self, text, sender_id=ATTACKER_ID):
        self.message = SimpleNamespace(text=text, message=text)
        self._sender = SimpleNamespace(id=sender_id)
        self.replies = []

    async def get_sender(self):
        return self._sender

    async def reply(self, message, **kwargs):
        self.replies.append(message)


class FakeClient:
    def __init__(self):
        self.handlers = []

    def on(self, builder):
        def register(callback):
            self.handlers.append((builder, callback))
            return callback
        return register


@pytest.fixture
def controller(monkeypatch):
    monkeypatch.setattr(tg, "SolanaAuditor", lambda config: None)
    config = TelegramConfig(**{
        "token": "x", "api_hash": "x", "api_id": "1",
        "chat_id": str(ADMIN_ID), "admin_ids": (ADMIN_ID,),
    })
    ctl = tg.TelegramController(config, FakeBot())

    async def noop(*args, **kwargs):
        pass

    ctl.log_brain_event = noop
    return ctl


NEWLY_GUARDED = (
    "status health model panel settings brain_train brain_predict signals portfolio execution "
    "autobuy autorunner why alpha runner profit solbalance blacklist whitelist resetrisk jito "
    "clearmemory tppreset slippage priority stats pipeline active closed kollist addkol removekol "
    "blacklistdeployer removeblacklist modelmode missed dailyrunner kolthreshold kols autotune "
    "rpcbalancer clustermap visualize"
).split()

GUARDED_COMMANDS = sorted({"_cmd_" + name for name in NEWLY_GUARDED} | {
    name for name, method in inspect.getmembers(tg.TelegramController, inspect.isfunction)
    if name.startswith("_cmd_") and "_require_admin(event)" in inspect.getsource(method)
})

MUTATING_ARGUMENTS = {
    "_cmd_autobuy": "on", "_cmd_autorunner": "0.1", "_cmd_slippage": "300",
    "_cmd_tppreset": "conservative", "_cmd_priority": "0.01", "_cmd_model": "on",
    "_cmd_modelmode": "safe", "_cmd_addkol": "wallet alias",
    "_cmd_removekol": "wallet", "_cmd_blacklistdeployer": "wallet",
    "_cmd_removeblacklist": "blocked", "_cmd_kolthreshold": "5",
    "_cmd_autotune": "run", "_cmd_brain_predict": "mint",
}


@pytest.mark.parametrize("command", GUARDED_COMMANDS)
def test_non_admin_cannot_change_state(controller, command):
    bot_before = copy.deepcopy(vars(controller._bot))
    controller_before = dict(vars(controller))
    event = FakeEvent("/" + command.removeprefix("_cmd_") + " " + MUTATING_ARGUMENTS.get(command, ""))
    asyncio.run(getattr(controller, command)(event))
    assert any("Unauthorized" in reply for reply in event.replies)
    assert vars(controller._bot) == bot_before
    assert vars(controller) == controller_before


@pytest.mark.parametrize("command,text,attribute,expected", [
    ("_cmd_autobuy", "/autobuy on", "_autobuy_enabled", True),
    ("_cmd_autorunner", "/autorunner 0.1", "_autorunner_amount", 0.1),
    ("_cmd_slippage", "/slippage 300", "slippage_bps", 300),
])
def test_admin_can_change_state(controller, command, text, attribute, expected):
    event = FakeEvent(text, ADMIN_ID)
    asyncio.run(getattr(controller, command)(event))
    target = controller._bot._config.jupiter if attribute == "slippage_bps" else controller._bot
    assert getattr(target, attribute) == expected
    assert controller._bot.saves == 1
    assert not any("Unauthorized" in reply for reply in event.replies)
    if command == "_cmd_autorunner":
        assert controller._bot._autorunner_enabled is True


def register_gate(controller):
    controller._client = FakeClient()
    controller._register_handlers()
    builder, gate = controller._client.handlers[0]
    assert isinstance(builder, events.NewMessage)
    assert builder.pattern is None
    assert gate.__name__ == "admin_gate"
    return gate


@pytest.mark.parametrize("text", [
    "/autobuy on", "/wallet", "🚨 Kill Switch", "📊 Dashboard", "⚙️ Settings",
    "📍 Portfolio", "/unknown", "/start_not_public", "/list",
])
def test_gate_blocks_attacker(controller, text):
    gate = register_gate(controller)
    event = FakeEvent(text)
    with pytest.raises(events.StopPropagation):
        asyncio.run(gate(event))
    assert any("Unauthorized" in reply for reply in event.replies)


@pytest.mark.parametrize("text,sender", [
    ("/start", ATTACKER_ID), ("/help", ATTACKER_ID), ("/ping", ATTACKER_ID),
    ("/version", ATTACKER_ID), ("/start@solbot", ATTACKER_ID),
    ("hello", ATTACKER_ID), ("", ATTACKER_ID), (None, ATTACKER_ID),
    ("/autobuy on", ADMIN_ID),
])
def test_gate_allows_public_messages_and_admin(controller, text, sender):
    gate = register_gate(controller)
    event = FakeEvent(text, sender)
    asyncio.run(gate(event))
    assert event.replies == []


def test_empty_admin_allowlist_is_closed(controller):
    controller._config = SimpleNamespace(admin_ids=(), chat_id="")
    gate = register_gate(controller)
    event = FakeEvent("/autobuy on", ADMIN_ID)
    with pytest.raises(events.StopPropagation):
        asyncio.run(gate(event))
    assert any("No admins are configured" in reply for reply in event.replies)
    asyncio.run(controller._cmd_autobuy(event))
    assert controller._bot._autobuy_enabled is False
    assert controller._bot.saves == 0


def test_authorization_error_is_closed(controller):
    gate = register_gate(controller)
    event = FakeEvent("/wallet")

    async def broken_sender():
        raise RuntimeError("sender unavailable")

    event.get_sender = broken_sender
    with pytest.raises(events.StopPropagation):
        asyncio.run(gate(event))


@pytest.mark.parametrize("sender,expected", [(ATTACKER_ID, []), (ADMIN_ID, [True])])
def test_installed_telethon_dispatch_stops_later_handlers(controller, monkeypatch, sender, expected):
    gate = register_gate(controller)
    called = []

    async def later(event):
        called.append(True)

    builder = SimpleNamespace(resolved=True, filter=lambda event: True)
    event = FakeEvent("/autobuy on", sender)
    monkeypatch.setattr(updates, "EventBuilderDict", lambda *args: {type(builder): event})
    client = SimpleNamespace(
        _mb_entity_cache=SimpleNamespace(self_id=ADMIN_ID), _conversations={},
        _event_builders=[(builder, gate), (builder, later)],
        _log={updates.__name__: logging.getLogger(__name__)},
    )
    asyncio.run(updates.UpdateMethods._dispatch_update(client, object()))
    assert called == expected
