with open('django_errors.log', 'r', encoding='utf-8', errors='ignore') as f:
    lines = f.readlines()
    last_lines = lines[-100:]
    for line in last_lines:
        print(line, end='')
