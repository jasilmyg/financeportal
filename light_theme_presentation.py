import sys
import re

path = 'presentation.html'

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

# CSS Variable Replacements for Light Theme
replacements = {
    '--bg-dark: #070a13;': '--bg-dark: #f0f4f8;',
    '--card-bg: rgba(13, 20, 38, 0.7);': '--card-bg: rgba(255, 255, 255, 0.85);',
    '--card-border: rgba(255, 255, 255, 0.06);': '--card-border: rgba(0, 0, 0, 0.1);',
    '--accent-primary: #00f2fe;': '--accent-primary: #008da8;',
    '--accent-secondary: #4facfe;': '--accent-secondary: #1e6eff;',
    '--accent-purple: #b026ff;': '--accent-purple: #7b1fa2;',
    '--accent-green: #39ff14;': '--accent-green: #00b359;',
    '--text-main: #f3f4f6;': '--text-main: #1a2233;',
    '--text-muted: #9ca3af;': '--text-muted: #5c6c7c;',
    '--grad-dark: radial-gradient(circle at 50% 50%, #0d1326 0%, #030712 100%);': '--grad-dark: radial-gradient(circle at 50% 50%, #ffffff 0%, #e2e8f0 100%);'
}

for old, new in replacements.items():
    content = content.replace(old, new)

# Hardcoded colors that need to be inverted for light mode
content = re.sub(r'color:\s*#fff(?:fff)?\s*;', 'color: var(--text-main);', content, flags=re.IGNORECASE)
content = content.replace('background: linear-gradient(to right, #ffffff, #d1d5db);', 'background: linear-gradient(to right, #1a2233, #4b5563);')

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)

print("Applied light theme to presentation.html")
