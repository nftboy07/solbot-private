import os

env_path = "/root/solbot-production/.env"
updates_path = "/tmp/temp_env_updates.txt"

if not os.path.exists(env_path):
    print(f"Error: {env_path} does not exist.")
    exit(1)

if not os.path.exists(updates_path):
    print(f"Error: {updates_path} does not exist.")
    exit(1)

with open(env_path, "r", encoding="utf-8") as f:
    content = f.read()

with open(updates_path, "r", encoding="utf-8") as f:
    updates = f.read()

new_lines = []
for line in updates.splitlines():
    line_stripped = line.strip()
    if not line_stripped or line_stripped.startswith("#"):
        new_lines.append(line)
        continue
    key = line_stripped.split("=", 1)[0].strip()
    # Check if the key is already configured (either with key= or key = )
    if f"{key}=" not in content.replace(" ", ""):
        new_lines.append(line)

if new_lines:
    with open(env_path, "a", encoding="utf-8") as f:
        # ensure there is a newline before appending
        if not content.endswith("\n"):
            f.write("\n")
        f.write("\n".join(new_lines) + "\n")
    print(f"Successfully appended {len(new_lines)} lines to {env_path}")
else:
    print("All environment settings are already present.")
