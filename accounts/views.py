import json as _json
import logging
import re as _re
from datetime import timedelta
from urllib.parse import urlparse

from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.contrib import messages
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import csrf_exempt
from .models import UserProfile, DiagnosticResult, School
from .forms import LoginForm, SignupStep1Form

_logger = logging.getLogger(__name__)


def _normalize_latex_text(text):
    """Fix LaTeX control chars and double-escaped backslashes before HTML rendering.

    JSON parsing converts \\t → tab, \\b → backspace, \\f → form-feed, etc.
    Some sources also store \\\\cmd (double backslash) instead of \\cmd.
    This undoes both issues so the HTML contains clean LaTeX for KaTeX.
    """
    if not isinstance(text, str) or not text:
        return text
    # 1. Restore control chars that came from JSON escape sequences
    text = text.replace('\x0c', '\\f')   # form-feed  → \f  (e.g. \frac, \forall)
    text = text.replace('\t',   '\\t')   # tab        → \t  (e.g. \theta, \times, \to)
    text = text.replace('\x08', '\\b')   # backspace  → \b  (e.g. \beta, \bar)
    text = text.replace('\r',   '\\r')   # CR         → \r  (e.g. \rightarrow, \rho)
    # 2. Collapse 2+ consecutive backslashes before LaTeX identifiers or brackets
    #    e.g. \\frac → \frac,  \\( → \(,  \\[ → \[
    text = _re.sub(r'\\{2,}(?=[a-zA-Z()\[\]{}])', r'\\', text)
    return text


def _normalize_diag_question(q):
    """Return a copy of q with LaTeX fields cleaned for HTML rendering."""
    q = dict(q)
    q['enonce']      = _normalize_latex_text(q.get('enonce') or '')
    q['options']     = [_normalize_latex_text(o) for o in (q.get('options') or [])]
    q['explication'] = _normalize_latex_text(q.get('explication') or '')
    return q


def _post_auth_redirect(user):
    if hasattr(user, 'agent'):
        return '/agent/dashboard/'
    return '/dashboard/'


def _safe_referer_next(request):
    """Return an internal path from HTTP_REFERER if it is safe for post-auth redirect."""
    raw = (request.META.get('HTTP_REFERER') or '').strip()
    if not raw:
        return ''
    try:
        parsed = urlparse(raw)
    except Exception:
        return ''

    # Accept relative referrer path or same-host absolute URL only.
    host = request.get_host()
    if parsed.scheme and parsed.netloc and parsed.netloc != host:
        return ''

    path = (parsed.path or '/').strip()
    if not path.startswith('/'):
        return ''
    if path.startswith('//'):
        return ''
    if path.startswith('/login/') or path.startswith('/signup/') or path.startswith('/logout/'):
        return ''

    query = parsed.query or ''
    return f"{path}?{query}" if query else path


def _store_referral_from_request(request):
    referral_code = (request.GET.get('ref') or '').strip().upper()
    if not referral_code:
        return None
    from .models import Agent
    agent = Agent.objects.filter(referral_code=referral_code, is_active=True).first()
    if agent:
        request.session['agent_referral_code'] = agent.referral_code
        return agent
    request.session.pop('agent_referral_code', None)
    return None


def _attach_pending_referral(request, user, contact_hint=''):
    referral_code = request.session.get('agent_referral_code')
    if not referral_code or hasattr(user, 'agent'):
        return
    from .models import Agent, AgentReferral
    agent = Agent.objects.filter(referral_code=referral_code, is_active=True).first()
    if not agent or agent.user_id == user.id:
        request.session.pop('agent_referral_code', None)
        return
    AgentReferral.objects.get_or_create(
        agent=agent,
        referred_user=user,
        defaults={
            'phone_hint': contact_hint,
            'amount': 150,
            'paid': False,
        }
    )
    request.session.pop('agent_referral_code', None)


def landing(request):
    _store_referral_from_request(request)
    if request.user.is_authenticated:
        return redirect(_post_auth_redirect(request.user))

    # Fetch top user stats for league section on landing page (Optimized)
    try:
        from core.models import UserStats
        from django.db.models import F
        
        base_stats = UserStats.objects.filter(
            user__is_staff=False, user__is_superuser=False, user__agent__isnull=True,
        ).annotate(
            xp=F('quiz_completes') * 20 + F('exercices_resolus') * 50 + F('messages_envoyes') * 5
        )
        
        total_users = base_stats.count()
        top_stat = base_stats.select_related('user', 'user__profile').order_by('-xp').first()
        
        if top_stat:
            top_user_name = getattr(top_stat.user, 'profile', None).first_name or top_stat.user.username
            top_user_xp = top_stat.xp
        else:
            top_user_name = '—'
            top_user_xp = 0
    except Exception:
        total_users = 0
        top_user_name = '—'
        top_user_xp = 0

    return render(request, 'landing.html', {
        'top_user_name': top_user_name,
        'top_user_xp':   top_user_xp,
        'total_users':   total_users,
        'league_names_fallback': [
            'Débutant','Apprenti','Curieux','Motivé','Travailleur','Persévérant',
            'Progressif','Déterminé','Ambitieux','Compétent','Confirmé','Performant',
            'Avancé','Talentueux','Expert Junior','Expert','Stratège','Élite',
            'Exceptionnel','Impressionnant','Maîtrise','Grand Expert','Professionnel',
            'Leader','Champion','Dominant','Inarrêtable','Légendaire','Mythique',
            'Maître Absolu',
        ],
    })


