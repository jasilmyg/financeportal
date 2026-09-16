import sys

path = 'templates/index.html'

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Remove the entire lock screen div
start_marker = '  <!-- CYBERSECURITY AUTH WALL -->'
end_marker = '  <!-- myG SPLASH LOADER -->'

start_idx = content.find(start_marker)
end_idx = content.find(end_marker)

if start_idx != -1 and end_idx != -1:
    content = content[:start_idx] + content[end_idx:]

# 2. Restore immediate execution of Splash Loader
splash_start = '  window.startSplashLoader = function() {'
splash_start_repl = '  (function() {'

splash_end = '  }; // end startSplashLoader'
splash_end_repl = '  })();'

content = content.replace(splash_start, splash_start_repl)
content = content.replace(splash_end, splash_end_repl)

# 3. Restore immediate execution of 3D Background
bg_start = '    window.start3DBackground = animate;'
bg_start_repl = '    animate();'

content = content.replace(bg_start, bg_start_repl)

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)

print("Lock screen removed and animations restored to auto-start.")
