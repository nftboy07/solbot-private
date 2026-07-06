import subprocess
import sys
from pathlib import Path

KEY_PATH = Path(r"C:\Users\91907\.ssh\REDACTED_SSH_KEY")
VPS = "root@REDACTED_VPS_HOST"
REMOTE_DIR = "/root/solbot-production"


def run(cmd: str) -> bool:
    print(f"$ {cmd}")
    result = subprocess.run(cmd, shell=True)
    return result.returncode == 0


def main():
    repo = Path(__file__).resolve().parents[1]
    if not run(f'git -C "{repo}" push origin refactor/async-architecture'):
        print("Git push failed — commit locally first.")
        sys.exit(1)

    ssh_base = f'ssh -i "{KEY_PATH}" -o StrictHostKeyChecking=no {VPS}'
    scp_base = f'scp -i "{KEY_PATH}" -o StrictHostKeyChecking=no'

    remote_cmds = (
        f"cd {REMOTE_DIR} && "
        "git pull origin refactor/async-architecture && "
        "venv/bin/pip install -r requirements.txt -q && "
        "venv/bin/python3 tools/cleanup_state.py && "
        "systemctl restart solbot.service && "
        "systemctl is-active solbot.service"
    )
    if not run(f'{ssh_base} "{remote_cmds}"'):
        sys.exit(1)

    run(f'{scp_base} "{repo / "ops/logrotate.solbot"}" {VPS}:/etc/logrotate.d/solbot')
    print("Deploy complete.")


if __name__ == "__main__":
    main()