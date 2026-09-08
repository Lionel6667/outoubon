"""
Management command: scrape_examhaiti
Télécharge tous les examens disponibles sur examhaiti.com et les enregistre
en PDF dans database/examens_{matiere}/ — même convention que NS4 :
    exam_{matiere}_{slug}.pdf

Usage:
    python manage.py scrape_examhaiti
    python manage.py scrape_examhaiti --subject physique
    python manage.py scrape_examhaiti --niveau 9e
    python manage.py scrape_examhaiti --niveau 9e --subject maths
    python manage.py scrape_examhaiti --subject all --limit 20
"""

import os
import re
import time
import threading
import requests
from pathlib import Path
from urllib.parse import urljoin, urlparse
from django.core.management.base import BaseCommand
from django.conf import settings


# ─── Configuration des catégories ────────────────────────────────────────────
# (url, dossier) — dossier = database/examens_{dossier}/ + préfixe exam_{dossier}_
CATEGORIES = {
    'physique':     ('https://www.examhaiti.com/physique-ns4/',           'physique'),
    'maths':        ('https://www.examhaiti.com/math-ns4/',               'maths'),
    'chimie':       ('https://www.examhaiti.com/chimie-ns4/',             'chimie'),
    'svt':          ('https://www.examhaiti.com/biologie-geologie-ns4/',  'svt'),
    'philosophie':  ('https://www.examhaiti.com/philosophie-ns4/',        'philosophie'),
    'anglais':      ('https://www.examhaiti.com/anglais-ns4/',            'anglais'),
    'histoire':     ('https://www.examhaiti.com/sciences-sociales-ns4/',  'histoire'),
    'francais':     ('https://www.examhaiti.com/creole-ns4/',             'francais'),
    'informatique': ('https://www.examhaiti.com/informatiques-ns4/',      'informatique'),
    'economie':     ('https://www.examhaiti.com/economie-ns4/',           'economie'),
    'espagnol':     ('https://www.examhaiti.com/espagnol-ns4/',           'espagnol'),
    'art':          ('https://www.examhaiti.com/art-musique-ns4/',        'art'),
}

# 9e AF — mêmes dossiers NS4 (Kreyòl → francais, Sc. sociales → histoire, Sc. exp. → svt)
CATEGORIES_9E = {
    'anglais':      ('https://www.examhaiti.com/anglais-2011-2023-9e-af/',          'anglais'),
    'francais':     ('https://www.examhaiti.com/creole-9e-af/',                     'francais'),
    'espagnol':     ('https://www.examhaiti.com/espagnol-9e-af/',                   'espagnol'),
    'francais_lg':  ('https://www.examhaiti.com/francais-9e-af/',                   'francais'),
    'maths':        ('https://www.examhaiti.com/math-9e-af/',                      'maths'),
    'histoire':     ('https://www.examhaiti.com/sciences-sociales-9e-af/',         'histoire'),
    'svt':          ('https://www.examhaiti.com/sciences-experimentales-9e-af/',  'svt'),
    'autres':       ('https://www.examhaiti.com/autres/',                           'autres'),
}

SKIP_PATH_MARKERS = (
    '/wp-json/', '/category/', '/author/', '/sponsors/', '/a-propos/',
    '/examen-9e-annee', '/examen-baccalaureat', '/concours-admission',
)

LANDING_URLS = {
    'https://www.examhaiti.com',
    'https://www.examhaiti.com/examen-9e-annee-fondamentale-haiti',
    'https://www.examhaiti.com/examen-baccalaureat-haiti',
}

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/120.0.0.0 Safari/537.36'
    ),
    'Accept-Language': 'fr-FR,fr;q=0.9,en;q=0.8',
}


def get_db_path() -> Path:
    base = Path(settings.BASE_DIR)
    # Le dossier database/ est dans le projet parent (à côté de BacIA_Django)
    for candidate in [
        base / 'database',
        base.parent / 'database',
        base / 'core' / 'database',
    ]:
        if candidate.exists():
            return candidate
    # Créer dans BASE_DIR si aucun trouvé
    (base / 'database').mkdir(exist_ok=True)
    return base / 'database'


