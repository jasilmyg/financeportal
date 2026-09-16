import re

path_css = 'static/css/style.css'
with open(path_css, 'r', encoding='utf-8') as f:
    css = f.read()

# Make sure .data-card is white and professional
css = re.sub(
    r'\.data-card\s*\{[\s\S]*?margin-bottom:\s*24px;\s*\}',
    '.data-card {\n  background: #ffffff; border: 1px solid #e2e8f0;\n  border-radius: 12px; padding: 26px 28px; margin-bottom: 24px;\n  box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05), 0 2px 4px -1px rgba(0, 0, 0, 0.03);\n}',
    css
)

# Fix .table-responsive to have white bg
css = re.sub(
    r'\.table-responsive\s*\{[\s\S]*?background:\s*var\(--bg-glass\);\s*\}',
    '.table-responsive { width: 100%; overflow-x: auto; border-radius: 8px; border: 1px solid #e2e8f0; background: #ffffff; }',
    css
)

# Fix table header
css = re.sub(
    r'table\.data-table\s*th\s*\{[\s\S]*?z-index:\s*2;\s*\}',
    'table.data-table th { background: #f8fafc; color: #475569; font-weight: 600; text-transform: uppercase; font-size: 11px; letter-spacing: 0.05em; padding: 12px 16px; border-bottom: 1px solid #e2e8f0; position: sticky; top: 0; z-index: 2; }',
    css
)

# Fix table td
css = re.sub(
    r'table\.data-table\s*td\s*\{[\s\S]*?transition:\s*background\s*0\.12s;\s*\}',
    'table.data-table td { padding: 12px 16px; border-bottom: 1px solid #f1f5f9; color: var(--text-main); transition: background 0.12s; }',
    css
)

# Fix table row hover
css = re.sub(
    r'table\.data-table\s*tbody\s*tr:hover\s*\{[\s\S]*?\}',
    'table.data-table tbody tr:hover { background: #f8fafc; }',
    css
)

# Fix badges
css = re.sub(
    r'\.card-title\s*span\.badge\s*\{[\s\S]*?text-transform:\s*uppercase;\s*\}',
    '.card-title span.badge { background: #f1f5f9; border: 1px solid #cbd5e1; padding: 4px 12px; border-radius: 20px; font-size: 11px; font-weight: 600; color: #475569; letter-spacing: 0.04em; text-transform: uppercase; }',
    css
)

with open(path_css, 'w', encoding='utf-8') as f:
    f.write(css)

print("CSS cleaned")
