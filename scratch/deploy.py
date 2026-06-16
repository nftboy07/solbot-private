import subprocess
import time

def run_local_cmd(cmd):
    print(f"Running local command: {cmd}")
    res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"Error: {res.stderr}")
    else:
        print(f"Output: {res.stdout}")
    return res.returncode == 0

def main():
    key_path = r"C:\Users\91907\.ssh\REDACTED_SSH_KEY"
    vps_ip = "REDACTED_VPS_HOST"
    
    # SCP all modified files to the production directory as root
    files_to_sync = [
        "solbot/pumpfun_client.py",
        "solbot/pump_movers.py",
        "solbot/bot.py",
        "solbot/telegram.py",
        "solbot/ai_tuner.py",
        "solbot/kols_controller.py",
        "solbot/rpc_balancer.py",
        "solbot/agi_prebuy_filter.py",
        "solbot/jito_tip_estimator.py",
        "solbot/ai_filter.py",
        "solbot/cluster_mapper.py"
    ]
    for file_path in files_to_sync:
        scp_cmd = f'scp -i "{key_path}" -o StrictHostKeyChecking=no {file_path} root@{vps_ip}:/root/solbot-production/{file_path}'
        if not run_local_cmd(scp_cmd):
            print(f"Failed to SCP {file_path} to VPS")
            return
        
    # Restart the service and tail logs as root
    ssh_cmd = (
        f'ssh -i "{key_path}" -o StrictHostKeyChecking=no root@{vps_ip} '
        '"systemctl restart solbot.service && '
        'echo "Deployment and restart successful!" && '
        'sleep 2 && '
        'tail -n 30 /root/solbot-production/solbot.log"'
    )
    run_local_cmd(ssh_cmd)

if __name__ == "__main__":
    main()
