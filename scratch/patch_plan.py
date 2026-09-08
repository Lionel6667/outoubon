from pathlib import Path

p = Path('templates/core/plan.html')
t = p.read_text(encoding='utf-8')
t = t.replace(
    '<h2><i class="fas fa-robot" style="color:var(--green)"></i> Planificateur IA personnalisé</h2>',
    '<h2><i class="fas fa-calendar-check" style="color:var(--green)"></i> Planificateur personnalisé</h2>',
)
t = t.replace(
    "L'IA combine diagnostic, quiz et maîtrise pour générer un plan semaine par semaine adapté à ta série",
    'Diagnostic, quiz, erreurs SM-2 — plan semaine par semaine pour ta série',
)
t = t.replace(
    "showLoader('L'IA analyse tes faiblesses…','Génération de ton plan personnalisé en cours')",
    "showLoader('Analyse de tes données…','Construction de ton plan personnalisé')",
)
t = t.replace(
    'pour que l\'IA crée ton planning personnalisé.',
    'pour créer ton planning personnalisé.',
)
p.write_text(t, encoding='utf-8')
print('patched')
