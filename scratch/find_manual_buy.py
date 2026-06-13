with open("../solbot/bot.py", "r", encoding="utf-8") as f:
    lines = f.readlines()

for i, line in enumerate(lines, 1):
    if "manual buy" in line.lower() or "tg button" in line.lower():
        print(f"Line {i}: {line.strip()}")
