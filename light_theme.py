import sys
import re

# 1. Update CSS
css_path = 'static/css/style.css'
with open(css_path, 'r', encoding='utf-8') as f:
    css = f.read()

replacements = {
  '--bg-void:      #020810;': '--bg-void:      #f0f4f8;',
  '--bg-deep:      #050d1e;': '--bg-deep:      #ffffff;',
  '--bg-surface:   #080f20;': '--bg-surface:   #ffffff;',
  '--bg-glass:     rgba(8, 18, 38, 0.72);': '--bg-glass:     rgba(255, 255, 255, 0.85);',
  '--bg-sidebar:   rgba(4, 10, 24, 0.96);': '--bg-sidebar:   rgba(255, 255, 255, 0.96);',
  
  '--text-primary: #e8f4ff;': '--text-primary: #1a2233;',
  '--text-main:    #b8d4f0;': '--text-main:    #4a5a73;',
  '--text-muted:   #5a7a9a;': '--text-muted:   #8a9baa;',
  '--text-dim:     #2a3a50;': '--text-dim:     #b0c0d0;',
  
  '--text-dark:    #020810;': '--text-dark:    #ffffff;',
  '--text-white:   #ffffff;': '--text-white:   #1a2233;',
  
  '--glow-card:    0 8px 40px rgba(0,0,0,0.5), 0 2px 8px rgba(0,0,0,0.3);': '--glow-card:    0 4px 20px rgba(0,0,0,0.05), 0 1px 4px rgba(0,0,0,0.03);',
  
  '--border-dim:   rgba(0, 229, 255, 0.06);': '--border-dim:   rgba(0, 0, 0, 0.05);',
  '--border-soft:  rgba(0, 229, 255, 0.12);': '--border-soft:  rgba(0, 0, 0, 0.1);',
  '--border-glow:  rgba(0, 229, 255, 0.28);': '--border-glow:  rgba(0, 168, 204, 0.2);'
}

for old, new in replacements.items():
    css = css.replace(old, new)

# Fix some hardcoded #fff that should be dark in light mode
css = re.sub(r'color:\s*#fff(?:fff)?\s*;', 'color: var(--text-primary);', css, flags=re.IGNORECASE)
css = re.sub(r'color:\s*#020810\s*;', 'color: var(--text-white);', css, flags=re.IGNORECASE)
css = css.replace('border-right: 1px solid rgba(255,255,255,0.05);', 'border-right: 1px solid var(--border-soft);')

with open(css_path, 'w', encoding='utf-8') as f:
    f.write(css)


# 2. Update HTML Three.js Settings
html_path = 'templates/index.html'
with open(html_path, 'r', encoding='utf-8') as f:
    html = f.read()

# Change all additive blending to normal so it works on white backgrounds
html = html.replace('THREE.AdditiveBlending', 'THREE.NormalBlending')

# Darken the globe mesh lines
html = html.replace('color: 0x00c8e8', 'color: 0x0a335c')
html = html.replace('color: 0x003366', 'color: 0x051a30')

# Darken the network dots and lines
html = html.replace('color: 0x00e5ff', 'color: 0x0055aa')
html = html.replace('color: 0x00a0cc', 'color: 0x3377cc')
html = html.replace('color: 0x00d4f0', 'color: 0x1166aa')

# For the starfield particles, black/dark grey is better on white
html = html.replace('color: 0xffffff', 'color: 0x445566')

# Hardcoded text in HTML (like splash loader)
html = html.replace('background: #020813;', 'background: #ffffff;')

with open(html_path, 'w', encoding='utf-8') as f:
    f.write(html)

print("Switched to light theme successfully.")
