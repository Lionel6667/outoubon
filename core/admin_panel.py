"""
Secret Admin Dashboard — password-protected analytics panel.
URL: /dashboard/otb-ctrl-9x7k/
"""
import hashlib
import json
from datetime import date, timedelta
from django.http import JsonResponse
from django.shortcuts import render, redirect
from django.utils import timezone

def _local_time(dt):
    """Convert a UTC datetime to local time (America/Port-au-Prince)."""
    if dt is None:
        return None
    try:
        return timezone.localtime(dt)
    except Exception:
        return dt


def _format_last_seen(profile, now=None):
    """Texte admin : En ligne ou dernière connexion précise."""
    if not profile or not profile.last_seen_at:
        return {'display': '—', 'online': False}
    now = now or timezone.now()
    delta = (now - profile.last_seen_at).total_seconds()
    if delta <= 15 * 60:
        return {'display': 'En ligne', 'online': True}
    local = _local_time(profile.last_seen_at)
    return {
        'display': local.strftime('%d/%m/%Y %H:%M'),
        'online': False,
    }


def _haiti_visits_qs():
    from accounts.visit_tracking import HAITI_COUNTRY
    return SiteVisit.objects.filter(country_code=HAITI_COUNTRY)


def _unique_haiti_visitors_since(start_date, end_date=None):
    qs = _haiti_visits_qs().filter(visit_date__gte=start_date)
    if end_date:
        qs = qs.filter(visit_date__lte=end_date)
    return qs.values('ip_hash').distinct().count()
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt
from django.db.models import Sum, Count, Q, F
from django.db.models.functions import TruncDate, TruncMonth, TruncWeek
from django.contrib.auth.models import User

from accounts.models import (
    UserProfile, Payment, Agent, AgentReferral, AgentWithdrawal,
    AdminMessage, AdminPanelConfig, SiteVisit, DailyUsage, XpWithdrawal,
)
from core.models import ExtraBetPost, ExtraBetAttempt, UserStats


# ─────────────── AUTH HELPERS ───────────────

def _admin_authenticated(request):
    """Check if the current session has admin panel access."""
    return request.session.get('_otb_admin_ok') is True


def _require_admin(view_func):
    """Decorator: redirect to admin login if not authenticated."""
    def wrapper(request, *args, **kwargs):
        if not _admin_authenticated(request):
            return redirect('admin_panel_login')
        return view_func(request, *args, **kwargs)
    return wrapper


# ─────────────── VIEWS ───────────────

def admin_login_view(request):
    """Login page for the admin panel. First visit = set password."""
    config = AdminPanelConfig.get_instance()
    is_first = config is None
    error = None

    if request.method == 'POST':
        password = request.POST.get('password', '')
        if is_first:
            # First time: set password
            confirm = request.POST.get('confirm', '')
            if len(password) < 6:
                error = 'Le mot de passe doit contenir au moins 6 caractères.'
            elif password != confirm:
                error = 'Les mots de passe ne correspondent pas.'
            else:
                AdminPanelConfig.set_password(password)
                request.session['_otb_admin_ok'] = True
                return redirect('admin_panel')
        else:
            if config.check_password(password):
                request.session['_otb_admin_ok'] = True
                return redirect('admin_panel')
            else:
                error = 'Mot de passe incorrect.'

    return render(request, 'core/admin_login.html', {
        'is_first': is_first,
        'error': error,
    })


