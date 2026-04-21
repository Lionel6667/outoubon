#!/usr/bin/env python
import os
import sys

def main():
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'bacia.settings')
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError("Django introuvable. Lance: pip install -r requirements.txt") from exc

    # Port par défaut 8001 pour éviter le conflit avec les autres projets sur 8000
    if 'runserver' in sys.argv:
        has_port = any(
            a.isdigit() or (':' in a and a.split(':')[-1].isdigit())
            for a in sys.argv[2:]
        )
        if not has_port:
            sys.argv.append('8001')

    execute_from_command_line(sys.argv)

if __name__ == '__main__':
    main()
