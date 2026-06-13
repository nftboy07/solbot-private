p = '/root/solbot-production/.env'
with open(p, 'r') as f:
    lines = f.readlines()

new_lines = []
has_max_positions = False
for line in lines:
    if line.startswith('MIN_MARKET_CAP_USD='):
        new_lines.append('MIN_MARKET_CAP_USD=100000\n')
    elif line.startswith('MAX_ACTIVE_POSITIONS='):
        new_lines.append('MAX_ACTIVE_POSITIONS=100\n')
        has_max_positions = True
    else:
        new_lines.append(line)

if not has_max_positions:
    new_lines.append('MAX_ACTIVE_POSITIONS=100\n')

with open(p, 'w') as f:
    f.writelines(new_lines)
print("Updated .env successfully")
