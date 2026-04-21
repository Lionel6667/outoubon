import re

with open('_page.html', 'r', encoding='utf-8') as f:
    html = f.read()

enonces = re.findall(r'diag-enonce[^>]*>(.*?)</div>', html, re.DOTALL)
print(f'Found {len(enonces)} enonces')

for e in enonces[:20]:
    t = e.strip()
    if '$' in t or '\\(' in t:
        print(f'TEXT: {repr(t[:120])}')
        # Show hex of first 50 bytes
        raw = t[:80].encode('utf-8')
        print(f'HEX:  {raw.hex(" ")}')
        print()
        break

# Now check: does the auth-page wrapper have anything weird?
# Check if there's a CSS rule hiding .katex
katex_css = re.findall(r'\.katex[^{]*\{[^}]*display\s*:\s*none[^}]*\}', html)
print(f'CSS hiding .katex: {katex_css}')

# Check if auth-page or auth-card has overflow:hidden or other issues
auth_styles = re.findall(r'\.auth-(?:page|card)[^{]*\{([^}]+)\}', html)
for s in auth_styles:
    if 'overflow' in s or 'display' in s:
        print(f'AUTH style: {s.strip()[:100]}')

# The REAL test: create a minimal test HTML file
test_html = '''<!DOCTYPE html>
<html>
<head>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.css">
<script src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/contrib/auto-render.min.js"></script>
</head>
<body>
<h2>KaTeX Test</h2>
<div id="test1">Formule: $U_n = 2n+3$ et $U_5 = 13$</div>
<div id="test2">Limite: \\(f(x) = x + e^x\\) quand \\(x \\to -\\infty\\)</div>
<div id="test3">Intégrale: \\(\\int_0^1 x\\,dx = \\frac{1}{2}\\)</div>
<hr>
<div id="status" style="color:red;font-weight:bold;"></div>
<script>
var status = document.getElementById('status');
if (typeof renderMathInElement === 'undefined') {
    status.textContent = 'ERREUR: renderMathInElement non chargé!';
} else {
    var opts = {
        delimiters: [
            {left: '$$', right: '$$', display: true},
            {left: '$', right: '$', display: false},
            {left: '\\\\(', right: '\\\\)', display: false},
            {left: '\\\\[', right: '\\\\]', display: true}
        ],
        throwOnError: false
    };
    try {
        renderMathInElement(document.body, opts);
        status.style.color = 'green';
        status.textContent = 'OK: renderMathInElement exécuté avec succès';
    } catch(e) {
        status.textContent = 'ERREUR: ' + e.message;
    }
}
</script>
</body>
</html>'''

with open('_katex_test.html', 'w', encoding='utf-8') as f:
    f.write(test_html)
print('\nCreated _katex_test.html — open in browser to test KaTeX independently')
