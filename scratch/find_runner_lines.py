import sys

with open("../solbot/bot.py", "r", encoding="utf-8") as f:
    lines = f.readlines()

for i, line in enumerate(lines, 1):
    if "daily runner" in line.lower() or "runner_buys" in line.lower():
        sys.stdout.buffer.write(f"Line {i}: {line.strip()}\n".encode('utf-8'))