def login_view(request):
    next_url = (request.POST.get('next') or request.GET.get('next') or '').strip()
    ref_next = _safe_referer_next(request)
    
    if request.user.is_authenticated:
        if next_url and next_url.startswith('/') and not next_url.startswith('//'):
            return redirect(next_url)
        if ref_next:
            return redirect(ref_next)
        return redirect(_post_auth_redirect(request.user))
    error = None
    if request.method == 'POST':
        identifier = request.POST.get('identifier', '').strip()
        password   = request.POST.get('password', '')
        user_obj   = None
        if '@' in identifier:
            user_obj = User.objects.filter(email__iexact=identifier).first()
        else:
            profile = UserProfile.objects.filter(phone=identifier).first()
            if not profile and identifier and not identifier.startswith('+509'):
                profile = UserProfile.objects.filter(phone=f'+509{identifier}').first()
            if profile:
                user_obj = profile.user
        if not user_obj:
            error = 'Aucun compte trouvé avec cet email ou numéro.'
        else:
            user = authenticate(request, username=user_obj.username, password=password)
            if user:
                login(request, user)
                # Enregistrer la session active pour le middleware single-device
                if not request.session.session_key:
                    request.session.save()
                
                profile = getattr(user, 'profile', None)
                if profile:
                    profile.active_session_key = request.session.session_key
                    profile.save(update_fields=['active_session_key'])
                # Générer un nouveau token persistant à chaque connexion
                from .models import PersistentAuthToken
                PersistentAuthToken.objects.filter(user=user).delete()
                token_obj = PersistentAuthToken.objects.create(user=user)
                # Respect ?next= so users land back on the page they were trying to visit
                target = _post_auth_redirect(user)
                if next_url and next_url.startswith('/') and not next_url.startswith('//'):
                    target = next_url
                elif ref_next:
                    target = ref_next
                
                response = redirect(target)
                # Cookie longue durée (1 an) pour l'auto-login instantané
                response.set_cookie('otb_persistent_token', token_obj.token, max_age=31536000, samesite='Lax', secure=not settings.DEBUG)
                return response
            error = 'Mot de passe incorrect.'
    return render(request, 'accounts/login.html', {'error': error})

def signup_view(request):
    """Étape 1 : infos personnelles (nom, email OU téléphone, mot de passe)."""
    if request.user.is_authenticated:
        return redirect(_post_auth_redirect(request.user))

    error = None
    form_data = {}

    if request.method == 'POST':
        first_name = request.POST.get('first_name', '').strip()
        last_name  = request.POST.get('last_name', '').strip()
        contact    = request.POST.get('contact', '').strip()   # email ou téléphone
        pwd1       = request.POST.get('password1', '')
        pwd2       = request.POST.get('password2', '')
        form_data  = request.POST

        if not all([first_name, contact, pwd1]):
            error = 'Remplis tous les champs obligatoires.'
        elif pwd1 != pwd2:
            error = 'Les mots de passe ne correspondent pas.'
        elif len(pwd1) < 8:
            error = 'Le mot de passe doit contenir au moins 8 caractères.'
        else:
            # Détecter si c'est un email ou un numéro de téléphone
            is_email = '@' in contact
            if is_email:
                if User.objects.filter(email__iexact=contact).exists():
                    error = 'Cet email est déjà utilisé.'
            else:
                if UserProfile.objects.filter(phone=contact).exists():
                    error = 'Ce numéro est déjà associé à un compte.'

        if not error:
            request.session['signup_step1'] = {
                'first_name': first_name,
                'last_name':  last_name,
                'contact':    contact,
                'is_email':   '@' in contact,
                'password':   pwd1,
            }
            return redirect('signup_step2')

    return render(request, 'accounts/signup.html', {'error': error, 'form_data': form_data})


def signup_step2_view(request):
    """Étape 2 : établissement scolaire + série — stocke en session, NE crée PAS le compte."""
    if request.user.is_authenticated:
        return redirect(_post_auth_redirect(request.user))

    step1 = request.session.get('signup_step1')
    if not step1:
        return redirect('signup')

    error = None

    if request.method == 'POST':
        school_name      = request.POST.get('school_name', '').strip()
        serie            = request.POST.get('serie', '').strip()
        langue_etrangere = request.POST.get('langue_etrangere', 'anglais').strip()
        bac_target_raw   = request.POST.get('bac_target', '').strip()

        if not serie:
            error = 'Choisis ta série.'
        else:
            # LLA : anglais est la matière principale, pas de choix alternatif
            if serie == 'LLA':
                langue_etrangere = 'anglais'
            elif langue_etrangere not in ('anglais', 'espagnol'):
                langue_etrangere = 'anglais'

            # Validate bac_target (950–1900, optional)
            bac_target = None
            if bac_target_raw:
                try:
                    bac_target = int(bac_target_raw)
                    bac_target = max(950, min(1900, bac_target))
                except ValueError:
                    bac_target = None

            if school_name:
                School.objects.get_or_create(name=school_name)

            request.session['signup_step2'] = {
                'school':           school_name,
                'serie':            serie,
                'langue_etrangere': langue_etrangere,
                'bac_target':       bac_target,
            }
            return redirect('diagnostic')

    series = [
        ('SVT', '🧬', 'Sciences de la Vie et de la Terre'),
        ('SMP', '⚗️', 'Sciences Mathématiques et Physiques'),
        ('SES', '📊', 'Sciences Économiques et Sociales'),
        ('LLA', '📚', 'Lettres, Langues et Arts'),
    ]
    return render(request, 'accounts/signup_step2.html', {'error': error, 'series': series})


def school_search_view(request):
    """AJAX : recherche d'écoles existantes."""
    if request.method == 'POST':
        if not request.user.is_authenticated:
            return JsonResponse({'error': 'Connexion requise'}, status=401)
        try:
            data = _json.loads(request.body)
        except (ValueError, _json.JSONDecodeError):
            return JsonResponse({'error': 'Requête invalide.'}, status=400)
        name = data.get('name', '').strip()
        if name:
            school, created = School.objects.get_or_create(name=name)
            return JsonResponse({'name': school.name, 'created': created})
        return JsonResponse({'error': 'Nom vide'}, status=400)

    q = request.GET.get('q', '').strip()
    results = list(
        School.objects.filter(name__icontains=q).values_list('name', flat=True)[:10]
    ) if q else []
    return JsonResponse({'results': results})