@_require_admin
def admin_panel_view(request):
    """Main admin dashboard page with all analytics."""
    today = date.today()
    now = timezone.now()

    # ── Users (élèves uniquement — agents exclus) ──
    from accounts.models import Agent as _Agent
    _agent_ids = _Agent.objects.values_list('user_id', flat=True)
    _student_qs = User.objects.filter(is_active=True, is_superuser=False).exclude(id__in=_agent_ids)
    total_users = _student_qs.count()
    today_signups = _student_qs.filter(date_joined__date=today).count()
    week_signups = _student_qs.filter(date_joined__date__gte=today - timedelta(days=7)).count()
    month_signups = _student_qs.filter(date_joined__date__gte=today - timedelta(days=30)).count()

    # ── Premium ──
    premium_profiles = UserProfile.objects.filter(plan_expiration__gte=today).select_related('user')
    premium_count = premium_profiles.count()
    premium_pct = round(premium_count / max(total_users, 1) * 100, 1)

    # ── Visiteurs uniques Haïti (1 IP / jour) ──
    haiti_visits = _haiti_visits_qs()
    unique_visitors_today = haiti_visits.filter(visit_date=today).values('ip_hash').distinct().count()
    unique_visitors_week = _unique_haiti_visitors_since(today - timedelta(days=7))
    unique_visitors_month = _unique_haiti_visitors_since(today - timedelta(days=30))

    # ── Revenus (paiements confirmés uniquement) ──
    completed_payments = Payment.objects.filter(status='completed')
    rev_total = completed_payments.aggregate(s=Sum('amount'))['s'] or 0
    rev_today = completed_payments.filter(paid_at__date=today).aggregate(s=Sum('amount'))['s'] or 0
    rev_week = completed_payments.filter(paid_at__date__gte=today - timedelta(days=7)).aggregate(s=Sum('amount'))['s'] or 0
    rev_month = completed_payments.filter(paid_at__date__gte=today - timedelta(days=30)).aggregate(s=Sum('amount'))['s'] or 0

    # ── Revenue chart (paiements réels, 30 jours) ──
    rev_chart_qs = (
        completed_payments
        .filter(paid_at__date__gte=today - timedelta(days=30))
        .annotate(day=TruncDate('paid_at'))
        .values('day')
        .annotate(total=Sum('amount'))
        .order_by('day')
    )
    rev_chart_labels = []
    rev_chart_data = []
    for r in rev_chart_qs:
        if r['day']:
            rev_chart_labels.append(r['day'].strftime('%d/%m'))
            rev_chart_data.append(r['total'] or 0)

    if not rev_chart_data:
        rev_chart_labels = [today.strftime('%d/%m')]
        rev_chart_data = [0]

    # ── Signups chart (last 30 days — students only) ──
    signups_chart = list(
        _student_qs.filter(date_joined__date__gte=today - timedelta(days=30))
        .annotate(day=TruncDate('date_joined'))
        .values('day')
        .annotate(count=Count('id'))
        .order_by('day')
    )
    signup_labels = [s['day'].strftime('%d/%m') for s in signups_chart]
    signup_data = [s['count'] for s in signups_chart]

    # ── Agents ──
    agents = Agent.objects.select_related('user').all()
    total_agents = agents.count()
    total_agent_earned = agents.aggregate(s=Sum('total_earned'))['s'] or 0
    total_agent_balance = agents.aggregate(s=Sum('balance'))['s'] or 0

    # ── Withdrawals ──
    pending_withdrawals = AgentWithdrawal.objects.filter(status='pending').select_related('agent__user').order_by('-created_at')
    all_withdrawals = AgentWithdrawal.objects.select_related('agent__user').order_by('-created_at')[:50]
    total_withdrawn = AgentWithdrawal.objects.filter(status='approved').aggregate(s=Sum('amount'))['s'] or 0

    # ── Recent users (students only) ──
    recent_users = _student_qs.select_related('profile').order_by('-date_joined')[:30]

    # ── Recent payments (confirmed only) ──
    recent_payments = Payment.objects.filter(status='completed').select_related('user').order_by('-created_at')[:30]

    # ── Usage stats ──
    active_today = _student_qs.filter(profile__last_seen_at__date=today).count()
    active_week = _student_qs.filter(profile__last_seen_at__date__gte=today - timedelta(days=7)).count()
    students_online = _student_qs.filter(profile__last_seen_at__gte=now - timedelta(minutes=15)).count()
    total_chats = DailyUsage.objects.aggregate(s=Sum('chat_count'))['s'] or 0
    total_quizzes = DailyUsage.objects.aggregate(s=Sum('quiz_count'))['s'] or 0

    # ── Admin messages sent ──
    admin_msgs_count = AdminMessage.objects.count()

    # ── Extra Bète stats ──
    extra_bet_posts = ExtraBetPost.objects.count()
    extra_bet_answers = ExtraBetAttempt.objects.count()

    # ── Real-time visitors Haïti (pages, 15 min) ──
    visitors_online = haiti_visits.filter(
        visited_at__gte=now - timedelta(minutes=15)
    ).values('ip_hash').distinct().count()

    # ── Top 5 Leaderboard ──
    top_users = UserStats.objects.select_related('user', 'user__profile').order_by('-xp_total')[:5]

    # ── Popular Subjects (approximate from recent DailyUsage) ──
    # We look at the last 1000 DailyUsage entries to find which subjects are hot
    popular_subjects = []
    recent_usage = DailyUsage.objects.order_by('-id')[:200]
    subj_map = {}
    for usage in recent_usage:
        if usage.exercise_subjects:
            for s, c in usage.exercise_subjects.items():
                # c peut être un int ou un dict (structure imbriquée) — on normalise
                count = c if isinstance(c, (int, float)) else (sum(c.values()) if isinstance(c, dict) else 0)
                subj_map[s] = subj_map.get(s, 0) + int(count)
    popular_subjects = sorted(subj_map.items(), key=lambda x: x[1], reverse=True)[:5]

    # ── All users for messaging dropdown (students only) ──
    all_users = _student_qs.select_related('profile').order_by('first_name', 'username')[:500]

    context = {
        'total_users': total_users,
        'today_signups': today_signups,
        'week_signups': week_signups,
        'month_signups': month_signups,
        'premium_count': premium_count,
        'premium_pct': premium_pct,
        'unique_visitors_today': unique_visitors_today,
        'unique_visitors_week': unique_visitors_week,
        'unique_visitors_month': unique_visitors_month,
        'visitors_online': visitors_online,
        'rev_today': rev_today,
        'rev_week': rev_week,
        'rev_month': rev_month,
        'rev_total': rev_total,
        'rev_chart_labels': json.dumps(rev_chart_labels),
        'rev_chart_data': json.dumps(rev_chart_data),
        'signup_labels': json.dumps(signup_labels),
        'signup_data': json.dumps(signup_data),
        'total_agents': total_agents,
        'total_agent_earned': total_agent_earned,
        'total_agent_balance': total_agent_balance,
        'pending_withdrawals': pending_withdrawals,
        'all_withdrawals': all_withdrawals,
        'total_withdrawn': total_withdrawn,
        'recent_users': recent_users,
        'recent_payments': recent_payments,
        'active_today': active_today,
        'active_week': active_week,
        'total_chats': total_chats,
        'total_quizzes': total_quizzes,
        'admin_msgs_count': admin_msgs_count,
        'extra_bet_posts':   extra_bet_posts,
        'extra_bet_answers': extra_bet_answers,
        'students_online': students_online,
        'top_users':         top_users,
        'popular_subjects':  popular_subjects,
        'agents': agents,
        'all_users': all_users,
    }
    from core.models import SiteSpotlight
    from core.spotlights import get_home_spotlights
    context['spotlights'] = list(SiteSpotlight.objects.filter(kind=SiteSpotlight.KIND_LAUREATE)[:40])
    context['hall_live'] = get_home_spotlights()
    context['xp_withdrawals'] = list(XpWithdrawal.objects.select_related('user').all()[:40])
    context['pending_xp_withdrawals'] = list(XpWithdrawal.objects.filter(status='pending').select_related('user'))
    return render(request, 'core/admin_panel.html', context)


