"""Parse and filter pump.fun coin payloads without hitting the network."""

from solbot.mayhem import metadata_indicates_mayhem
from solbot.new_launch_scanner import (
    created_timestamp_seconds,
    extract_coin_list,
    lamports_to_sol,
    should_skip_coin,
    token_event_from_pump_coin,
)


LIVE_SHAPED_COIN = {
    "mint": "44Y3CuAK9SCTCoD93LEukokbrbKFR4qD4ZNAR4rApump",
    "name": "bullying",
    "symbol": "bullying",
    "metadata_uri": "https://ipfs.io/ipfs/bafkreid62idqa6qvsovltacfoeswmd6yiqxmdr4zu2pp7vddquj7chx2we",
    "creator": "D6HD6QHSmMkyCXJ23UDm5vdyZGM443G1mKqhFBEctmqs",
    "created_timestamp": 1788460530000,
    "complete": False,
    "virtual_sol_reserves": 48922261202,
    "real_sol_reserves": 18922261202,
    "usd_market_cap": 7826.56,
    "is_banned": False,
}


def test_extract_coin_list_accepts_raw_array_and_wrapped_data():
    assert extract_coin_list([LIVE_SHAPED_COIN])[0]["mint"] == LIVE_SHAPED_COIN["mint"]
    assert extract_coin_list({"data": [LIVE_SHAPED_COIN]})[0]["symbol"] == "bullying"
    assert extract_coin_list({"oops": 1}) == []


def test_lamports_and_created_timestamp_units():
    assert abs(lamports_to_sol(48922261202) - 48.922261202) < 1e-9
    assert lamports_to_sol(12.5) == 12.5
    assert created_timestamp_seconds(1788460530000) == 1788460530.0
    assert created_timestamp_seconds(1788460530) == 1788460530.0


def test_token_event_from_live_shaped_payload():
    token = token_event_from_pump_coin(LIVE_SHAPED_COIN, sol_price=150.0)
    assert token is not None
    assert token.mint.endswith("pump")
    assert token.symbol == "bullying"
    assert token.liquidity_sol > 40.0
    assert token.market_cap_usd == 7826.56
    assert abs(token.timestamp - 1788460530.0) < 0.01


def test_skip_banned_mayhem_no_lp_and_ungated_graduation():
    assert should_skip_coin({**LIVE_SHAPED_COIN, "is_banned": True}, ["pumpfun"]) == "banned"
    mayhem_coin = {**LIVE_SHAPED_COIN, "mayhem_state": "active"}
    assert metadata_indicates_mayhem(mayhem_coin)
    assert should_skip_coin(mayhem_coin, ["pumpfun"]) == "mayhem"
    assert should_skip_coin({**LIVE_SHAPED_COIN, "complete": True}, ["pumpfun"]) == "graduated (raydium source disabled)"
    assert should_skip_coin({**LIVE_SHAPED_COIN, "complete": True}, ["pumpfun", "raydium"]) is None
    empty = {
        **LIVE_SHAPED_COIN,
        "virtual_sol_reserves": 0,
        "real_sol_reserves": 0,
        "virtual_quote_reserves": 0,
        "real_quote_reserves": 0,
    }
    assert should_skip_coin(empty, ["pumpfun"]) == "no LP"
    assert should_skip_coin(LIVE_SHAPED_COIN, ["pumpfun"]) is None
