import subprocess
import os
import sys

VPS_HOST = "REDACTED_VPS_HOST"
VPS_USER = "root"
KEY_PATH = r"C:\Users\91907\.ssh\REDACTED_SSH_KEY"
LOCAL_DIR = r"C:\Users\91907\Documents\New project\solbot-private-ai-fix"

env_updates = """
# OpenAI Responses API
OPENAI_API_KEY_FILE=/root/.secrets/openai-api-key.txt
OPENAI_API_URL=https://api.openai.com/v1/responses
OPENAI_MODEL=gpt-5.4-mini

# Cross-DEX Arbitrage
ARBITRAGE_ENABLED=false
ARBITRAGE_DRY_RUN=true
ARBITRAGE_ROUTE_DEXES=Raydium,Meteora,Orca,Pump.fun
ARBITRAGE_INPUT_SOL=0.10
ARBITRAGE_MIN_PROFIT_SOL=0.02
ARBITRAGE_ESTIMATED_FEES_SOL=0.003
ARBITRAGE_JITO_TIP_SOL=0.001
ARBITRAGE_SCAN_INTERVAL_SECONDS=15
ARBITRAGE_SLIPPAGE_BPS=100
ARBITRAGE_LOG_FILE=arbitrage.log

# Cabal / Developer Cluster Detection
CABAL_DETECTOR_ENABLED=true
CABAL_TOP_HOLDERS_LIMIT=20
CABAL_MAX_CLUSTER_SUPPLY_PCT=30
CABAL_MAX_TRACE_HOPS=3
CABAL_CACHE_TTL_SECONDS=180
"""

def run_local_cmd(cmd):
    print(f"Executing: {cmd}")
    res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"Error: {res.stderr}")
    else:
        print(f"Success. Output:\n{res.stdout}")
    return res.returncode == 0

def main():
    # 1. Write temp_env_updates.txt locally
    temp_env_path = os.path.join(LOCAL_DIR, "scratch", "temp_env_updates.txt")
    with open(temp_env_path, "w", encoding="utf-8") as f:
        f.write(env_updates)

    # 2. SCP updater and updates to VPS
    scp_updater = f'scp -i "{KEY_PATH}" -o StrictHostKeyChecking=no "{LOCAL_DIR}/scratch/vps_env_updater.py" {VPS_USER}@{VPS_HOST}:/tmp/vps_env_updater.py'
    scp_updates = f'scp -i "{KEY_PATH}" -o StrictHostKeyChecking=no "{temp_env_path}" {VPS_USER}@{VPS_HOST}:/tmp/temp_env_updates.txt'
    
    if not run_local_cmd(scp_updater):
        print("Failed to upload updater script.")
        sys.exit(1)
        
    if not run_local_cmd(scp_updates):
        print("Failed to upload env updates.")
        sys.exit(1)

    # 3. Execute updater on VPS
    ssh_run = f'ssh -i "{KEY_PATH}" -o StrictHostKeyChecking=no {VPS_USER}@{VPS_HOST} "python3 /tmp/vps_env_updater.py"'
    if not run_local_cmd(ssh_run):
        print("Failed to execute env updater on VPS.")
        sys.exit(1)

    # 4. Clean up VPS temp files
    ssh_cleanup = f'ssh -i "{KEY_PATH}" -o StrictHostKeyChecking=no {VPS_USER}@{VPS_HOST} "rm /tmp/vps_env_updater.py /tmp/temp_env_updates.txt"'
    run_local_cmd(ssh_cleanup)

    # 5. Clean up local temp files
    if os.path.exists(temp_env_path):
        os.remove(temp_env_path)
        
    print("Environment updates applied successfully.")

if __name__ == "__main__":
    main()
