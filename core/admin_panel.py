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
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt
from django.db.models import Sum, Count, Q, F
from django.db.models.functions import TruncDate, TruncMonth, TruncWeek
from django.contrib.auth.models import User

from accounts.models import (
    UserProfile, Payment, Agent, AgentReferral, AgentWithdrawal,
    AdminMessage, AdminPanelConfig, SiteVisit, DailyUsage,
)


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
    premium_count = UserProfile.objects.filter(plan_expiration__gte=today).count()
    premium_pct = round(premium_count / max(total_users, 1) * 100, 1)

    # ── Visits ──
    visits_today = SiteVisit.objects.filter(visited_at__date=today).count()
    unique_today = SiteVisit.objects.filter(visited_at__date=today).values('ip_hash').distinct().count()
    visits_week = SiteVisit.objects.filter(visited_at__date__gte=today - timedelta(days=7)).count()
    visits_month = SiteVisit.objects.filter(visited_at__date__gte=today - timedelta(days=30)).count()

    # ── Revenue ──
    completed_payments = Payment.objects.filter(status='completed')
    rev_today = completed_payments.filter(paid_at__date=today).aggregate(s=Sum('amount'))['s'] or 0
    rev_week = completed_payments.filter(paid_at__date__gte=today - timedelta(days=7)).aggregate(s=Sum('amount'))['s'] or 0
    rev_month = completed_payments.filter(paid_at__date__gte=today - timedelta(days=30)).aggregate(s=Sum('amount'))['s'] or 0
    rev_total = completed_payments.aggregate(s=Sum('amount'))['s'] or 0

    # ── Revenue chart data (last 30 days) ──
    rev_chart = list(
        completed_payments
        .filter(paid_at__date__gte=today - timedelta(days=30))
        .annotate(day=TruncDate('paid_at'))
        .values('day')
        .annotate(total=Sum('amount'))
        .order_by('day')
    )
    rev_chart_labels = [r['day'].strftime('%d/%m') for r in rev_chart]
    rev_chart_data = [r['total'] for r in rev_chart]

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
    active_today = DailyUsage.objects.filter(date=today).values('user').distinct().count()
    active_week = DailyUsage.objects.filter(date__gte=today - timedelta(days=7)).values('user').distinct().count()
    total_chats = DailyUsage.objects.aggregate(s=Sum('chat_count'))['s'] or 0
    total_quizzes = DailyUsage.objects.aggregate(s=Sum('quiz_count'))['s'] or 0

    # ── Admin messages sent ──
    admin_msgs_count = AdminMessage.objects.count()

    # ── All users for messaging dropdown (students only) ──
    all_users = _student_qs.select_related('profile').order_by('first_name', 'username')[:500]

    context = {
        'total_users': total_users,
        'today_signups': today_signups,
        'week_signups': week_signups,
        'month_signups': month_signups,
        'premium_count': premium_count,
        'premium_pct': premium_pct,
        'visits_today': visits_today,
        'unique_today': unique_today,
        'visits_week': visits_week,
        'visits_month': visits_month,
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
        'agents': agents,
        'all_users': all_users,
    }
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
        return JsonResponse({'ok': True, 'count': users.count()})
    else:
        if not receiver_id:
            return JsonResponse({'error': 'receiver_id requis'}, status=400)
        try:
            receiver = User.objects.get(pk=receiver_id)
        except User.DoesNotExist:
            return JsonResponse({'error': 'Utilisateur introuvable'}, status=404)
        AdminMessage.objects.create(receiver=receiver, content=_personalize(content, receiver))
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
        results.append({
            'id': u.id,
            'username': u.username,
            'name': f"{u.first_name} {u.last_name}".strip() or u.username,
            'email': u.email,
            'phone': p.phone if p else '',
            'serie': p.serie if p else '',
            'is_premium': p.is_premium if p else False,
            'expiration': p.plan_expiration.strftime('%d/%m/%Y') if p and p.plan_expiration else None,
            'joined': u.date_joined.strftime('%d/%m/%Y %H:%M'),
            'last_active': p.last_activity.strftime('%d/%m/%Y') if p and p.last_activity else None,
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

    signups = list(
        User.objects.filter(date_joined__date__gte=start, is_superuser=False)
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