def fetch_page(url: str, session: requests.Session, retries: int = 3, page_timeout: int = 60) -> tuple[bytes | None, str]:
    """
    Fetch avec retry + timeout total via thread. Retourne (contenu_bytes, content_type).
    Gère à la fois les pages HTML et les PDFs directs.
    """
    for attempt in range(retries):
        result = [None, '']
        def _fetch():
            try:
                r = session.get(url, headers=HEADERS, timeout=(10, 20), stream=True)
                if r.status_code != 200:
                    return
                ct = r.headers.get('content-type', '').lower()
                chunks = []
                total = 0
                for chunk in r.iter_content(chunk_size=16384):
                    if chunk:
                        chunks.append(chunk)
                        total += len(chunk)
                        if total > 40 * 1024 * 1024:  # 40 MB max en mémoire
                            break
                result[0] = b''.join(chunks)
                result[1] = ct
            except requests.RequestException:
                pass
        t = threading.Thread(target=_fetch, daemon=True)
        t.start()
        t.join(timeout=page_timeout)
        if t.is_alive():
            if attempt < retries - 1:
                time.sleep(2)
                continue
            print(f"    Timeout ({page_timeout}s) — {url}")
            return None, ''
        if result[0] is not None:
            return result[0], result[1]
        if attempt < retries - 1:
            time.sleep(2 ** attempt)
        else:
            print(f"    Echec apres {retries} essais — {url}")
    return None, ''


def fetch_html(url: str, session: requests.Session, retries: int = 3) -> str | None:
    """Fetch HTML uniquement (rétro-compatibilité)."""
    content, ct = fetch_page(url, session, retries)
    if content is None:
        return None
    if b'%PDF' in content[:10]:
        return None  # C'est un PDF, pas du HTML
    try:
        return content.decode('utf-8', errors='replace')
    except Exception:
        return None


def _soup(html: str):
    from bs4 import BeautifulSoup
    return BeautifulSoup(html, 'html.parser')


def _all_category_urls() -> set[str]:
    urls = set()
    for mapping in (CATEGORIES, CATEGORIES_9E):
        for url, _folder in mapping.values():
            urls.add(url.rstrip('/'))
    urls.update(LANDING_URLS)
    return urls


def resolve_folder_key(folder_key: str, url_slug: str) -> str:
    """Classe un examen 9e « Autres » comme NS4 (techno → informatique, art → art)."""
    if folder_key != 'autres':
        return folder_key
    slug = url_slug.lower()
    if any(w in slug for w in ('techno', 'informatique', 'computer')):
        return 'informatique'
    if any(w in slug for w in ('esthet', 'artistique', 'art', 'musique')):
        return 'art'
    return 'art'


def extract_exam_links(html: str, base_url: str) -> list[str]:
    """Extrait tous les liens vers des pages d'examens individuels."""
    try:
        soup = _soup(html)
    except ImportError:
        raise RuntimeError("beautifulsoup4 non installé. Lance: pip install beautifulsoup4")

    links = set()
    base_domain = urlparse(base_url).netloc
    cat_path = urlparse(base_url).path.rstrip('/')
    skip_urls = _all_category_urls()

    for a in soup.find_all('a', href=True):
        href = a['href'].strip()
        if not href or href.startswith('#') or href.startswith('mailto:'):
            continue
        full = urljoin(base_url, href).split('#')[0].rstrip('/')
        parsed = urlparse(full)
        if parsed.netloc != base_domain or parsed.query:
            continue
        path = parsed.path
        if any(marker in path.lower() for marker in SKIP_PATH_MARKERS):
            continue
        if full in skip_urls:
            continue
        slug = path.strip('/').split('/')[-1] if path.strip('/') else ''
        if not slug:
            continue
        is_child = bool(cat_path) and path.rstrip('/') != cat_path and path.startswith(cat_path + '/')
        text = (a.get_text() or '')
        looks_like_exam = bool(re.search(
            r'9e[-_]?a[nf]|9eme|ns4|20\d{2}', slug + ' ' + text, re.I
        ))
        if is_child or looks_like_exam:
            links.add(full)

    return sorted(links)


