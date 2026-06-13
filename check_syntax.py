import py_compile
import sys

files = [
    r"C:\Users\91907\.gemini\antigravity\scratch\solbot-private\solbot\bot.py",
    r"C:\Users\91907\.gemini\antigravity\scratch\solbot-private\solbot\db.py",
    r"C:\Users\91907\.gemini\antigravity\scratch\solbot-private\solbot\ai_tuner.py",
    r"C:\Users\91907\.gemini\antigravity\scratch\solbot-private\solbot\telegram.py"
]

all_good = True
for f in files:
    try:
        py_compile.compile(f, doraise=True)
        print(f"SUCCESS: {f}")
    except Exception as e:
        print(f"ERROR in {f}: {e}")
        all_good = False

sys.exit(0 if all_good else 1)
