# Telegram Commands Audit - Solbot V3.1

| Command | Category | Status | Action Required |
|---------|----------|--------|-----------------|
| `/list` | System | LIVE | None |
| `/status` | System | LIVE | Update with live metrics |
| `/balance` | Wallet | LIVE | None |
| `/portfolio` | Trading | LIVE | None |
| `/mode` | Strategy | LIVE | None |
| `/autobuy` | Strategy | LIVE | None |
| `/buy` | Risk | LIVE | None |
| `/drawdown` | Risk | LIVE | None |
| `/risk` | Risk | LIVE | None |
| `/kill` | Risk | LIVE | None |
| `/proxy` | Network | LIVE | None |
| `/profit` | Reports | LIVE | None |
| `/follow` | KOL/Whale | LIVE | None |
| `/unfollow` | KOL/Whale | LIVE | None |
| `/blacklist` | Security | LIVE | None |
| `/pause` | Control | LIVE | None |
| `/resume` | Control | LIVE | None |
| `/reload` | Control | LIVE | None |
| `/exitall` | Trading | LIVE | None |
| `/aitoggle` | AI | LIVE | None |
| `/aiscore` | AI | LIVE | None |
| `/signals` | Alpha | MOCK | Replace with `signal_history` data |
| `/brain` | AI | MOCK | Connect to `AIFilter` insights |
| `/why` | AI | MOCK | Show reasoning for last reject/buy |
| `/alpha` | Alpha | MOCK | Aggregate KOL signals |
| `/model` | AI | MOCK | Show current AI model version |
| `/feature` | System | MOCK | List enabled modules |
| `/replay` | System | MOCK | Re-run last signal through filters |
| `/health` | System | PARTIAL | Add OS/Memory/CPU usage |
| `/metrics` | Stats | BROKEN | Implement `RuntimeMetrics` |
| `/events` | Stats | BROKEN | Implement live event stream |
| `/runtime` | System | BROKEN | Show uptime and thread health |
| `/proxyhealth`| Network | LIVE | Alias to `/proxy` |
| `/devinfo` | Security | BROKEN | Detail creator history |
| `/walletscore`| Security | LIVE | Connect to `WalletScore` |
| `/creatorinfo`| Security | BROKEN | Connect to `creators` table |
| `/signallog` | Alpha | BROKEN | Tail last 10 signals |
| `/rejects` | Alpha | BROKEN | Show reasons for last 10 skips |
| `/lastbuy` | Trading | BROKEN | Detail last transaction |
| `/lastsignal` | Alpha | BROKEN | Detail last detected token |
