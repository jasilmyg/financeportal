import re

path_js = 'static/js/main.js'
with open(path_js, 'r', encoding='utf-8') as f:
    js = f.read()

# Replace rowStyles to be professional
professional_row_styles = """  const rowStyles = {
    "Target":            { bg: "var(--bg-glass)", color: "var(--text-main)", fontWeight: "600" },
    "Achievement":       { bg: "var(--bg-glass)", color: "var(--text-main)", fontWeight: "600" },
    "Last Week Status":  { bg: "var(--bg-glass)", color: "var(--text-main)", fontWeight: "500" },
    "Last Month Status": { bg: "var(--bg-glass)", color: "var(--text-main)", fontWeight: "500" },
    "G/DG*":             { bg: "var(--bg-glass)", color: "var(--text-main)", fontWeight: "500" },
    "Qtr Trend":         { bg: "var(--bg-glass)", color: "var(--text-main)", fontWeight: "500" },
    "Qtr Trend %":       { bg: "var(--bg-glass)", color: "var(--text-main)", fontWeight: "500" },
  };"""

js = re.sub(
    r'const rowStyles = \{[\s\S]*?\};\n',
    professional_row_styles + '\n',
    js
)

# Also fix the styling of td in JS
js = re.sub(
    r'td\.style\.color\s*=\s*style\.color;',
    '',
    js
)

with open(path_js, 'w', encoding='utf-8') as f:
    f.write(js)

print("JS cleaned")
