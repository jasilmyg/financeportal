import re

path = 'static/css/style.css'
with open(path, 'r', encoding='utf-8') as f:
    css = f.read()

# Replace hardcoded backgrounds
replacements = {
    r'background:\s*#020810\s*;': 'background: var(--bg-void);',
    r'background:\s*rgba\(4,\s*8,\s*20,\s*0\.85\)\s*;': 'background: var(--bg-sidebar);',
    r'background:\s*rgba\(4,\s*10,\s*24,\s*0\.78\)\s*;': 'background: var(--bg-sidebar);',
    r'background:\s*rgba\(2,\s*8,\s*16,\s*0\.5\)\s*;': 'background: rgba(255,255,255,0.4);',
    r'background:\s*rgba\(8,\s*14,\s*28,\s*0\.65\)\s*;': 'background: var(--bg-glass);',
    r'background:\s*rgba\(8,\s*14,\s*28,\s*0\.4\)\s*;': 'background: var(--bg-glass);',
    r'border:\s*1px\s*solid\s*rgba\(255,\s*255,\s*255,\s*0\.04\)\s*;': 'border: 1px solid var(--border-soft);',
    r'border-bottom:\s*1px\s*solid\s*rgba\(255,\s*255,\s*255,\s*0\.05\)\s*;': 'border-bottom: 1px solid var(--border-soft);',
    r'border-top:\s*1px\s*solid\s*rgba\(255,\s*255,\s*255,\s*0\.05\)\s*;': 'border-top: 1px solid var(--border-soft);',
    r'border-right:\s*1px\s*solid\s*rgba\(255,\s*255,\s*255,\s*0\.05\)\s*;': 'border-right: 1px solid var(--border-soft);'
}

for pattern, repl in replacements.items():
    css = re.sub(pattern, repl, css)

# Make sure all tables and inputs also don't use hardcoded dark colors
css = re.sub(r'background:\s*rgba\(10,\s*16,\s*32,\s*0\.6\)\s*;', 'background: var(--bg-glass);', css)
css = re.sub(r'background:\s*rgba\(0,\s*0,\s*0,\s*0\.3\)\s*;', 'background: rgba(255,255,255,0.3);', css)

# Update chart axis texts (usually #5a7a9a or #8a9baa)
# Ensure table rows hover isn't hardcoded white
css = re.sub(r'background:\s*rgba\(255,\s*255,\s*255,\s*0\.03\)\s*;', 'background: rgba(0,0,0,0.03);', css)

with open(path, 'w', encoding='utf-8') as f:
    f.write(css)

print("Fixed hardcoded dark backgrounds.")