# ─────────────── API ENDPOINTS ───────────────

@_require_admin
def api_admin_withdrawal(request):
    """Approve or reject a withdrawal request."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST only'}, status=405)

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    wid = data.get('id')
    action = data.get('action')  # 'approve' or 'reject'
    note = data.get('note', '')

    if action not in ('approve', 'reject'):
        return JsonResponse({'error': 'Action invalide'}, status=400)

    try:
        w = AgentWithdrawal.objects.select_related('agent').get(pk=wid)
    except AgentWithdrawal.DoesNotExist:
        return JsonResponse({'error': 'Retrait introuvable'}, status=404)

    if w.status != 'pending':
        return JsonResponse({'error': 'Déjà traité'}, status=400)

    if action == 'approve':
        w.status = 'approved'
        w.note = note or 'Approuvé'
        w.save(update_fields=['status', 'note', 'updated_at'])
        # Deduct from agent balance
        agent = w.agent
        agent.balance = max(0, agent.balance - w.amount)
        agent.save(update_fields=['balance'])
    else:
        w.status = 'rejected'
        w.note = note or 'Refusé'
        w.save(update_fields=['status', 'note', 'updated_at'])

    return JsonResponse({'ok': True, 'status': w.status})


@_require_admin
def api_admin_send_message(request):
    """Send admin message to specific user or broadcast."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST only'}, status=405)

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    content = data.get('content', '').strip()
    receiver_id = data.get('receiver_id')
    broadcast = data.get('broadcast', False)

    if not content:
        return JsonResponse({'error': 'Message vide'}, status=400)

    def _personalize(text, user):
        """Replace @eleve with the user's first name."""
        name = (getattr(getattr(user, 'profile', None), 'first_name', '') or user.first_name or user.username)
        return text.replace('@eleve', name)

    if broadcast:
        from accounts.models import Agent as _AgentMsg
        _agent_ids_msg = _AgentMsg.objects.values_list('user_id', flat=True)
        users = User.objects.filter(is_active=True, is_superuser=False).exclude(id__in=_agent_ids_msg)
        for u in users:
            AdminMessage.objects.create(receiver=u, content=_personalize(content, u))
        from core.push_events import push_admin_announcement
        push_admin_announcement([u.id for u in users], content)
        return JsonResponse({'ok': True, 'count': users.count()})
    else:
        if not receiver_id:
            return JsonResponse({'error': 'receiver_id requis'}, status=400)
        try:
            receiver = User.objects.get(pk=receiver_id)
        except User.DoesNotExist:
            return JsonResponse({'error': 'Utilisateur introuvable'}, status=404)
        AdminMessage.objects.create(receiver=receiver, content=_personalize(content, receiver))
        from core.push_events import push_admin_announcement
        push_admin_announcement([receiver.id], content)
        return JsonResponse({'ok': True})


