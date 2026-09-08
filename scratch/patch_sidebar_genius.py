from pathlib import Path

p = Path('templates/core/_sidebar.html')
text = p.read_text(encoding='utf-8')
if 'genius_hub' in text:
    print('already patched')
    raise SystemExit(0)

needle = (
    '<a href="{% url \'examen_blanc\' %}" class="nav-item nav-item--hub-child '
    '{% if active_page == \'examen_blanc\' %}active{% endif %}">'
    '<i class="fas fa-scroll"></i> <span data-i18n="nav_examen">Examen Blanc</span></a>'
)
insert = needle + (
    '\n  <a href="{% url \'genius_hub\' %}" class="nav-item nav-item--hub-child '
    '{% if active_page == \'genius\' %}active{% endif %}">'
    '<i class="fas fa-users"></i> <span>Groupe de Génies</span></a>'
)
if needle not in text:
    raise SystemExit('needle not found')
p.write_text(text.replace(needle, insert), encoding='utf-8')
print('sidebar updated')
