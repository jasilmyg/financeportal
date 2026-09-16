import sys

path = 'templates/index.html'

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Simplify the cyanGlow filter to remove lag
heavy_glow = """          <filter id="cyanGlow" x="-20%" y="-20%" width="140%" height="140%">
            <feGaussianBlur stdDeviation="4" result="blur1"/>
            <feGaussianBlur stdDeviation="8" result="blur2"/>
            <feGaussianBlur stdDeviation="15" result="blur3"/>
            <feMerge>
              <feMergeNode in="blur3"/>
              <feMergeNode in="blur2"/>
              <feMergeNode in="blur1"/>
              <feMergeNode in="SourceGraphic"/>
            </feMerge>
          </filter>"""

light_glow = """          <filter id="cyanGlow" x="-20%" y="-20%" width="140%" height="140%">
            <feGaussianBlur stdDeviation="5" result="blur"/>
            <feMerge>
              <feMergeNode in="blur"/>
              <feMergeNode in="SourceGraphic"/>
            </feMerge>
          </filter>"""

content = content.replace(heavy_glow, light_glow)

# 2. Add line animation CSS
css_insert_point = '      .auth-lock-icon {'
animated_line_css = """
      .animated-line {
        stroke-dasharray: 15 20 80 40 20 15 150 40;
        animation: flow-circuit 2.5s linear infinite;
      }
      .animated-line-reverse {
        stroke-dasharray: 15 20 80 40 20 15 150 40;
        animation: flow-circuit-reverse 2.5s linear infinite;
      }
      @keyframes flow-circuit {
        from { stroke-dashoffset: 380; }
        to { stroke-dashoffset: 0; }
      }
      @keyframes flow-circuit-reverse {
        from { stroke-dashoffset: -380; }
        to { stroke-dashoffset: 0; }
      }
      """

if '.animated-line' not in content:
    content = content.replace(css_insert_point, animated_line_css + css_insert_point)

# 3. Add class to paths. 
# The left paths go outward, the right paths go outward (from right to left). 
# Wait, left paths are drawn from x=0 to center. So `dashoffset` 380 -> 0 means drawing flows from x=0 to center.
# Right paths are drawn from x=1000 to center. `dashoffset` 380 -> 0 means flowing from x=1000 to center.
# This means `animated-line` works for both!

content = content.replace('<path d="M0 160', '<path class="animated-line" d="M0 160')
content = content.replace('<path d="M0 220', '<path class="animated-line" d="M0 220')
content = content.replace('<path d="M0 300', '<path class="animated-line" d="M0 300')
content = content.replace('<path d="M0 380', '<path class="animated-line" d="M0 380')
content = content.replace('<path d="M0 450', '<path class="animated-line" d="M0 450')

content = content.replace('<path d="M1000 160', '<path class="animated-line" d="M1000 160')
content = content.replace('<path d="M1000 220', '<path class="animated-line" d="M1000 220')
content = content.replace('<path d="M1000 300', '<path class="animated-line" d="M1000 300')
content = content.replace('<path d="M1000 380', '<path class="animated-line" d="M1000 380')
content = content.replace('<path d="M1000 450', '<path class="animated-line" d="M1000 450')

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)

print("Optimized glow and added line animations.")
