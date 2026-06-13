with open("../solbot/telegram.py", "r", encoding="utf-8") as f:
    lines = f.readlines()

for i, line in enumerate(lines, 1):
    if "def _cmd_model" in line or "def _cmd_brain" in line:
        print(f"Line {i}: {line.strip()}")
