import re

# 1. Update index.html
path_html = 'templates/index.html'
with open(path_html, 'r', encoding='utf-8') as f:
    html = f.read()

replacements_html = {
    r'rgba\(30,40,70,0\.9\)': 'var(--bg-surface)',
    r'rgba\(250,235,180,0\.15\)': 'rgba(245,158,11,0.1)',
    r'rgba\(150,220,210,0\.12\)': 'rgba(14,165,233,0.1)',
    r'rgba\(250,235,180,0\.08\)': 'rgba(245,158,11,0.05)',
    r'#f5d67a': '#d97706',
    r'#6ee7d4': '#0284c7',
    r'#6998ff': '#2563eb',
    r'#34d399': '#059669',
    r'#fbbf24': '#d97706',
    r'#4facfe': '#2563eb'
}

for pattern, repl in replacements_html.items():
    html = re.sub(pattern, repl, html, flags=re.IGNORECASE)

with open(path_html, 'w', encoding='utf-8') as f:
    f.write(html)

# 2. Update main.js
path_js = 'static/js/main.js'
with open(path_js, 'r', encoding='utf-8') as f:
    js = f.read()

replacements_js = {
    r'#34d399': '#059669',
    r'#fbbf24': '#d97706',
    r'#f5d67a': '#d97706',
    r'#4facfe': '#2563eb',
    r'#b6c0f3': '#4338ca',
    r'#aec6ff': '#1d4ed8',
    r'#a3f0bb': '#059669',
    r'#c5f5d5': '#10b981',
    r'#d5f5e3': '#34d399',
    r'rgba\(255,255,255,0\.05\)': 'rgba(0,0,0,0.05)',
    r'rgba\(255,\s*255,\s*255,\s*0\.05\)': 'rgba(0,0,0,0.05)',
    r'rgba\(0,\s*229,\s*255,\s*0\.06\)': 'rgba(0,0,0,0.04)'
}

for pattern, repl in replacements_js.items():
    js = re.sub(pattern, repl, js, flags=re.IGNORECASE)

with open(path_js, 'w', encoding='utf-8') as f:
    f.write(js)

print("Fixed neon text colors for Light Theme.")