@_require_admin
def api_admin_users(request):
    """Paginated user list with search."""
    search = request.GET.get('q', '').strip()
    page = int(request.GET.get('page', 1))
    per_page = 30
    offset = (page - 1) * per_page

    from accounts.models import Agent as _AgentSearch
    _agent_ids_s = _AgentSearch.objects.values_list('user_id', flat=True)
    qs = User.objects.filter(is_superuser=False).exclude(id__in=_agent_ids_s).select_related('profile').order_by('-date_joined')

    if search:
        qs = qs.filter(
            Q(username__icontains=search) |
            Q(first_name__icontains=search) |
            Q(last_name__icontains=search) |
            Q(email__icontains=search) |
            Q(profile__phone__icontains=search)
        )

    total = qs.count()
    users = qs[offset:offset + per_page]

    results = []
    for u in users:
        p = getattr(u, 'profile', None)
        seen = _format_last_seen(p)
        results.append({
            'id': u.id,
            'username': u.username,
            'name': f"{u.first_name} {u.last_name}".strip() or u.username,
            'email': u.email,
            'phone': p.phone if p else '',
            'school': p.school if p else '',
            'serie': p.serie if p else '',
            'is_premium': p.is_premium if p else False,
            'expiration': p.plan_expiration.strftime('%d/%m/%Y') if p and p.plan_expiration else None,
            'joined': _local_time(u.date_joined).strftime('%d/%m/%Y %H:%M'),
            'last_active': seen['display'],
            'is_online': seen['online'],
        })

    return JsonResponse({
        'users': results,
        'total': total,
        'page': page,
        'pages': (total + per_page - 1) // per_page,
    })


