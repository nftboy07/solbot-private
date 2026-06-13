with open("../solbot/bot.py", "r", encoding="utf-8") as f:
    lines = f.readlines()

for i, line in enumerate(lines, 1):
    if "_execute_snipe" in line:
        print(f"Line {i}: {line.strip()}")
