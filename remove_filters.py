import re

path = 'templates/index.html'

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Remove SVG filter definitions completely
content = re.sub(r'<filter id="cyanGlow".*?</filter>', '', content, flags=re.DOTALL)
content = re.sub(r'<filter id="faintGlow".*?</filter>', '', content, flags=re.DOTALL)

# 2. Remove filter attributes from all SVG elements
content = content.replace(' filter="url(#cyanGlow)"', '')
content = content.replace(' filter="url(#faintGlow)"', '')

# 3. Remove drop-shadow and backdrop-filter from CSS
content = content.replace('filter: drop-shadow(0 0 16px #00e5ff);', '')
content = content.replace('backdrop-filter: blur(10px);', '')
content = content.replace('filter: drop-shadow(0 0 15px #00e5ff);', '')

# 4. Lighten colors slightly to compensate for the lost glow
content = content.replace('color: #00e5ff;', 'color: #88ffff;')
content = content.replace('stroke="#00e5ff"', 'stroke="#66f0ff"')

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)

print("All heavy filters removed to completely eliminate lag.")