def logout_view(request):
    if request.user.is_authenticated:
        from .models import PersistentAuthToken
        PersistentAuthToken.objects.filter(user=request.user).delete()
    logout(request)
    response = redirect('landing')
    response.delete_cookie('otb_persistent_token')
    return response


@login_required
def get_auth_token_view(request):
    """
    API POST endpoint — retourne le token d'authentification persistante
    pour l'utilisateur actuellement authentifié.
    Utilisé par le formulaire de login pour récupérer le token après connexion réussie.
    """
    from .models import PersistentAuthToken
    token_obj = PersistentAuthToken.objects.filter(user=request.user).first()
    if not token_obj or not token_obj.is_valid():
        return JsonResponse({'error': 'no valid token'}, status=404)

    return JsonResponse({
        'token': token_obj.token,
        'expires_at': token_obj.expires_at.isoformat(),
    })


@csrf_exempt
@require_POST
def verify_auth_token_view(request):
    """
    API POST endpoint — vérifie un token et auto-logue l'utilisateur si valide.
    Appelée par JavaScript au chargement de la page pour auto-login sans mot de passe.
    """
    data = {}
    try:
        data = _json.loads(request.body or '{}')
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}

    token = (data.get('token') or request.POST.get('token', '') or '').strip()
    if not token:
        # Don't throw 401 here, just a 400 for missing parameter to avoid console noise on auto-login attempts
        return JsonResponse({'error': 'no token'}, status=400)

    from .models import PersistentAuthToken
    from django.contrib.auth import login as auth_login
    
    token_obj = PersistentAuthToken.objects.select_related('user').filter(token=token).first()
    if not token_obj or not token_obj.is_valid():
        return JsonResponse({'error': 'invalid or expired token'}, status=401)

    # Token valide — logue l'utilisateur
    user = token_obj.user
    auth_login(request, user)
    
    # Enregistrer la session active pour le middleware single-device
    if not request.session.session_key:
        request.session.save()
        
    profile = getattr(user, 'profile', None)
    if profile:
        profile.active_session_key = request.session.session_key
        profile.save(update_fields=['active_session_key'])

    # Rolling renewal — extend token on each successful verify
    token_obj.expires_at = timezone.now() + timedelta(days=365)
    token_obj.save(update_fields=['expires_at'])
    
    # Retourner le redirectURL basé sur le type d'utilisateur
    # Respecter le paramètre 'next' pour rediriger l'utilisateur vers la page qu'il voulait visiter
    next_url = (data.get('next') or request.POST.get('next', '') or request.GET.get('next', '') or '').strip()
    if not next_url:
        next_url = _safe_referer_next(request)
    # Validate next_url: must start with / but not // (open redirect protection)
    if next_url and next_url.startswith('/') and not next_url.startswith('//'):
        redirect_url = next_url
    elif hasattr(user, 'agent'):
        redirect_url = '/agent/dashboard/'
    else:
        redirect_url = '/dashboard/'
    
    return JsonResponse({
        'ok': True,
        'redirect': redirect_url,
        'username': user.username,
        'expires_at': token_obj.expires_at.isoformat(),
    })


def complete_profile_view(request):
    """Étape post-inscription Google : école + série obligatoires, puis diagnostic."""
    if not request.user.is_authenticated:
        return redirect('/login/?next=' + request.get_full_path())

    profile, _ = UserProfile.objects.get_or_create(user=request.user)

    if request.method == 'POST':
        school = request.POST.get('school', '').strip()
        serie  = request.POST.get('serie', '').strip()
        if not serie:
            return render(request, 'accounts/complete_profile.html', {
                'error': 'Choisis ta série.',
                'profile': profile,
            })
        if school:
            School.objects.get_or_create(name=school)
        profile.school = school
        profile.serie  = serie
        profile.level  = 'Terminale'
        profile.save()

        return redirect('diagnostic')

    return render(request, 'accounts/complete_profile.html', {'profile': profile})

def _get_diag_subjects(langue_etrangere):
    lang = 'espagnol' if langue_etrangere == 'espagnol' else 'anglais'
    return ['maths', 'physique', 'chimie', 'svt', 'francais', 'philosophie', 'histoire', lang]


