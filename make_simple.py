import sys

path = 'templates/index.html'

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

# Replace the HTML
start_marker = '    <!-- Exact Design Match SVG Background & Animation -->'
end_marker = '    <div class="auth-form" style="position:absolute; bottom:15%; z-index: 10;">'

simple_html = """
    <!-- Simple Minimalist Background & Lock -->
    <div class="auth-simple-bg">
      <div class="auth-simple-pulse"></div>
      <svg class="auth-lock-icon-simple" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <rect x="3" y="11" width="18" height="11" rx="2" ry="2"></rect>
        <path d="M7 11V7a5 5 0 0 1 10 0v4"></path>
        <circle cx="12" cy="16" r="1"></circle>
      </svg>
    </div>
"""

start_idx = content.find(start_marker)
end_idx = content.find(end_marker)

if start_idx != -1 and end_idx != -1:
    content = content[:start_idx] + simple_html + '\n' + content[end_idx:]

# Add simple CSS and remove massive old CSS block
css_start_marker = '/* New Authentic Circuit Background */'
css_end_marker = '/* Auth Form */'

simple_css = """
      /* Simple Minimalist Style */
      .auth-simple-bg {
        position: absolute;
        inset: 0;
        z-index: 1;
        display: flex;
        align-items: center;
        justify-content: center;
        background: radial-gradient(circle at center, #061936 0%, #020813 60%);
      }
      .auth-simple-pulse {
        position: absolute;
        width: 160px;
        height: 160px;
        border-radius: 50%;
        background: rgba(0, 229, 255, 0.05);
        border: 2px solid rgba(0, 229, 255, 0.15);
        animation: simple-pulse 2.5s ease-in-out infinite alternate;
      }
      @keyframes simple-pulse {
        0% { transform: scale(1); opacity: 0.4; }
        100% { transform: scale(1.2); opacity: 1; box-shadow: 0 0 40px rgba(0, 229, 255, 0.2); }
      }
      .auth-lock-icon-simple {
        position: absolute;
        width: 60px;
        height: 60px;
        color: #00e5ff;
        z-index: 3;
      }
"""

css_start_idx = content.find(css_start_marker)
css_end_idx = content.find(css_end_marker)

if css_start_idx != -1 and css_end_idx != -1:
    content = content[:css_start_idx] + simple_css + '\n      ' + content[css_end_idx:]

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)

print("Switched to simple minimalist design.")
