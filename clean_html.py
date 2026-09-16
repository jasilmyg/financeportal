import re

path_html = 'templates/index.html'
with open(path_html, 'r', encoding='utf-8') as f:
    html = f.read()

# Remove the messy inline styles from the Q1 performance table
html = re.sub(
    r'style="vertical-align:\s*middle;\s*text-align:\s*left;\s*background:\s*var\(--bg-surface\);\s*border-right:\s*1px\s*solid\s*[^"]+"',
    'style="vertical-align: middle; text-align: left;"', html
)
html = re.sub(
    r'style="text-align:\s*center;\s*background:\s*[^;]+;\s*color:\s*[^;]+;\s*border-bottom:\s*[^;]+;\s*letter-spacing:[^"]+"',
    'style="text-align: center; letter-spacing: 0.5px; font-size: 12px; padding: 8px 12px;"', html
)
html = re.sub(
    r'style="background:\s*[^;]+;\s*color:\s*[^;]+;\s*font-size:\s*11px;\s*padding:[^"]+"',
    'style="font-size: 11px; padding: 6px 10px;"', html
)
html = re.sub(
    r'style="background:\s*var\(--grad-primary\);\s*color:\s*var\(--text-main\);\s*border-right:[^"]+"',
    'style="font-weight: 700; color: var(--text-primary); border-right: 1px solid var(--border-soft);"', html
)

# Update the cache string
html = re.sub(
    r'href="/static/css/style\.css\?v=.*?"',
    'href="/static/css/style.css?v=20260814_pro_theme"', html
)
html = re.sub(
    r'src="/static/js/main\.js\?v=.*?"',
    'src="/static/js/main.js?v=20260814_pro_theme"', html
)

with open(path_html, 'w', encoding='utf-8') as f:
    f.write(html)

print("HTML cleaned")
