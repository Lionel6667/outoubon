from pathlib import Path

p = Path('templates/core/amis.html')
text = p.read_text(encoding='utf-8')
start = text.index('{% block extra_css %}')
end = text.index('{% endblock %}', start) + len('{% endblock %}')
replacement = (
    "{% load static %}\n"
    "{% block extra_head_links %}\n"
    "<link rel=\"stylesheet\" href=\"{% static 'css/v2/amis.css' %}\">\n"
    "{% endblock %}\n"
    "{% block extra_css %}{% endblock %}"
)
p.write_text(text[:start] + replacement + text[end:], encoding='utf-8')
print('trimmed', end - start, 'chars')
