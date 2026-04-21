with open('core/gemini.py', encoding='utf-8') as f:
    lines = f.readlines()

new_block = [
    "            # Pas de liste de questions affichees - l'IA guide a->b->c->d dans le chat\n",
    "            'questions': [],\n",
    "            '_dissertation_sujet': sujet,\n",
]

# Lines 2599-2614 (1-indexed) = indices 2598-2613 (0-indexed)
new_lines = lines[:2598] + new_block + lines[2614:]
with open('core/gemini.py', 'w', encoding='utf-8') as f:
    f.writelines(new_lines)
print('Done, lines:', len(new_lines))