def diagnostic_view(request):
    """Étape 3 : diagnostic — charge les questions (générées async), crée le compte à la validation."""
    import uuid, random

    step1 = request.session.get('signup_step1')
    step2 = request.session.get('signup_step2')
    is_signup_flow = bool(step1 and step2)

    if not is_signup_flow and not request.user.is_authenticated:
        return redirect('/login/?next=' + request.get_full_path())

    # Langue étrangère choisie
    if is_signup_flow:
        langue = step2.get('langue_etrangere', 'anglais')
    else:
        try:
            langue = request.user.profile.langue_etrangere or 'anglais'
        except Exception:
            langue = 'anglais'

    subjects = _get_diag_subjects(langue)

    if request.method == 'POST':
        session_qs = request.session.get('diagnostic_qs')
        if not session_qs:
            return redirect('diagnostic')

        # Utiliser la liste de matières stockée si disponible (cohérence)
        stored_subjects = request.session.get('diagnostic_subjects')
        if stored_subjects:
            subjects = stored_subjects

        # ── Créer le compte si on est dans le flow d'inscription ──
        if is_signup_flow:
            contact     = step1['contact']
            is_email    = step1['is_email']
            first_name  = step1['first_name']
            last_name   = step1['last_name']
            password    = step1['password']
            serie       = step2['serie']
            school_name = step2.get('school', '')

            if is_email:
                existing = User.objects.filter(email__iexact=contact).first()
            else:
                existing = UserProfile.objects.filter(phone=contact).select_related('user').first()
                existing = existing.user if existing else None

            if existing:
                user = existing
            else:
                base = (contact.split('@')[0] if is_email else contact.lstrip('+').replace(' ', ''))
                username, n = base, 1
                while User.objects.filter(username=username).exists():
                    username = f"{base}{n}"; n += 1

                user = User.objects.create_user(
                    username=username,
                    email=contact if is_email else '',
                    password=password,
                    first_name=first_name,
                    last_name=last_name,
                )
                if school_name:
                    School.objects.get_or_create(name=school_name)
                UserProfile.objects.create(

                    user=user,
                    first_name=first_name,
                    last_name=last_name,
                    phone='' if is_email else contact,
                    school=school_name,
                    level='Terminale',
                    serie=serie,
                    langue_etrangere=langue,
                    bac_target=step2.get('bac_target'),
                )
                from core.models import UserStats
                UserStats.objects.get_or_create(user=user)
                _attach_pending_referral(request, user, contact)

            login(request, user)
            request.session.pop('signup_step1', None)
            request.session.pop('signup_step2', None)
        else:
            user = request.user

        # ── Sauvegarder les résultats ──
        for subj in subjects:
            subj_questions = session_qs.get(subj, [])
            total   = len(subj_questions)
            correct = 0
            for idx, q in enumerate(subj_questions):
                user_ans = request.POST.get(f'q_{subj}_{idx + 1}')
                if user_ans is not None:
                    try:
                        if int(user_ans) == int(q['reponse_correcte']):
                            correct += 1
                    except (ValueError, TypeError):
                        pass
            score = round((correct / total) * 100) if total else 0
            DiagnosticResult.objects.update_or_create(
                user=user, subject=subj,
                defaults={'score': score, 'total_asked': total or 3}
            )

        request.session.pop('diagnostic_qs', None)
        request.session.pop('diagnostic_subjects', None)
        
        # Générer le token persistant pour le nouvel utilisateur
        from .models import PersistentAuthToken
        PersistentAuthToken.objects.filter(user=user).delete()
        token_obj = PersistentAuthToken.objects.create(user=user)
        
        response = redirect(_post_auth_redirect(user))
        # Cookie longue durée (1 an) pour l'auto-login instantané
        response.set_cookie('otb_persistent_token', token_obj.token, max_age=31536000, samesite='Lax', secure=not settings.DEBUG)
        return response

    # ── GET ──
    session_qs = request.session.get('diagnostic_qs')

    if not session_qs:
        # Pas encore de questions — afficher l'écran de chargement,
        # le JS va appeler /diagnostic/generate/ en AJAX
        return render(request, 'accounts/diagnostic.html', {'generating': True})

    stored_subjects = request.session.get('diagnostic_subjects')
    if stored_subjects:
        subjects = stored_subjects

    question_sets = [
        {'subject': subj, 'questions': [_normalize_diag_question(q) for q in session_qs.get(subj, [])]}
        for subj in subjects
    ]

    return render(request, 'accounts/diagnostic.html', {
        'questions':  question_sets,
        'generating': False,
    })


