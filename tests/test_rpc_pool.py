import asyncio
import time

from solbot.rpc_pool import RPCPool


def test_rpc_pool_reactivates_after_cooldown():
    pool = RPCPool([{"url": "https://example.com", "name": "test"}])
    node = pool.nodes[0]
    node.is_active = False
    node.inactive_since = time.time() - 300

    asyncio.run(pool._reactivate_stale_nodes(cooldown_seconds=120))

    assert node.is_active is True