import re

path = 'templates/index.html'

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Add CSS keyframes for GPU-accelerated rotation
css_injection = """
      @keyframes spin-svg { 100% { transform: rotate(360deg); } }
      @keyframes spin-svg-rev { 100% { transform: rotate(-360deg); } }
      .spin-slow { transform-origin: 0px 0px; animation: spin-svg 20s linear infinite; }
      .spin-slow-rev { transform-origin: 0px 0px; animation: spin-svg-rev 30s linear infinite; }
      .spin-med { transform-origin: 0px 0px; animation: spin-svg 15s linear infinite; }
"""
content = content.replace('      .animated-line {', css_injection + '      .animated-line {')

# 2. Add classes to the circles and remove <animateTransform>
c1 = '<circle cx="0" cy="0" r="160" stroke="#66f0ff" stroke-width="2" fill="none" stroke-dasharray="10 5" opacity="0.3">'
c1_new = '<circle cx="0" cy="0" r="160" stroke="#66f0ff" stroke-width="2" fill="none" stroke-dasharray="10 5" opacity="0.3" class="spin-slow">'
content = content.replace(c1 + '\n            <animateTransform attributeName="transform" type="rotate" from="0" to="360" dur="20s" repeatCount="indefinite" />\n          </circle>', c1_new + '</circle>')

c2 = '<circle cx="0" cy="0" r="140" stroke="#66f0ff" stroke-width="12" fill="none" stroke-dasharray="4 8" >'
c2_new = '<circle cx="0" cy="0" r="140" stroke="#66f0ff" stroke-width="12" fill="none" stroke-dasharray="4 8" class="spin-slow-rev">'
content = content.replace(c2 + '\n            <animateTransform attributeName="transform" type="rotate" from="360" to="0" dur="30s" repeatCount="indefinite" />\n          </circle>', c2_new + '</circle>')

c3 = '<circle cx="0" cy="0" r="100" stroke="#66f0ff" stroke-width="4" fill="none" stroke-dasharray="25 15" >'
c3_new = '<circle cx="0" cy="0" r="100" stroke="#66f0ff" stroke-width="4" fill="none" stroke-dasharray="25 15" class="spin-med">'
content = content.replace(c3 + '\n            <animateTransform attributeName="transform" type="rotate" from="0" to="360" dur="15s" repeatCount="indefinite" />\n          </circle>', c3_new + '</circle>')

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)

print("Swapped SMIL animations for GPU-accelerated CSS animations.")
