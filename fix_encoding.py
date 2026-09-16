import re

path = 'templates/index.html'

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

replacements = {
    'ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â ': '&mdash;',
    'ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢': '&trade;',
    'ÃƒÂ°Ã…Â¸Ã¢â‚¬Å“Ã‚Â¦': '&#128202;',
    'ÃƒÂ°Ã…Â¸Ã¢â‚¬â„¢Ã‚Â°': '&#128176;',
    'ÃƒÆ’Ã¢â‚¬â€ ': '&times;',
    'ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¹': '&#8377;',
    'ÃƒÂ°Ã…Â¸Ã¢â‚¬ËœÃ‚Â¥': '&#128101;',
    'ÃƒÂ°Ã…Â¸Ã¢â‚¬Å“Ã…Â ': '&#128200;',
    'ÃƒÂ°Ã…Â¸Ã‚Â§Ã‚Â¹': '&#129529;'
}

for bad, good in replacements.items():
    content = content.replace(bad, good)

# Also fix the weird comment block if it exists
content = re.sub(r'<!-- Ãƒ.*?-->', '<!-- myG SPLASH LOADER -->', content, flags=re.DOTALL)

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)

print('Fixed encoding artifacts successfully.')