def diagnostic_generate_view(request):
    """AJAX POST — prépare un diagnostic depuis la même banque que le quiz existant."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST only'}, status=405)

    from core.views import get_quiz_questions_for_user

    step1 = request.session.get('signup_step1')
    step2 = request.session.get('signup_step2')
    is_signup_flow = bool(step1 and step2)

    if not is_signup_flow and not request.user.is_authenticated:
        return JsonResponse({'error': 'not authenticated'}, status=403)

    if is_signup_flow:
        langue = step2.get('langue_etrangere', 'anglais')
    else:
        try:
            langue = request.user.profile.langue_etrangere or 'anglais'
        except Exception:
            langue = 'anglais'

    subjects = _get_diag_subjects(langue)
    quiz_user = request.user if request.user.is_authenticated else None

    session_qs = {}
    for subj in subjects:
        payload = get_quiz_questions_for_user(
            quiz_user,
            subject=subj,
            count=10,
            chapter='',
            include_review=False,
        )
        qs = payload.get('questions', [])
        if len(qs) < 10:
            fallbacks = _get_fallback_dicts(subj)
            needed = 10 - len(qs)
            qs = qs + fallbacks[:needed]
        session_qs[subj] = [_normalize_diag_question(q) for q in qs[:10]]

    request.session['diagnostic_qs']       = session_qs
    request.session['diagnostic_subjects'] = subjects
    request.session.modified = True

    return JsonResponse({'ok': True})


def _get_fallback_dicts(subject):
    """Questions de secours (difficiles) si Gemini est indisponible."""
    fallback = {
        'maths': [
            {'enonce': "Soit $f(x) = \\ln(x^2 + 1)$. Quelle est $f'(x)$ ?", 'options': ['$\\dfrac{2x}{x^2+1}$', '$\\dfrac{1}{x^2+1}$', '$\\dfrac{2}{x^2+1}$', '$\\ln(2x)$'], 'reponse_correcte': 0, 'explication': "Dérivée de $\\ln(u) = \\dfrac{u'}{u}$. Ici $u = x^2+1$ donc $u' = 2x$, d'où $f'(x) = \\dfrac{2x}{x^2+1}$.", 'sujet': 'Dérivation', 'subject': 'maths'},
            {'enonce': "$\\int_0^1 x\\,e^x\\,dx = ?$", 'options': ['$1$', '$e-1$', '$e+1$', '$2e-1$'], 'reponse_correcte': 1, 'explication': "Intégration par parties avec $u=x$, $v'=e^x$ : $\\bigl[xe^x - e^x\\bigr]_0^1 = (e - e) - (0 - 1) = 1$... soit $e - 1$.", 'sujet': 'Intégration', 'subject': 'maths'},
            {'enonce': "Suite géométrique : $u_0=3$, $q=2$. Quelle est la valeur de $u_5$ ?", 'options': ['$48$', '$96$', '$64$', '$192$'], 'reponse_correcte': 1, 'explication': "$u_n = u_0 \\cdot q^n \\Rightarrow u_5 = 3 \\times 2^5 = 3 \\times 32 = 96$.", 'sujet': 'Suites', 'subject': 'maths'},
        ],
        'physique': [
            {'enonce': "Un champ magnétique $B=0{,}5$ T exerce une force sur un fil de $L=2$ m portant $I=3$ A. Quelle est $F$ ?", 'options': ['$1{,}5$ N', '$0{,}75$ N', '$3$ N', '$6$ N'], 'reponse_correcte': 2, 'explication': "$F = BIL = 0{,}5 \\times 3 \\times 2 = 3$ N.", 'sujet': 'Magnétisme', 'subject': 'physique'},
            {'enonce': "Un corps de masse $m = 2$ kg accélère à $a = 4$ m/s². Quelle est la force résultante $F$ ?", 'options': ['$2$ N', '$4$ N', '$6$ N', '$8$ N'], 'reponse_correcte': 3, 'explication': "D'après la 2ᵉ loi de Newton : $F = ma = 2 \\times 4 = 8$ N.", 'sujet': 'Mécanique', 'subject': 'physique'},
            {'enonce': "En circuit RLC série à résonance, quelle relation est vraie ?", 'options': ['$X_L < X_C$', '$X_L = X_C$', '$X_L > X_C$', '$R = 0$'], 'reponse_correcte': 1, 'explication': "À la résonance : $X_L = X_C$, donc l'impédance est minimale $Z = R$.", 'sujet': 'Électricité', 'subject': 'physique'},
        ],
        'chimie': [
            {'enonce': "Le pH d'une solution $\\text{H}_2\\text{SO}_4$ à $0{,}01$ mol/L (totalement dissocié) est :", 'options': ['$1$', '$1{,}7$', '$2$', '$0{,}5$'], 'reponse_correcte': 1, 'explication': "$\\text{H}_2\\text{SO}_4 \\rightarrow 2\\text{H}^+$ donc $[\\text{H}^+] = 0{,}02$ mol/L, $\\text{pH} = -\\log(0{,}02) \\approx 1{,}7$.", 'sujet': 'Acides-Bases', 'subject': 'chimie'},
            {'enonce': "Nombre d'oxydation du Mn dans $\\text{MnO}_4^-$ ?", 'options': ['$+4$', '$+6$', '$+7$', '$+3$'], 'reponse_correcte': 2, 'explication': "$x + 4 \\times (-2) = -1 \\Rightarrow x = +7$.", 'sujet': 'Oxydoréduction', 'subject': 'chimie'},
            {'enonce': "Un ester est obtenu par réaction entre :", 'options': ['Alcool + cétone', 'Acide + alcool', 'Alcool + halogène', 'Acide + base'], 'reponse_correcte': 1, 'explication': "Estérification : acide carboxylique $+$ alcool $\\rightarrow$ ester $+$ eau.", 'sujet': 'Chimie orga', 'subject': 'chimie'},
        ],
        'svt': [
            {'enonce': "Lors de la méiose, la division réductrice ($2n \\rightarrow n$) se produit à quel stade ?", 'options': ['Méiose II', 'Mitose', 'Méiose I', 'Prophase II'], 'reponse_correcte': 2, 'explication': "La méiose I est la division réductrice : elle passe de $2n$ à $n$ chromosomes.", 'sujet': 'Génétique', 'subject': 'svt'},
            {'enonce': "Quel type de roche est formé par cristallisation lente du magma en profondeur ?", 'options': ['Roche volcanique', 'Roche métamorphique', 'Roche sédimentaire', 'Roche plutonique'], 'reponse_correcte': 3, 'explication': "Les roches plutoniques (ex : granite) se forment par refroidissement lent en profondeur.", 'sujet': 'Géologie', 'subject': 'svt'},
            {'enonce': "La transmission d'un caractère dominant lié à l'X : père atteint × mère saine. Les fils seront :", 'options': ['Tous atteints', 'Tous sains', '50% atteints', 'Dépend de la mère'], 'reponse_correcte': 1, 'explication': "Le père transmet son Y aux fils, pas son X — les fils ne reçoivent donc pas l'allèle dominant.", 'sujet': 'Génétique', 'subject': 'svt'},
        ],
        'francais': [
            {'enonce': "Dans *Germinal* de Zola, à quel mouvement littéraire l'œuvre appartient-elle ?", 'options': ['Romantisme', 'Réalisme', 'Naturalisme', 'Symbolisme'], 'reponse_correcte': 2, 'explication': "Zola est le chef de file du Naturalisme : déterminisme social et biologique.", 'sujet': 'Littérature', 'subject': 'francais'},
            {'enonce': "Une analepse est :", 'options': ['Un retour en arrière', 'Un saut dans le futur', 'Une pause descriptive', 'Un résumé'], 'reponse_correcte': 0, 'explication': "Analepse = flashback, retour en arrière dans la narration.", 'sujet': 'Narratologie', 'subject': 'francais'},
            {'enonce': "Quelle figure de style est : « La vie est un long fleuve tranquille » ?", 'options': ['Métonymie', 'Oxymore', 'Métaphore filée', 'Hyperbole'], 'reponse_correcte': 2, 'explication': "Comparaison implicite étendue = métaphore filée.", 'sujet': 'Figures de style', 'subject': 'francais'},
        ],
        'philosophie': [
            {'enonce': "Kant distingue « agir *par* devoir » et « agir *conformément* au devoir ». Laquelle a une valeur morale ?", 'options': ['Conformément au devoir', 'Par devoir', 'Les deux', "Ni l'un ni l'autre"], 'reponse_correcte': 1, 'explication': "Pour Kant, seule l'action motivée *par* le devoir a une valeur morale.", 'sujet': 'Éthique', 'subject': 'philosophie'},
            {'enonce': "Chez Sartre, « l'existence précède l'essence » signifie :", 'options': ["L'homme est défini avant de naître", "L'homme se définit par ses actes", 'La nature humaine est fixe', "Dieu définit l'homme"], 'reponse_correcte': 1, 'explication': "L'existentialisme sartrien : pas de nature pré-définie — l'homme se crée par ses choix.", 'sujet': 'Existentialisme', 'subject': 'philosophie'},
            {'enonce': "Pour Platon, l'allégorie de la caverne illustre principalement :", 'options': ["Le mythe de l'origine", 'La distinction apparence/réalité', 'La politique idéale', 'Le dualisme corps/âme'], 'reponse_correcte': 1, 'explication': "La caverne oppose l'opinion (*doxa*) à la vraie connaissance (*épistémé*).", 'sujet': 'Théorie de la connaissance', 'subject': 'philosophie'},
        ],
        'histoire': [
            {'enonce': "Le plan Marshall (1947) visait principalement à :", 'options': ["Reconstruire l'Europe pour contenir le communisme", "Punir l'Allemagne", "Créer l'OTAN", "Décoloniser l'Afrique"], 'reponse_correcte': 0, 'explication': "Aide économique américaine à l'Europe pour reconstruire et limiter l'influence soviétique.", 'sujet': 'Guerre Froide', 'subject': 'histoire'},
            {'enonce': "La conférence de Bandung (1955) est le symbole de :", 'options': ["La création de l'ONU", 'Le mouvement des non-alignés', 'La décolonisation africaine', "L'OTAN"], 'reponse_correcte': 1, 'explication': "Bandung = naissance du Tiers-Monde et du mouvement des non-alignés.", 'sujet': 'Décolonisation', 'subject': 'histoire'},
            {'enonce': "Quel régime est qualifié de « totalitaire » parmi ces exemples ?", 'options': ['IIIe République française', 'Monarchie britannique', 'URSS stalinienne', 'IVe République italienne'], 'reponse_correcte': 2, 'explication': "L'URSS de Staline = parti unique, culte du chef, terreur de masse, contrôle total.", 'sujet': 'Totalitarismes', 'subject': 'histoire'},
        ],
        'anglais': [
            {'enonce': "Which tense is correct: « By the time she arrived, he ___ for two hours. »", 'options': ['had been waiting', 'was waiting', 'has waited', 'waited'], 'reponse_correcte': 0, 'explication': "Past perfect continuous for an action ongoing before another past event.", 'sujet': 'Grammar', 'subject': 'anglais'},
            {'enonce': "The word *ubiquitous* means:", 'options': ['Rare', 'Present everywhere', 'Mysterious', 'Temporary'], 'reponse_correcte': 1, 'explication': "*Ubiquitous* = seeming to appear everywhere at the same time.", 'sujet': 'Vocabulary', 'subject': 'anglais'},
            {'enonce': "« I wish I had studied harder. » What does this express?", 'options': ['A future plan', 'A present habit', 'A regret about the past', 'A real condition'], 'reponse_correcte': 2, 'explication': "*I wish + past perfect* expresses regret about a past action.", 'sujet': 'Grammar', 'subject': 'anglais'},
        ],
        'espagnol': [
            {'enonce': "Completa : « Ayer yo ___ al mercado con mi madre. »", 'options': ['voy', 'fui', 'iré', 'iba'], 'reponse_correcte': 1, 'explication': "Pretérito indefinido de *ir*: yo **fui**. Ayer indique un temps passé ponctuel.", 'sujet': 'Conjugaison', 'subject': 'espagnol'},
            {'enonce': "¿Cuál es el sinónimo de *hermoso*?", 'options': ['Feo', 'Bello', 'Triste', 'Pequeño'], 'reponse_correcte': 1, 'explication': "*Hermoso* et *bello* sont des synonymes signifiant « beau ».", 'sujet': 'Vocabulaire', 'subject': 'espagnol'},
            {'enonce': "Elige la forma correcta del subjuntivo : « Espero que él ___ a tiempo. »", 'options': ['llega', 'llegará', 'llegue', 'llegó'], 'reponse_correcte': 2, 'explication': "Après *Espero que* on utilise le subjonctif présent : **llegue**.", 'sujet': 'Subjonctif', 'subject': 'espagnol'},
        ],
    }
    return list(fallback.get(subject, [
        {'enonce': f'Question {subject} {i+1}', 'options': ['A','B','C','D'], 'reponse_correcte': 0, 'explication': '', 'sujet': 'Général', 'subject': subject}
        for i in range(3)
    ]))


# ─────────────────────────── AGENT ───────────────────────────

from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST

def _agent_referral_link(request, agent):
    return request.build_absolute_uri(f"/?ref={agent.referral_code}")


@require_POST
def agent_register_view(request):
    """Inscription agent via le formulaire du modal landing."""
    try:
        # Nettoyer la session si elle est corrompue
        if request.session.session_key:
            try:
                request.session.save()
            except Exception:
                # Si la session est corrompue, en créer une nouvelle
                request.session.flush()
                request.session.create()

        phone    = request.POST.get('phone', '').strip()
        password = request.POST.get('password', '').strip()

        if not phone or not password:
            return JsonResponse({'error': 'Tous les champs sont requis.'}, status=400)
        if len(password) < 8:
            return JsonResponse({'error': 'Mot de passe trop court (min 8 caractères).'}, status=400)
        if not phone.startswith('+509'):
            phone = '+509' + phone

        from .models import Agent
        if Agent.objects.filter(phone=phone).exists() or UserProfile.objects.filter(phone=phone).exists():
            return JsonResponse({'error': 'Ce numéro est déjà utilisé.'}, status=400)

        base     = phone.lstrip('+').replace(' ', '')
        username = base
        n = 1
        while User.objects.filter(username=username).exists():
            username = f"{base}{n}"; n += 1

        user = User.objects.create_user(username=username, password=password)
        UserProfile.objects.create(user=user, phone=phone)
        agent = Agent.objects.create(user=user, phone=phone)

        login(request, user)
        # Même logique que login_view élève : enregistrer la session + token persistant
        if request.session.session_key:
            new_profile = getattr(user, 'profile', None)
            if new_profile:
                new_profile.active_session_key = request.session.session_key
                new_profile.save(update_fields=['active_session_key'])
        from .models import PersistentAuthToken
        PersistentAuthToken.objects.filter(user=user).delete()
        PersistentAuthToken.objects.create(user=user)
        return JsonResponse({'ok': True, 'redirect': '/agent/dashboard/'})
    
    except Exception as e:
        # En cas d'erreur de session ou autre, nettoyer et retourner une erreur générique
        try:
            request.session.flush()
        except:
            pass
        return JsonResponse({'error': 'Erreur d\'inscription. Veuillez réessayer.'}, status=500)


@require_POST
def agent_login_view(request):
    try:
        # Nettoyer la session si elle est corrompue
        if request.session.session_key:
            try:
                request.session.save()
            except Exception:
                # Si la session est corrompue, en créer une nouvelle
                request.session.flush()
                request.session.create()

        phone = request.POST.get('phone', '').strip()
        password = request.POST.get('password', '')
        if not phone or not password:
            return JsonResponse({'error': 'Tous les champs sont requis.'}, status=400)

        if not phone.startswith('+509'):
            phone = f'+509{phone}'

        profile = UserProfile.objects.filter(phone=phone).select_related('user').first()
        if not profile:
            return JsonResponse({'error': 'Identifiants incorrects.'}, status=400)

        user = authenticate(request, username=profile.user.username, password=password)
        if not user or not hasattr(user, 'agent'):
            return JsonResponse({'error': 'Identifiants incorrects.'}, status=400)

        login(request, user)
        # Même logique que login_view élève : enregistrer la session + token persistant
        if request.session.session_key:
            profile = getattr(user, 'profile', None)
            if profile:
                profile.active_session_key = request.session.session_key
                profile.save(update_fields=['active_session_key'])
        from .models import PersistentAuthToken
        PersistentAuthToken.objects.filter(user=user).delete()
        PersistentAuthToken.objects.create(user=user)
        return JsonResponse({'ok': True, 'redirect': '/agent/dashboard/'})

    except Exception as e:
        # En cas d'erreur de session ou autre, nettoyer et retourner une erreur générique
        try:
            request.session.flush()
        except:
            pass
        return JsonResponse({'error': 'Erreur de connexion. Veuillez réessayer.'}, status=500)


def agent_dashboard_view(request):
    from .models import Agent, AgentWithdrawal
    
    if not request.user.is_authenticated:
        return redirect('/?agent=1')
    
    try:
        agent = request.user.agent
    except Agent.DoesNotExist:
        return redirect('landing')
    except Exception:
        return redirect('landing')

    referral_link = _agent_referral_link(request, agent)
    referrals     = agent.referrals.select_related('referred_user').all()
    withdrawals   = agent.withdrawals.all()

    # Demande de retrait
    withdrawal_error   = None
    withdrawal_success = None
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'withdraw':
            amount  = request.POST.get('amount', '0')
            moncash = request.POST.get('moncash', '').strip()
            try:
                amount = int(amount)
            except ValueError:
                amount = 0
            if amount < 150:
                withdrawal_error = 'Montant minimum de retrait : 150G.'
            elif amount > agent.balance:
                withdrawal_error = 'Solde insuffisant.'
            elif not moncash:
                withdrawal_error = 'Entre ton numéro MonCash.'
            else:
                AgentWithdrawal.objects.create(agent=agent, amount=amount, moncash=moncash)
                agent.balance -= amount
                agent.save(update_fields=['balance'])
                withdrawal_success = f'Demande de retrait de {amount}G enregistrée. Tu seras notifié par MonCash.'

    # Générer le QR code en data URI
    qr_data_uri = ''
    try:
        import qrcode
        import io
        import base64
        qr = qrcode.QRCode(version=1, box_size=6, border=2, error_correction=qrcode.constants.ERROR_CORRECT_M)
        qr.add_data(referral_link)
        qr.make(fit=True)
        img = qr.make_image(fill_color='#fbbf24', back_color='#0f172a')
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        b64 = base64.b64encode(buf.getvalue()).decode('ascii')
        qr_data_uri = f'data:image/png;base64,{b64}'
    except Exception:
        pass

    return render(request, 'agent/dashboard.html', {
        'agent':              agent,
        'referral_link':      referral_link,
        'referrals':          referrals,
        'withdrawals':        withdrawals,
        'withdrawal_error':   withdrawal_error,
        'withdrawal_success': withdrawal_success,
        'qr_data_uri':        qr_data_uri,
    })


@login_required
def agent_withdrawal_status_api(request):
    """API pour vérifier le statut des dernières demandes de retrait."""
    from .models import Agent, AgentWithdrawal
    try:
        agent = request.user.agent
    except Agent.DoesNotExist:
        return JsonResponse({'ok': False}, status=403)

    withdrawals = AgentWithdrawal.objects.filter(agent=agent).order_by('-created_at')[:5]
    items = []
    for w in withdrawals:
        items.append({
            'id': w.pk,
            'amount': w.amount,
            'moncash': w.moncash,
            'status': w.status,
            'note': w.note,
            'created_at': w.created_at.strftime('%d/%m/%Y %H:%M'),
            'updated_at': w.updated_at.strftime('%d/%m/%Y %H:%M'),
        })
    return JsonResponse({'ok': True, 'withdrawals': items, 'balance': agent.balance})


# ─────────────── DEVICE FINGERPRINT (1 appareil/compte) ───────────────

import json
from django.contrib.sessions.models import Session
from django.views.decorators.csrf import csrf_exempt

@login_required
@require_POST
def api_device_check(request):
    """Register or verify the device fingerprint for the logged-in user.

    POST body (JSON): { "fingerprint": "<visitorId>" }
    Rules:
    - 1 seul téléphone actif
    - Nouveau téléphone → devient "appareil candidat"
    - Ancien téléphone reçoit notification, 5 min grace period
    - Après 5 min → switch automatique
    - 1 changement tous les 15 jours
    Exception: herbyscott7@gmail.com is fully exempt — no device restriction.
    """
    # ── Exempt account: skip all device logic, no banner, no lock ──
    if request.user.email == 'herbyscott7@gmail.com':
        return JsonResponse({'ok': True, 'device_changed': False, 'exempt': True})

    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'ok': False, 'error': 'Invalid JSON'}, status=400)

    fp = (body.get('fingerprint') or '').strip()
    if not fp or len(fp) > 64:
        return JsonResponse({'ok': False, 'error': 'Missing or invalid fingerprint'}, status=400)

    profile = request.user.profile
    ua = request.META.get('HTTP_USER_AGENT', '')[:500]
    current_session_key = request.session.session_key
    now = timezone.now()

    device_changed = False
    pending_switch = False
    locked_until = None
    grace_remaining = 0

    if not profile.device_fingerprint:
        # ── First device registration ──
        profile.device_fingerprint = fp
        profile.device_user_agent = ua
        profile.last_login_device = now
        profile.active_session_key = current_session_key or ''
        profile.save(update_fields=[
            'device_fingerprint', 'device_user_agent',
            'last_login_device', 'active_session_key',
        ])
        return JsonResponse({'ok': True, 'device_changed': False})

    elif profile.device_fingerprint == fp:
        # ── Same device — just update timestamp ──
        profile.device_user_agent = ua
        profile.last_login_device = now
        profile.active_session_key = current_session_key or ''
        # Clear any pending switch if we're the active device
        if profile.pending_device_fingerprint:
            elapsed = (now - profile.pending_device_at).total_seconds() if profile.pending_device_at else 0
            if elapsed >= 300:
                # Pending device timed out, this device remains active
                pass
            else:
                # Old device still active — inform about incoming switch
                grace_remaining = max(0, 300 - int(elapsed))
        profile.save(update_fields=[
            'device_user_agent', 'last_login_device', 'active_session_key',
        ])
        return JsonResponse({
            'ok': True,
            'device_changed': False,
            'pending_switch': bool(profile.pending_device_fingerprint and grace_remaining > 0),
            'grace_remaining': grace_remaining,
        })

    else:
        # ── Different device ──
        # Check 15-day lock
        if profile.device_change_locked_until and now < profile.device_change_locked_until:
            days_left = (profile.device_change_locked_until - now).days
            return JsonResponse({
                'ok': False,
                'error': 'device_locked',
                'message': f'Changement d\'appareil bloqué pendant encore {days_left} jours.',
                'locked_until': profile.device_change_locked_until.isoformat(),
            }, status=403)

        # Check if this is already the pending device
        if profile.pending_device_fingerprint == fp:
            elapsed = (now - profile.pending_device_at).total_seconds() if profile.pending_device_at else 999
            if elapsed >= 300:
                # 5 min passed → auto-switch
                profile.device_fingerprint = fp
                profile.device_user_agent = ua
                profile.last_login_device = now
                profile.active_session_key = current_session_key or ''
                profile.pending_device_fingerprint = ''
                profile.pending_device_session_key = ''
                profile.pending_device_at = None
                profile.device_change_locked_until = now + timedelta(days=15)
                profile.save(update_fields=[
                    'device_fingerprint', 'device_user_agent',
                    'last_login_device', 'active_session_key',
                    'pending_device_fingerprint', 'pending_device_session_key',
                    'pending_device_at', 'device_change_locked_until',
                ])
                _flush_other_sessions(request.user, current_session_key)
                return JsonResponse({'ok': True, 'device_changed': True, 'switched': True})
            else:
                # Still in grace period
                return JsonResponse({
                    'ok': True,
                    'device_changed': False,
                    'pending_switch': True,
                    'grace_remaining': max(0, 300 - int(elapsed)),
                })
        else:
            # New device → set as pending
            profile.pending_device_fingerprint = fp
            profile.pending_device_session_key = current_session_key or ''
            profile.pending_device_at = now
            profile.save(update_fields=[
                'pending_device_fingerprint', 'pending_device_session_key',
                'pending_device_at',
            ])
            return JsonResponse({
                'ok': True,
                'device_changed': False,
                'pending_switch': True,
                'grace_remaining': 300,
            })


@login_required
def api_device_status(request):
    """GET: Check device switch status (called by both old and new device)."""
    profile = request.user.profile
    fp = request.GET.get('fingerprint', '')
    now = timezone.now()

    result = {
        'active_device': profile.device_fingerprint == fp,
        'pending_switch': bool(profile.pending_device_fingerprint),
        'grace_remaining': 0,
        'switched': False,
    }

    if profile.pending_device_fingerprint and profile.pending_device_at:
        elapsed = (now - profile.pending_device_at).total_seconds()
        if elapsed >= 300:
            # Auto-switch happened
            if profile.pending_device_fingerprint == fp:
                result['switched'] = True
            else:
                result['kicked'] = True
            result['grace_remaining'] = 0
        else:
            result['grace_remaining'] = max(0, 300 - int(elapsed))

    return JsonResponse(result)


def _flush_other_sessions(user, keep_session_key):
    """Delete all Django sessions for `user` except `keep_session_key`."""
    from django.contrib.sessions.backends.db import SessionStore
    for s in Session.objects.filter(expire_date__gte=timezone.now()):
        data = s.get_decoded()
        if str(data.get('_auth_user_id')) == str(user.pk):
            if s.session_key != keep_session_key:
                s.delete()
