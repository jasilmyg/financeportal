import sys

path = 'templates/index.html'
with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Stop the 3D animation from auto-starting
content = content.replace('    animate();\n  })();', '    window.start3DBackground = animate;\n  })();')

# 2. Call it from checkAuth()
auth_script_old = """            if (typeof window.startSplashLoader === 'function') {
              window.startSplashLoader();
            }"""

auth_script_new = """            if (typeof window.start3DBackground === 'function') {
              window.start3DBackground();
            }
            if (typeof window.startSplashLoader === 'function') {
              window.startSplashLoader();
            }"""

content = content.replace(auth_script_old, auth_script_new)

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)

print('Deferred 3D background animation.')
