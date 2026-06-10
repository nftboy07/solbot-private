import asyncio
import argparse
import json
import sys
from typing import Optional
from solbot.db import Database

async def replay_execution(trade_id: Optional[str] = None, signal_id: Optional[str] = None):
    db = Database()
    await db.connect()

    if trade_id:
        print(f"Replaying execution for Trade ID: {trade_id}")
        trades = await db._execute_read("SELECT * FROM trade_events WHERE trade_id = ?", (trade_id,))
        if not trades:
            print(f"No trade found with ID: {trade_id}")
            return
        
        trade = dict(trades[0])
        print("\n--- Trade Summary ---")
        for k, v in trade.items():
            if v is not None:
                print(f"{k}: {v}")

        features = await db._execute_read("SELECT * FROM feature_snapshots WHERE trade_id = ?", (trade_id,))
        if features:
            print("\n--- Feature Snapshot ---")
            feat_data = json.loads(features[0]['serialized_features'])
            print(json.dumps(feat_data, indent=2))

    elif signal_id:
        print(f"Replaying execution for Signal ID: {signal_id}")
        signals = await db._execute_read("SELECT * FROM signal_events WHERE signal_id = ?", (signal_id,))
        if not signals:
            print(f"No signal found with ID: {signal_id}")
            return
        
        signal = dict(signals[0])
        print("\n--- Signal Details ---")
        for k, v in signal.items():
            if v is not None:
                print(f"{k}: {v}")

        features = await db._execute_read("SELECT * FROM feature_snapshots WHERE signal_id = ?", (signal_id,))
        if features:
            print("\n--- Feature Snapshot ---")
            feat_data = json.loads(features[0]['serialized_features'])
            print(json.dumps(feat_data, indent=2))

    # Generic execution path details from RPC and Proxy events could be linked here if IDs match
    print("\nReplay complete.")

def main():
    parser = argparse.ArgumentParser(description="Solbot Replay Tool")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--trade-id", help="Replay by Trade ID")
    group.add_argument("--signal-id", help="Replay by Signal ID")
    
    args = parser.parse_args()
    
    asyncio.run(replay_execution(trade_id=args.trade_id, signal_id=args.signal_id))

if __name__ == "__main__":
    main()
