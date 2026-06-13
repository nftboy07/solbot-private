with open("../solbot/bot.py", "r", encoding="utf-8") as f:
    lines = f.readlines()

for i, line in enumerate(lines, 1):
    if "mover" in line.lower() or "trending" in line.lower():
        print(f"Line {i}: {line.strip()}")