@_require_admin
def api_admin_stats_chart(request):
    """Return chart data for different periods."""
    period = request.GET.get('period', '30')  # 7, 30, 90, 365
    try:
        days = int(period)
    except ValueError:
        days = 30

    today = date.today()
    start = today - timedelta(days=days)
    completed = Payment.objects.filter(status='completed', paid_at__date__gte=start)

    rev = list(
        completed
        .annotate(day=TruncDate('paid_at'))
        .values('day')
        .annotate(total=Sum('amount'))
        .order_by('day')
    )

    from accounts.models import Agent as _AgentChart
    signups = list(
        User.objects.filter(
            date_joined__date__gte=start,
            is_superuser=False,
            is_active=True,
        ).exclude(id__in=_AgentChart.objects.values_list('user_id', flat=True))
        .annotate(day=TruncDate('date_joined'))
        .values('day')
        .annotate(count=Count('id'))
        .order_by('day')
    )

    return JsonResponse({
        'revenue': {
            'labels': [r['day'].strftime('%d/%m') for r in rev],
            'data': [r['total'] for r in rev],
        },
        'signups': {
            'labels': [s['day'].strftime('%d/%m') for s in signups],
            'data': [s['count'] for s in signups],
        },
    })


@_require_admin
def admin_logout_view(request):
    """Logout from admin panel only."""
    request.session.pop('_otb_admin_ok', None)
    return redirect('admin_panel_login')


# ─────────────── GESTION ABONNEMENTS ───────────────

@_require_admin
def api_admin_subscription(request):
    """Activer, prolonger ou annuler l'abonnement d'un élève manuellement."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST only'}, status=405)

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    action  = data.get('action')   # 'activate_monthly' | 'activate_annual' | 'cancel'
    user_id = data.get('user_id')

    if action not in ('activate_monthly', 'activate_annual', 'cancel'):
        return JsonResponse({'error': 'Action invalide'}, status=400)

    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        return JsonResponse({'error': 'Utilisateur introuvable'}, status=404)

    from accounts.payments import _activate_subscription_and_pay_commission, PLANS
    from accounts.models import UserProfile as _UP

    profile, _ = _UP.objects.get_or_create(user=user)

    if action == 'cancel':
        profile.plan_expiration = None
        profile.save(update_fields=['plan_expiration'])
        return JsonResponse({'ok': True, 'message': f'Abonnement annulé pour {user.username}', 'is_premium': False, 'expiration': None})

    days = PLANS['monthly']['days'] if action == 'activate_monthly' else PLANS['annual']['days']
    _activate_subscription_and_pay_commission(user, days)
    profile.refresh_from_db()

    return JsonResponse({
        'ok': True,
        'message': f'Abonnement activé jusqu\'au {profile.plan_expiration.strftime("%d/%m/%Y")}',
        'is_premium': True,
        'expiration': profile.plan_expiration.strftime('%d/%m/%Y'),
    })


@_require_admin
def api_admin_user_detail(request):
    """Retourne les détails complets d'un utilisateur."""
    user_id = request.GET.get('id')
    try:
        user = User.objects.select_related('profile', 'stats').get(pk=user_id)
    except User.DoesNotExist:
        return JsonResponse({'error': 'Introuvable'}, status=404)

    p = getattr(user, 'profile', None)
    s = getattr(user, 'stats', None)

    from accounts.models import AgentReferral as _AR
    referral = _AR.objects.filter(referred_user=user).select_related('agent__user').first()

    payments_qs = user.payments.filter(status='completed').order_by('-paid_at')[:10]
    payments = [{'plan': py.plan, 'amount': py.amount, 'date': _local_time(py.paid_at).strftime('%d/%m/%Y %H:%M') if py.paid_at else '-'} for py in payments_qs]

    xp = 0
    if s:
        xp = int(getattr(s, 'xp_total', 0) or 0)

    seen = _format_last_seen(p)
    return JsonResponse({
        'id': user.id,
        'username': user.username,
        'name': f"{user.first_name} {user.last_name}".strip() or user.username,
        'email': user.email,
        'phone': p.phone if p else '',
        'school': p.school if p else '',
        'serie': p.serie if p else '',
        'level': p.level if p else '',
        'is_premium': p.is_premium if p else False,
        'expiration': p.plan_expiration.strftime('%d/%m/%Y') if p and p.plan_expiration else None,
        'streak': p.streak if p else 0,
        'joined': _local_time(user.date_joined).strftime('%d/%m/%Y'),
        'last_active': seen['display'],
        'is_online': seen['online'],
        'xp': xp,
        'quiz_completes': s.quiz_completes if s else 0,
        'exercices_resolus': s.exercices_resolus if s else 0,
        'messages_envoyes': s.messages_envoyes if s else 0,
        'minutes_etude': s.minutes_etude if s else 0,
        'referred_by': referral.agent.user.username if referral else None,
        'commission_paid': referral.paid if referral else None,
        'payments': payments,
    })


