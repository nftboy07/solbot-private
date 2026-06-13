with open("../solbot/telegram.py", "r", encoding="utf-8") as f:
    lines = f.readlines()

for i, line in enumerate(lines, 1):
    if "class Telegram" in line or "TelegramManager" in line:
        print(f"Line {i}: {line.strip()}")
