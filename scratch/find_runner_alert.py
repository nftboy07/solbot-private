import os
import sys

for root, dirs, files in os.walk("../solbot"):
    for file in files:
        if file.endswith(".py"):
            path = os.path.join(root, file)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read()
                if "daily runner" in content.lower():
                    print(f"Found in {path}")
            except Exception as e:
                pass
