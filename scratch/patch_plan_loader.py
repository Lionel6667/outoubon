from pathlib import Path
import re

p = Path('templates/core/plan.html')
t = p.read_text(encoding='utf-8')
t = re.sub(
    r"showLoader\('L'IA analyse tes faiblesses…','Génération de ton plan personnalisé en cours'\);",
    "showLoader('Analyse de tes données…','Construction de ton plan personnalisé');",
    t,
    count=1,
)
p.write_text(t, encoding='utf-8')
print('loader fixed')
