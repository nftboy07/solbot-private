import sys

with open("../solbot/telegram.py", "r", encoding="utf-8") as f:
    lines = f.readlines()

for i, line in enumerate(lines, 1):
    if "submitting" in line.lower():
        sys.stdout.buffer.write(f"Line {i}: {line.strip()}\n".encode('utf-8'))