def find_pdf_urls(html: str, page_url: str) -> list[str]:
    """
    Cherche toutes les URLs de PDF dans une page :
    - liens directs <a href="...pdf">
    - iframes / embeds Google Docs viewer (url=...)
    - pdfviewer embeds
    - wp-content/uploads
    """
    pdfs = set()

    # 1. Liens directs .pdf
    for match in re.finditer(r'https?://[^\s"\'<>]+\.pdf', html, re.IGNORECASE):
        pdfs.add(match.group(0))

    # 2. Google Docs/Drive viewer : ?url=https://...
    for match in re.finditer(r'[?&]url=(https?://[^&"\'<>\s]+)', html):
        url = requests.utils.unquote(match.group(1))
        if url.endswith('.pdf') or 'pdf' in url.lower():
            pdfs.add(url)

    # 3. iframe src avec .pdf ou pdf viewer
    for match in re.finditer(r'src=["\']([^"\']+)["\']', html, re.IGNORECASE):
        src = match.group(1)
        if '.pdf' in src.lower() or 'pdf' in src.lower():
            full = urljoin(page_url, src)
            pdfs.add(full)

    # 4. WP embed viewer patterns
    for match in re.finditer(r'(https?://[^\s"\'<>]*wp-content/uploads/[^\s"\'<>]*\.pdf)', html, re.IGNORECASE):
        pdfs.add(match.group(1))

    for match in re.finditer(r'(https?://[^\s"\'<>]*my_exam/uploads/[^\s"\'<>]*\.pdf)', html, re.IGNORECASE):
        pdfs.add(match.group(1))

    return list(pdfs)


def extract_page_text(html: str) -> str:
    """Extrait le texte principal de la page (fallback si pas de PDF)."""
    try:
        soup = _soup(html)
        # Supprimer nav, footer, sidebar
        for tag in soup(['nav', 'footer', 'header', 'script', 'style', 'aside']):
            tag.decompose()
        # Prendre le contenu principal
        main = (
            soup.find('article') or
            soup.find('main') or
            soup.find(class_=re.compile(r'entry-content|post-content|content')) or
            soup.body
        )
        if main:
            text = main.get_text(separator='\n', strip=True)
            # Nettoyer les lignes vides multiples
            text = re.sub(r'\n{3,}', '\n\n', text)
            return text[:8000]
        return ''
    except Exception:
        return ''


def slugify(text: str) -> str:
    """Convertit un titre en nom de fichier safe."""
    text = text.lower()
    text = re.sub(r'[^\w\s-]', '', text)
    text = re.sub(r'[-\s]+', '_', text).strip('_')
    return text[:60]


