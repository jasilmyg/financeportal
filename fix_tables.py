import re

path = 'static/css/style.css'
with open(path, 'r', encoding='utf-8') as f:
    css = f.read()

replacements = {
    r'rgba\(2,\s*8,\s*18,\s*0\.5\)': 'var(--bg-glass)',
    r'rgba\(2,\s*8,\s*18,\s*0\.9\)': 'var(--bg-surface)',
    r'rgba\(8,\s*18,\s*38,\s*0\.97\)': 'var(--bg-surface)',
    r'rgba\(8,\s*18,\s*38,\s*0\.96\)': 'var(--bg-surface)',
    r'rgba\(8,\s*18,\s*38,\s*0\.82\)': 'var(--bg-surface)',
    r'rgba\(2,\s*8,\s*16,\s*0\.82\)': 'rgba(255,255,255,0.7)',
    r'rgba\(0,\s*229,\s*255,\s*0\.04\)': 'var(--border-dim)',
    r'rgba\(0,\s*229,\s*255,\s*0\.06\)': 'var(--border-soft)',
    r'rgba\(0,\s*229,\s*255,\s*0\.10\)': 'var(--border-soft)',
    r'rgba\(255,\s*255,\s*255,\s*0\.04\)': 'var(--border-soft)',
    r'rgba\(255,\s*255,\s*255,\s*0\.08\)': 'var(--border-soft)',
    r'rgba\(0,\s*229,\s*255,\s*0\.22\)': 'var(--border-glow)',
    r'rgba\(0,\s*229,\s*255,\s*0\.18\)': 'var(--border-glow)',
    r'rgba\(0,\s*229,\s*255,\s*0\.08\)': 'var(--border-soft)',
    r'rgba\(0,\s*229,\s*255,\s*0\.02\)': 'var(--border-dim)',
    
    r'background:\s*linear-gradient\(135deg,\s*#dce8ff\s*0%,\s*#80c8ff\s*100%\);': 'color: var(--text-primary);',
    r'-webkit-background-clip:\s*text;': '',
    r'-webkit-text-fill-color:\s*transparent;': '',
    r'background-clip:\s*text;': ''
}

for pattern, repl in replacements.items():
    css = re.sub(pattern, repl, css)

# Make sure tables don't have hardcoded text colors that are invisible
# Table text color is var(--text-main) and headers var(--text-muted).
# If --text-main was changed to dark, it should be fine. But let's verify.
# Also fix any stray `#fff`
css = re.sub(r'color:\s*#fff(?:fff)?\s*;', 'color: var(--text-primary);', css, flags=re.IGNORECASE)

with open(path, 'w', encoding='utf-8') as f:
    f.write(css)

print("Swept through all remaining hardcoded dark colors and gradients.")