@_require_admin
def api_admin_users_by_type(request):
    """Retourne la liste complète des utilisateurs filtrée par type pour l'affichage des fenêtres modales."""
    filter_type = request.GET.get('type', 'registered') # 'registered', 'premium', 'active'
    today = date.today()
    now = timezone.now()

    from accounts.models import Agent as _AgentFilter
    _agent_ids = _AgentFilter.objects.values_list('user_id', flat=True)
    qs = User.objects.filter(is_superuser=False).exclude(id__in=_agent_ids).select_related('profile').order_by('-date_joined')

    if filter_type == 'premium':
        qs = qs.filter(profile__plan_expiration__gte=today)
    elif filter_type == 'active':
        qs = qs.filter(profile__last_seen_at__gte=now - timedelta(minutes=15))

    users = qs[:200]

    results = []
    for u in users:
        p = getattr(u, 'profile', None)
        seen = _format_last_seen(p, now)
        results.append({
            'id': u.id,
            'username': u.username,
            'name': f"{u.first_name} {u.last_name}".strip() or u.username,
            'email': u.email,
            'phone': p.phone if p else '',
            'school': p.school if p else 'OU TOU BON',
            'serie': p.serie if p else '',
            'is_premium': p.is_premium if p else False,
            'joined': _local_time(u.date_joined).strftime('%d/%m/%Y'),
            'last_active': seen['display'],
            'is_online': seen['online'],
        })

    return JsonResponse({
        'users': results,
        'total': qs.count(),
        'type': filter_type,
    })


@_require_admin
def api_admin_ai_usage(request):
    """Solde DeepSeek + conso IA (aujourd'hui / 7j / période) et plus gros utilisateurs."""
    try:
        days = int(request.GET.get('days') or 30)
    except (TypeError, ValueError):
        days = 30
    from core.ai_usage import get_ai_ops_dashboard
    return JsonResponse(get_ai_ops_dashboard(days))