def download_pdf(url: str, dest_path: Path, session: requests.Session, pdf_timeout: int = 90) -> bool:
    """Télécharge un PDF vers dest_path avec timeout total. Retourne True si succès."""
    result = [False]
    error  = [None]

    def _do_download():
        try:
            r = session.get(url, headers=HEADERS, timeout=(15, 60), stream=True, allow_redirects=True)
            if r.status_code != 200:
                return
            content_type = r.headers.get('content-type', '')
            max_bytes = 40 * 1024 * 1024  # 40 MB max par fichier
            downloaded = 0
            first_bytes_buf = b''
            with open(dest_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    if not chunk:
                        continue
                    if not first_bytes_buf:
                        first_bytes_buf = chunk[:8]
                        # Vérification magic bytes si pas clairement PDF
                        if 'pdf' not in content_type and not url.lower().endswith('.pdf'):
                            if not first_bytes_buf.startswith(b'%PDF'):
                                return
                    f.write(chunk)
                    downloaded += len(chunk)
                    if downloaded > max_bytes:
                        break  # Fichier trop gros, on coupe
            if dest_path.exists() and dest_path.stat().st_size > 1000:
                result[0] = True
        except Exception as e:
            error[0] = e

    t = threading.Thread(target=_do_download, daemon=True)
    t.start()
    t.join(timeout=pdf_timeout)
    if t.is_alive():
        # Thread toujours en vie = timeout dépassé
        if dest_path.exists():
            try:
                dest_path.unlink()
            except Exception:
                pass
        return False
    return result[0]


def save_as_text(content: str, dest_path: Path) -> bool:
    """Sauvegarde le texte comme .txt dans la db."""
    txt_path = dest_path.with_suffix('.txt')
    try:
        with open(txt_path, 'w', encoding='utf-8') as f:
            f.write(content)
        return len(content) > 100
    except Exception:
        return False


class Command(BaseCommand):
    help = 'Télécharge les examens depuis examhaiti.com vers database/examens_{matiere}/'

    def add_arguments(self, parser):
        parser.add_argument(
            '--subject', default='all',
            help='Matière à scraper (maths/anglais/francais/... ou all)'
        )
        parser.add_argument(
            '--niveau', default='ns4', choices=['ns4', '9e', 'all'],
            help='Niveau : ns4 (bac), 9e (9e AF), ou all'
        )
        parser.add_argument(
            '--limit', type=int, default=0,
            help='Nombre max de pages par catégorie (0 = tout)'
        )
        parser.add_argument(
            '--delay', type=float, default=1.5,
            help='Délai en secondes entre chaque requête (défaut: 1.5s)'
        )
        parser.add_argument(
            '--pdf-timeout', type=int, default=90,
            help='Timeout total (s) par téléchargement PDF (défaut: 90s)'
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Liste les examens trouvés sans télécharger'
        )

    def _source_categories(self, niveau: str) -> dict:
        if niveau == '9e':
            return dict(CATEGORIES_9E)
        if niveau == 'all':
            merged = dict(CATEGORIES)
            for key, val in CATEGORIES_9E.items():
                merged[f'9e_{key}'] = val
            return merged
        return dict(CATEGORIES)

    def handle(self, *args, **options):
        # Vérifier beautifulsoup4
        try:
            import bs4  # noqa
        except ImportError:
            self.stdout.write(self.style.ERROR(
                "beautifulsoup4 non installé. Lance:\n"
                "  pip install beautifulsoup4\n"
            ))
            return

        subject_filter = options['subject'].lower()
        niveau         = options['niveau']
        limit          = options['limit']
        delay          = options['delay']
        pdf_timeout    = options['pdf_timeout']
        dry_run        = options['dry_run']
        db_path        = get_db_path()

        source = self._source_categories(niveau)
        cats_to_scrape = source
        if subject_filter != 'all':
            cats_to_scrape = {
                k: v for k, v in source.items()
                if k == subject_filter
                or k.replace('9e_', '') == subject_filter
                or v[1] == subject_filter
            }
            if subject_filter in ('informatique', 'art') and niveau in ('9e', 'all'):
                for key, val in source.items():
                    if val[1] == 'autres':
                        cats_to_scrape[key] = val

        if not cats_to_scrape:
            self.stdout.write(self.style.ERROR(f"Matière inconnue : {subject_filter}"))
            keys = sorted({v[1] for v in source.values()} | set(source.keys()))
            self.stdout.write(f"Choisir parmi : {', '.join(keys)} ou all")
            return

        self.stdout.write(self.style.SUCCESS(f"\n{'='*60}"))
        self.stdout.write(self.style.SUCCESS("  BacIA - Scraper ExamHaiti"))
        self.stdout.write(self.style.SUCCESS(f"  Niveau      : {niveau}"))
        self.stdout.write(self.style.SUCCESS(f"  Destination : {db_path}"))
        if dry_run:
            self.stdout.write(self.style.WARNING("  Mode        : dry-run (pas de téléchargement)"))
        self.stdout.write(self.style.SUCCESS(f"{'='*60}\n"))

        session = requests.Session()
        session.headers.update(HEADERS)

        total_saved = 0

        for cat_key, (cat_url, folder_key) in cats_to_scrape.items():
            label = folder_key if folder_key != 'autres' else 'autres (-> info/art)'
            self.stdout.write(self.style.WARNING(f"\n[{label.upper()}] -- {cat_url}"))

            cat_html = fetch_html(cat_url, session)
            if not cat_html:
                self.stdout.write(self.style.ERROR(f"  Impossible de récupérer {cat_url}"))
                continue

            exam_links = extract_exam_links(cat_html, cat_url)
            if limit:
                exam_links = exam_links[:limit]

            self.stdout.write(f"  -> {len(exam_links)} examens trouves")

            saved_count = 0
            for i, exam_url in enumerate(exam_links, 1):
                url_slug = urlparse(exam_url).path.strip('/').split('/')[-1]
                dest_folder = resolve_folder_key(folder_key, url_slug)
                file_stem = f"exam_{dest_folder}_{url_slug}"
                subj_dir = db_path / f"examens_{dest_folder}"
                subj_dir.mkdir(exist_ok=True)
                existing = {f.stem.lower() for f in subj_dir.iterdir()}

                if dry_run:
                    self.stdout.write(
                        f"  [{i:02d}/{len(exam_links)}] {url_slug}  ->  {subj_dir.name}/{file_stem}.pdf"
                    )
                    continue

                if file_stem.lower() in existing:
                    self.stdout.write(f"  [{i:02d}/{len(exam_links)}] [SKIP] Deja telecharge : {url_slug}")
                    continue

                self.stdout.write(f"  [{i:02d}/{len(exam_links)}] >> {url_slug} -> examens_{dest_folder}/")
                time.sleep(delay)

                pdf_path = subj_dir / f"{file_stem}.pdf"
                saved = False
                if download_pdf(exam_url, pdf_path, session, pdf_timeout):
                    size_kb = pdf_path.stat().st_size // 1024
                    self.stdout.write(
                        self.style.SUCCESS(f"       [OK] PDF ({size_kb} KB) : {pdf_path.name}")
                    )
                    saved_count += 1
                    total_saved += 1
                    continue

                raw_content, content_type = fetch_page(exam_url, session, page_timeout=min(pdf_timeout, 45))
                if raw_content is None:
                    continue

                saved = False

                # ── Cas 1 : La page retourne directement un PDF ───────────────
                if b'%PDF' in raw_content[:10] or 'pdf' in content_type:
                    pdf_path = subj_dir / f"{file_stem}.pdf"
                    try:
                        with open(pdf_path, 'wb') as f:
                            f.write(raw_content)
                        if pdf_path.stat().st_size > 1000:
                            size_kb = pdf_path.stat().st_size // 1024
                            self.stdout.write(
                                self.style.SUCCESS(f"       [OK] PDF direct ({size_kb} KB) : {pdf_path.name}")
                            )
                            saved = True
                            saved_count += 1
                            total_saved += 1
                    except Exception as e:
                        self.stdout.write(f"       [ERR] Erreur ecriture PDF : {e}")
                    continue  # Passer à l'exam suivant

                # ── Cas 2 : HTML — chercher des liens PDF dans la page ────────
                try:
                    page_html = raw_content.decode('utf-8', errors='replace')
                except Exception:
                    continue

                pdf_urls = find_pdf_urls(page_html, exam_url)

                for pdf_url in pdf_urls[:3]:
                    pdf_path = subj_dir / f"{file_stem}.pdf"
                    self.stdout.write(f"       [DL] PDF trouve dans la page...")
                    if download_pdf(pdf_url, pdf_path, session, pdf_timeout):
                        size_kb = pdf_path.stat().st_size // 1024
                        self.stdout.write(
                            self.style.SUCCESS(f"       [OK] Sauvegarde ({size_kb} KB) : {pdf_path.name}")
                        )
                        saved = True
                        saved_count += 1
                        total_saved += 1
                        break
                    else:
                        if pdf_path.exists():
                            pdf_path.unlink()

                # ── Cas 3 : Fallback texte ────────────────────────────────────
                if not saved:
                    page_text = extract_page_text(page_html)
                    if page_text and len(page_text) > 200:
                        txt_path = subj_dir / f"{file_stem}.txt"
                        with open(txt_path, 'w', encoding='utf-8') as f:
                            f.write(f"SOURCE: {exam_url}\n\n{page_text}")
                        self.stdout.write(
                            self.style.SUCCESS(f"       [TXT] Texte sauvegarde : {txt_path.name}")
                        )
                        saved_count += 1
                        total_saved += 1
                    else:
                        self.stdout.write(f"       [VIDE] Contenu vide (images seulement ?)")

            self.stdout.write(
                self.style.SUCCESS(f"\n  [DONE] {label} : {saved_count} fichiers sauvegardes")
            )

        self.stdout.write(self.style.SUCCESS(
            f"\n{'='*60}\n"
            f"  TERMINE -- {total_saved} fichiers telecharges au total\n"
            f"  Destination : {db_path}\n"
            f"{'='*60}\n"
        ))

        if total_saved > 0:
            self.stdout.write(self.style.SUCCESS(
                "  INFO: Relance `python manage.py rebuild_pdf_index --force` "
                "puis `python manage.py build_subject_json --force` pour indexer.\n"
            ))
