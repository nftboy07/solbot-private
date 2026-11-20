# Unauthenticated trading control via `/autobuy`, `/autorunner`, `/slippage` (High)

## Summary
Most privileged Telegram commands in `solbot/telegram.py` start with `if not await self._require_admin(event): return`. `TelegramController._cmd_autobuy` (line 1643), `_cmd_autorunner` (line 1656) and `_cmd_slippage` (line 2140) do not. Their handlers (`_register_handlers`, lines 185-191) are registered with `events.NewMessage(pattern=...)` and no chat or sender filter. So any Telegram user who messages the bot reaches these commands.

## Root cause
The authorization check lives in each command body, not in the handler registration. These three handlers do not call it, so the bot never compares the sender with `TELEGRAM_ADMIN_IDS` / `TELEGRAM_CHAT_ID`. Guarded commands such as `/risk`, `/setsize`, `/live` and `/panic` reject the same sender.

## Impact (unprivileged attacker, today)
The attacker only needs the bot's @username, which shows in every alert the bot sends.
- `/autobuy on` sets `bot._autobuy_enabled = True` and saves it with `_save_state()`, so the setting survives restarts. With it on, `bot.py` (around lines 1201, 1473 and 2956) fires `_execute_snipe` with real SOL on every token the filters pass. `/autobuy off` silently stops the owner's strategy.
- `/autorunner 0.1` turns on runner auto-buys at the largest size (bot.py:2396-2398).
- `/slippage 10000` sets Jupiter `slippage_bps` to 100%, so every swap accepts any price. The attacker can then sandwich the bot's trades, or have the bot buy a token the attacker controls at an inflated price, and take the bot's SOL.

## PoC
The PoC is offline. It drives the real `TelegramController` methods with a fake Telethon event from a sender who is not on the admin list. No Telegram, RPC or wallet is contacted.

```
pip install -r requirements.txt pytest
python3 -m pytest poc/test_autobuy_authz_bypass.py -v -s -p no:cacheprovider -c /dev/null --rootdir=.
```
Output:
```
test_guarded_command_rejects_attacker /risk replies to attacker: ['⛔️ Unauthorized. This command is restricted to bot admins.']  PASSED
test_attacker_enables_autobuy autobuy after attacker: True saved: 1 ['🤖 <b>Autobuy:</b> <code>ENABLED</code>']  PASSED
test_attacker_enables_autorunner_max_size autorunner after attacker: True 0.1 [... ENABLED ... 0.1 SOL]  PASSED
test_attacker_sets_slippage_100pct slippage after attacker: 10000 ['⚙️ <b>Jupiter Slippage set to:</b> <code>10000 BPS</code> (100.00%)']  PASSED
4 passed
```

## Fix
Add `if not await self._require_admin(event): return` at the top of `_cmd_autobuy`, `_cmd_autorunner` and `_cmd_slippage`, and audit the other unguarded state-changing commands (`/tppreset`, `/resetrisk`, `/clearmemory`, `/addkol`, `/removekol`, `/blacklistdeployer`, `/removeblacklist`, `/modelmode`, `/kolthreshold`, `/autotune`). Better: enforce admin-only access once, in `_register_handlers`, for every non-public command.

## Venue
Private report to the repo owner (nftboy07/solbot-private). This is a private repo with no public bounty program.