@_require_admin
def api_admin_xp_withdrawal(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST only'}, status=405)
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    from core.xp import settle_xp_withdrawal
    try:
        w = XpWithdrawal.objects.select_related('user').get(pk=data.get('id'))
    except XpWithdrawal.DoesNotExist:
        return JsonResponse({'error': 'Introuvable'}, status=404)
    if not settle_xp_withdrawal(w, data.get('action') or '', data.get('note') or ''):
        return JsonResponse({'error': 'Déjà traité'}, status=400)
    return JsonResponse({'ok': True})


@_require_admin
def api_admin_spotlight(request):
    from core.models import SiteSpotlight
    from core.spotlights import bust_spotlight_cache
    if request.method == 'POST':
        is_json = 'application/json' in (request.content_type or '')
        if is_json:
            # Requête JSON (toggle / delete) — lire le corps JSON sans toucher à POST
            # (sinon RawPostDataException sur les formulaires multipart).
            try:
                payload = json.loads(request.body or '{}')
            except (json.JSONDecodeError, ValueError):
                payload = {}
            action = payload.get('action') or ''
            sid = payload.get('id')
        else:
            action = request.POST.get('action') or ''
            sid = request.POST.get('id')
        if not action and (
            request.POST.get('title')
            or request.POST.get('student')
            or request.POST.get('student_email')
        ):
            action = 'create'
        if action == 'delete':
            SiteSpotlight.objects.filter(pk=sid).delete()
            bust_spotlight_cache()
            return JsonResponse({'ok': True})
        if action == 'toggle':
            s = SiteSpotlight.objects.filter(pk=sid).first()
            if not s:
                return JsonResponse({'error': 'Introuvable'}, status=404)
            s.is_published = not s.is_published
            s.save(update_fields=['is_published'])
            bust_spotlight_cache()
            return JsonResponse({'ok': True, 'is_published': s.is_published})
        # create
        kind = request.POST.get('kind') or ''
        title = (request.POST.get('title') or '').strip()

        # Compte élève à mettre en avant (email ou username) — optionnel.
        linked_user = None
        student_ref = (request.POST.get('student') or request.POST.get('student_email') or '').strip()
        if student_ref:
            from django.contrib.auth.models import User
            from django.db.models import Q
            linked_user = User.objects.filter(
                Q(email__iexact=student_ref) | Q(username__iexact=student_ref)
            ).first()
            if not linked_user:
                return JsonResponse({'error': f"Aucun compte trouvé pour « {student_ref} »."}, status=404)
            # Si aucun titre saisi, on prend le nom du compte.
            if not title:
                prof = getattr(linked_user, 'profile', None)
                full = ''
                if prof:
                    full = f"{(prof.first_name or '').strip()} {(prof.last_name or '').strip()}".strip()
                title = full or linked_user.get_full_name() or linked_user.username

        if kind != SiteSpotlight.KIND_LAUREATE or not title:
            return JsonResponse({'error': 'Le portrait manuel sert uniquement au lauréat du site (1 an). Renseigne un titre ou un compte élève.'}, status=400)
        try:
            pin_order = max(0, int(request.POST.get('pin_order') or 0))
        except (TypeError, ValueError):
            pin_order = 0
        s = SiteSpotlight(
            kind=kind,
            user=linked_user,
            title=title[:140],
            subtitle=(request.POST.get('subtitle') or '')[:180],
            school=(request.POST.get('school') or '')[:180],
            serie=(request.POST.get('serie') or '')[:40],
            score=(request.POST.get('score') or '')[:40],
            body=request.POST.get('body') or '',
            academic_year=(request.POST.get('academic_year') or '2025-2026')[:16],
            week_label=(request.POST.get('week_label') or '')[:80],
            pin_order=pin_order,
            is_published=request.POST.get('is_published') in ('1', 'on', 'true', 'True'),
        )
        if request.FILES.get('photo'):
            s.photo = request.FILES['photo']
        s.save()
        bust_spotlight_cache()
        return JsonResponse({'ok': True, 'id': s.pk})
    return JsonResponse({'error': 'POST only'}, status=405)

