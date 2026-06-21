"""
Management command: scrape_examhaiti
Télécharge tous les examens disponibles sur examhaiti.com et les enregistre
en PDF dans le dossier database/ pour enrichir la base de données IA.

Usage:
    python manage.py scrape_examhaiti
    python manage.py scrape_examhaiti --subject physique
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
                        if total > 25 * 1024 * 1024:  # 25 MB max en mémoire
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


def extract_exam_links(html: str, base_url: str) -> list[str]:
    """Extrait tous les liens vers des pages d'examens individuels."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        raise RuntimeError("beautifulsoup4 non installé. Lance: pip install beautifulsoup4 lxml")

    soup  = BeautifulSoup(html, 'lxml')
    links = set()
    base_domain = urlparse(base_url).netloc

    for a in soup.find_all('a', href=True):
        href = a['href'].strip()
        full = urljoin(base_url, href)
        parsed = urlparse(full)
        # Doit être sur le même domaine, chemin différent de la page catégorie
        if (parsed.netloc == base_domain
                and parsed.path not in ('/', urlparse(base_url).path)
                and not href.startswith('#')
                and base_url.rstrip('/') in full  # sous-page de la catégorie
                and full != base_url):
            links.add(full.rstrip('/'))

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

    return list(pdfs)


def extract_page_text(html: str) -> str:
    """Extrait le texte principal de la page (fallback si pas de PDF)."""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, 'lxml')
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
            r = session.get(url, headers=HEADERS, timeout=(15, 20), stream=True)
            if r.status_code != 200:
                return
            content_type = r.headers.get('content-type', '')
            max_bytes = 20 * 1024 * 1024  # 20 MB max par fichier
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
    help = 'Télécharge les examens depuis examhaiti.com vers le dossier database/'

    def add_arguments(self, parser):
        parser.add_argument(
            '--subject', default='all',
            help='Matière à scraper (physique/maths/chimie/svt/philosophie/anglais/histoire/all)'
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

    def handle(self, *args, **options):
        # Vérifier beautifulsoup4
        try:
            import bs4  # noqa
        except ImportError:
            self.stdout.write(self.style.ERROR(
                "beautifulsoup4 non installé. Lance:\n"
                "  pip install beautifulsoup4 lxml\n"
            ))
            return

        subject_filter = options['subject'].lower()
        limit          = options['limit']
        delay          = options['delay']
        pdf_timeout    = options['pdf_timeout']
        db_path        = get_db_path()

        self.stdout.write(self.style.SUCCESS(f"\n{'='*60}"))
        self.stdout.write(self.style.SUCCESS("  BacIA - Scraper ExamHaiti"))
        self.stdout.write(self.style.SUCCESS(f"  Destination : {db_path}"))
        self.stdout.write(self.style.SUCCESS(f"{'='*60}\n"))

        cats_to_scrape = (
            {k: v for k, v in CATEGORIES.items() if k == subject_filter}
            if subject_filter != 'all'
            else CATEGORIES
        )
        if not cats_to_scrape:
            self.stdout.write(self.style.ERROR(f"Matière inconnue : {subject_filter}"))
            self.stdout.write(f"Choisir parmi : {', '.join(CATEGORIES.keys())} ou all")
            return

        session = requests.Session()
        session.headers.update(HEADERS)

        total_saved = 0

        for subj_key, (cat_url, subj_label) in cats_to_scrape.items():
            self.stdout.write(self.style.WARNING(f"\n[{subj_label.upper()}] -- {cat_url}"))

            # Sous-dossier par matière
            subj_dir = db_path / f"examens_{subj_key}"
            subj_dir.mkdir(exist_ok=True)

            # Liste les examens déjà téléchargés
            existing = {f.stem.lower() for f in subj_dir.iterdir()}

            # Fetch la page catégorie
            cat_html = fetch_html(cat_url, session)
            if not cat_html:
                self.stdout.write(self.style.ERROR(f"  Impossible de récupérer {cat_url}"))
                continue

            # Extraire les liens
            exam_links = extract_exam_links(cat_html, cat_url)
            if limit:
                exam_links = exam_links[:limit]

            self.stdout.write(f"  -> {len(exam_links)} examens trouves")

            saved_count = 0
            for i, exam_url in enumerate(exam_links, 1):
                # Nom du fichier basé sur l'URL
                url_slug  = urlparse(exam_url).path.strip('/').split('/')[-1]
                file_stem = f"exam_{subj_key}_{url_slug}"

                if file_stem.lower() in existing:
                    self.stdout.write(f"  [{i:02d}/{len(exam_links)}] [SKIP] Deja telecharge : {url_slug}")
                    continue

                self.stdout.write(f"  [{i:02d}/{len(exam_links)}] >> {url_slug}")
                time.sleep(delay)

                # Fetch la page — peut être HTML ou PDF direct
                raw_content, content_type = fetch_page(exam_url, session)
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
                self.style.SUCCESS(f"\n  [DONE] {subj_label} : {saved_count} fichiers sauvegardes dans {subj_dir}")
            )

        self.stdout.write(self.style.SUCCESS(
            f"\n{'='*60}\n"
            f"  TERMINE -- {total_saved} fichiers telecharges au total\n"
            f"  Destination : {db_path}\n"
            f"{'='*60}\n"
        ))

        if total_saved > 0:
            self.stdout.write(self.style.SUCCESS(
                "  INFO: Redemarre le serveur pour charger les nouveaux fichiers.\n"
            ))
