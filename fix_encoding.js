const fs = require('fs');
const path = 'templates/index.html';

let content = fs.readFileSync(path, 'utf8');

const replacements = {
    'ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â ': '&mdash;',
    'ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢': '&trade;',
    'ÃƒÂ°Ã…Â¸Ã¢â‚¬Å“Ã‚Â¦': '&#128202;', // Bar Chart
    'ÃƒÂ°Ã…Â¸Ã¢â‚¬â„¢Ã‚Â°': '&#128176;', // Money Bag
    'ÃƒÆ’Ã¢â‚¬â€ ': '&times;',      // Multiply
    'ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¹': '&#8377;',      // Rupee
    'ÃƒÂ°Ã…Â¸Ã¢â‚¬ËœÃ‚Â¥': '&#128101;', // People Group
    'ÃƒÂ°Ã…Â¸Ã¢â‚¬Å“Ã…Â ': '&#128200;', // Chart/File
    'ÃƒÂ°Ã…Â¸Ã‚Â§Ã‚Â¹': '&#129529;',  // Broom
    'Ã¢â€šÂ¹': '&#8377;',          // Rupee alternative
    'Ã¢â‚¬â€œ': '&ndash;',
    'Ã¢â‚¬â€': '&mdash;'
};

for (const [bad, good] of Object.entries(replacements)) {
    content = content.split(bad).join(good);
}

// Fix the large corrupted comment block at the top
content = content.replace(/<!-- Ãƒ.*-->/s, '<!-- myG SPLASH LOADER -->');

fs.writeFileSync(path, content, 'utf8');
console.log('Fixed encoding artifacts successfully.');
