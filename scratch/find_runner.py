with open("../solbot/telegram.py", "r", encoding="utf-8") as f:
    lines = f.readlines()

for i, line in enumerate(lines, 1):
    if "runner" in line.lower():
        print(f"Line {i}: {line.strip()}")
