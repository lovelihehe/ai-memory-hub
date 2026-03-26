# Per user: encode mojibake as UTF-8, then decode as GBK
# This is the standard mojibake reversal technique

import sys
sys.stdout.reconfigure(encoding='utf-8')

# Read the file and extract mojibake
with open(r"c:\Users\11818\ai-memory-hub\src\ai_memory_hub\core\utils.py", "r", encoding="utf-8") as f:
    content = f.read()

import re
pattern = r'"([^"]+)"'
matches = re.findall(pattern, content)

seen = set()
mojibake_list = []
for m in matches:
    if m and not all(ord(c) < 128 for c in m):
        if m not in seen:
            seen.add(m)
            mojibake_list.append(m)

print("=" * 80)
print("MOJIBAKE DECODING RESULTS")
print("(Encode mojibake as UTF-8 bytes, then decode as GBK)")
print("=" * 80)
print()

# Per user instruction: encode as UTF-8, decode as GBK
results = []

for mojibake in mojibake_list:
    utf8_bytes = mojibake.encode('utf-8')
    hex_str = utf8_bytes.hex()
    
    # Encode as UTF-8 bytes, decode as GBK
    try:
        result = utf8_bytes.decode('gbk', errors='replace')
        results.append((mojibake, result, hex_str))
    except Exception as e:
        results.append((mojibake, f"ERROR: {e}", hex_str))

# Print all results
for mojibake, result, hex_str in results:
    print(f"MOJIBAKE: {mojibake!r}")
    print(f"  UTF-8 hex: {hex_str}")
    print(f"  As GBK:    {result!r}")
    print()

# Also show unique mappings
print("=" * 80)
print("UNIQUE MOJIBAKE -> GBK MAPPINGS")
print("=" * 80)
print()

seen_mb = set()
for mojibake, result, _ in results:
    if mojibake not in seen_mb:
        seen_mb.add(mojibake)
        # Format for easy replacement
        print(f'    "{mojibake}": "{result}",')
