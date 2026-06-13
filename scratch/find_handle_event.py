with open("../solbot/bot.py", "r", encoding="utf-8") as f:
    lines = f.readlines()

for i, line in enumerate(lines, 1):
    if "def _handle_event" in line:
        print(f"Line {i}: {line.strip()}")
