import json
with open('/root/solbot-production/data/state.json') as f:
    d = json.load(f)
print("Keys:", list(d.keys()))
print("wallet_scores count:", len(d.get("wallet_scores", {})))
print("copy_targets count:", len(d.get("copy_targets", [])))
print("blacklisted_wallets count:", len(d.get("blacklisted_wallets", [])))
# Print first few wallet scores
kols = [k for k, v in d.get("wallet_scores", {}).items() if "KOL" in v.get("alias", "")]
print("KOL aliases count:", len(kols))
print("First 5 KOLs:", [(k, d["wallet_scores"][k]["alias"]) for k in kols[:5]])
