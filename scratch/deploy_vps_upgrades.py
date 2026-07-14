import subprocess
import os
import sys

VPS_HOST = "13.201.69.107"
VPS_USER = "root"
KEY_PATH = r"C:\Users\91907\.ssh\id_ed25519"
LOCAL_DIR = r"C:\Users\91907\Documents\New project\solbot-private-ai-fix"
REMOTE_DIR = "/root/solbot-production"

files_to_upload = [
    "solbot/bot.py",
    "solbot/config.py",
    "solbot/agi_prebuy_filter.py",
    "solbot/ai_filter.py",
    "solbot/arbitrage_engine.py",
    "solbot/cabal_detector.py",
    "solbot/safety_decision.py",
]

def run_local_cmd(cmd):
    print(f"Executing: {cmd}")
    res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"Error: {res.stderr}")
    else:
        print(f"Success. Output:\n{res.stdout}")
    return res.returncode == 0

def run_ssh_cmd(cmd_str):
    ssh_cmd = f'ssh -i "{KEY_PATH}" -o StrictHostKeyChecking=no {VPS_USER}@{VPS_HOST} "{cmd_str}"'
    return run_local_cmd(ssh_cmd)

def main():
    print("--- STARTING VPS DEPLOYMENT ---")
    
    # 1. Upload the OpenAI API key file
    local_key_file = "C:/Users/91907/Downloads/openai-api-key.txt"
    remote_key_file = "/root/.secrets/openai-api-key.txt"
    scp_key_cmd = f'scp -i "{KEY_PATH}" -o StrictHostKeyChecking=no "{local_key_file}" {VPS_USER}@{VPS_HOST}:{remote_key_file}'
    print("Uploading OpenAI API key file...")
    if not run_local_cmd(scp_key_cmd):
        print("Failed to upload API key file.")
        sys.exit(1)

    # 2. Upload the code files
    for filepath in files_to_upload:
        local_path = os.path.join(LOCAL_DIR, filepath)
        remote_path = f"{REMOTE_DIR}/{filepath}"
        scp_cmd = f'scp -i "{KEY_PATH}" -o StrictHostKeyChecking=no "{local_path}" {VPS_USER}@{VPS_HOST}:{remote_path}'
        print(f"Uploading {filepath}...")
        if not run_local_cmd(scp_cmd):
            print(f"Failed to upload {filepath}")
            sys.exit(1)

    # 3. Syntax compile check on VPS
    print("Performing syntax compile checks on remote VPS...")
    for filepath in files_to_upload:
        remote_path = f"{REMOTE_DIR}/{filepath}"
        check_cmd = f"/root/solbot-production/venv/bin/python3 -m py_compile {remote_path}"
        if not run_ssh_cmd(check_cmd):
            print(f"Syntax compile check failed for {filepath}")
            sys.exit(1)
        print(f"Syntax check passed for {filepath}")

    # 4. Update VPS .env with safety, arbitrage, and cabal settings
    print("Configuring remote .env file...")
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
    # Write updates to a temp local file, scp it, and append on the VPS
    temp_env_path = os.path.join(LOCAL_DIR, "scratch", "temp_env_updates.txt")
    os.makedirs(os.path.dirname(temp_env_path), exist_ok=True)
    with open(temp_env_path, "w") as f:
        f.write(env_updates)
    
    remote_temp_path = "/tmp/temp_env_updates.txt"
    scp_temp_cmd = f'scp -i "{KEY_PATH}" -o StrictHostKeyChecking=no "{temp_env_path}" {VPS_USER}@{VPS_HOST}:{remote_temp_path}'
    if not run_local_cmd(scp_temp_cmd):
        print("Failed to upload env updates.")
        sys.exit(1)
        
    # Python script on VPS to safely append settings if not already present
    python_env_script = f"""python3 -c '
env_path = "/root/solbot-production/.env"
with open(env_path, "r") as f:
    content = f.read()

with open("{remote_temp_path}", "r") as f:
    updates = f.read()

new_lines = []
for line in updates.splitlines():
    line_stripped = line.strip()
    if not line_stripped or line_stripped.startswith("#"):
        new_lines.append(line)
        continue
    key = line_stripped.split("=", 1)[0].strip()
    if f"{{key}}=" not in content:
        new_lines.append(line)

if new_lines:
    with open(env_path, "a") as f:
        f.write("\\n" + "\\n".join(new_lines) + "\\n")
    print("Appended new settings to .env")
else:
    print("All settings already present in .env")
'"""
    run_ssh_cmd(python_env_script)
    run_ssh_cmd(f"rm {remote_temp_path}")
    if os.path.exists(temp_env_path):
        os.remove(temp_env_path)

    # 5. Restart the service
    print("Restarting solbot.service on VPS...")
    if not run_ssh_cmd("systemctl restart solbot.service"):
        print("Failed to restart solbot.service")
        sys.exit(1)
    print("solbot.service restarted successfully.")

    # 6. Tail logs to verify startup
    print("Waiting 5 seconds for service startup...")
    import time
    time.sleep(5)
    print("Tailing solbot.log:")
    run_ssh_cmd("tail -n 40 /root/solbot-production/solbot.log")

if __name__ == "__main__":
    main()
