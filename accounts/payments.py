"""
Intégration paiement MonCash via MonCash Connect (api.moncashconnect.com).

Plans:
  - Mensuel:  750G / mois  (prix barré: 1000G → -25%)
  - Annuel:   500G / mois  → 6 000G facturé en une fois

API MonCash Connect :
  - POST {MONCASH_API_URL}/pay-create   body {amount, referenceId, returnUrl} → {paymentUrl}
  - GET  {MONCASH_API_URL}/pay-status?referenceId=...                          → {status, amount, completedAt}
  - Auth : header  Authorization: Bearer <MONCASH_SECRET_KEY>
"""

import hashlib
import hmac
import json
import logging
import uuid
from datetime import date, timedelta

import requests
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .models import Payment, UserProfile, GiftPaymentLink

logger = logging.getLogger(__name__)

# ── Plan configuration ──────────────────────────────────────
PLANS = {
    'monthly': {'label': 'Mensuel', 'amount': 750,  'days': 30},
    'annual':  {'label': 'Annuel',  'amount': 6000, 'days': 365},
}

# Numéro WhatsApp support (NatCash)
WHATSAPP_SUPPORT = '50936200585'
NATCASH_NUMBER   = '40615883'

# Statuts renvoyés par MonCash Connect qui signifient "payé".
_PAID_STATUSES = {'completed', 'complete', 'paid', 'success', 'successful', 'succeeded'}


def _is_paid_status(status) -> bool:
    return str(status or '').strip().lower() in _PAID_STATUSES


def _moncash_headers() -> dict:
    return {
        'Authorization': f'Bearer {settings.MONCASH_SECRET_KEY}',
        'Content-Type': 'application/json',
    }


def _moncash_create_payment(amount: int, reference_id: str, return_url: str) -> str:
    """POST /pay-create → renvoie l'URL de paiement (paymentUrl). Lève en cas d'échec."""
    resp = requests.post(
        f"{settings.MONCASH_API_URL}/pay-create",
        json={'amount': amount, 'referenceId': reference_id, 'returnUrl': return_url},
        headers=_moncash_headers(),
        timeout=15,
    )
    data = {}
    try:
        data = resp.json()
    except ValueError:
        pass
    # Accepte camelCase (nouveau) et snake_case (ancien) pour robustesse.
    return data.get('paymentUrl') or data.get('payment_url') or ''


def _moncash_get_status(reference_id: str) -> dict:
    """GET /pay-status?referenceId=... → dict {status, amount, completedAt}."""
    resp = requests.get(
        f"{settings.MONCASH_API_URL}/pay-status",
        params={'referenceId': reference_id},
        headers=_moncash_headers(),
        timeout=10,
    )
    try:
        return resp.json() or {}
    except ValueError:
        return {}


# ── Helper: activer abonnement + payer commission agent (1er mois) ──────────
def _activate_subscription_and_pay_commission(user, days: int, is_first_paid_subscription=None):
    """
    Active ou prolonge l'abonnement. Commission agent + parrainage élève
    uniquement sur le premier paiement completed.
    """
    from accounts.models import AgentReferral
    profile, _ = UserProfile.objects.get_or_create(user=user)
    today = date.today()

    if is_first_paid_subscription is None:
        is_first_subscription = not Payment.objects.filter(user=user, status='completed').exists()
    else:
        is_first_subscription = bool(is_first_paid_subscription)

    start = profile.plan_expiration if (profile.plan_expiration and profile.plan_expiration > today) else today
    profile.plan_expiration = start + timedelta(days=days)
    profile.save(update_fields=['plan_expiration'])
    try:
        from core.push_events import push_premium_activated
        push_premium_activated(user, profile.plan_expiration.strftime('%d/%m/%Y'))
    except Exception:
        pass

    # Commission agent uniquement sur le PREMIER mois
    if is_first_subscription:
        referral = AgentReferral.objects.filter(referred_user=user, paid=False).select_related('agent').first()
        if referral:
            referral.paid = True
            referral.save(update_fields=['paid'])
            agent = referral.agent
            agent.balance += referral.amount
            agent.total_earned += referral.amount
            agent.save(update_fields=['balance', 'total_earned'])
            logger.info('Commission %dG versée à agent %s pour parrainage de %s', referral.amount, agent.user.username, user.username)
        try:
            from accounts.referrals import mark_student_referral_paid
            mark_student_referral_paid(user)
        except Exception:
            logger.exception('Student referral XP settlement failed for %s', user.username)


# ─────────────────────── PAGE TARIFS ───────────────────────

def pricing_view(request):
    """Affiche la page d'abonnement avec les plans. Accessible aux visiteurs non connectés."""
    if not request.user.is_authenticated:
        # Guests see the page but payment buttons are intercepted client-side
        from core.views import _is_guest
        return render(request, 'accounts/pricing.html', {
            'profile': None,
            'is_premium': False,
            'plan_expiration': None,
            'is_guest': _is_guest(request),
            'user_authenticated': False,
        })
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    return render(request, 'accounts/pricing.html', {
        'profile': profile,
        'is_premium': profile.is_premium,
        'plan_expiration': profile.plan_expiration,
        'is_guest': False,
        'user_authenticated': True,
    })


# ─────────────────── CRÉER PAIEMENT ───────────────────────

@login_required
@require_POST
def create_payment(request):
    """Appelle MonCash Connect /pay-create et redirige vers MonCash."""
    plan_key = request.POST.get('plan', 'monthly')
    plan = PLANS.get(plan_key)
    if not plan:
        return JsonResponse({'ok': False, 'error': 'Plan invalide'}, status=400)

    ref_id = f"BACIA-{request.user.pk}-{plan_key}-{uuid.uuid4().hex[:8].upper()}"
    return_url = request.build_absolute_uri(f'/payment-success/?ref={ref_id}')

    # Sauvegarder le paiement en attente
    Payment.objects.create(
        user=request.user,
        reference_id=ref_id,
        plan=plan_key,
        amount=plan['amount'],
        status='pending',
    )

    # Appeler MonCash Connect
    try:
        payment_url = _moncash_create_payment(plan['amount'], ref_id, return_url)
    except requests.RequestException as e:
        logger.error('MonCash /pay-create error: %s', e)
        return render(request, 'accounts/payment_error.html', {
            'error': 'Erreur de connexion au service de paiement. Réessaie.',
        })

    if not payment_url:
        logger.error('MonCash no paymentUrl for %s', ref_id)
        return render(request, 'accounts/payment_error.html', {
            'error': 'Le service de paiement n\'a pas retourné de lien. Réessaie.',
        })

    return redirect(payment_url)


# ─────────────────── PAGE SUCCÈS ───────────────────────────

@login_required
def payment_success(request):
    """Page retour après paiement MonCash — en attente de confirmation webhook."""
    return render(request, 'accounts/payment_success.html')


# ─────────────────── WEBHOOK ──────────────────────────────

@csrf_exempt
@require_POST
def moncash_webhook(request):
    """Reçoit la notification MonCash Connect quand le paiement est confirmé."""
    payload = request.body

    # Vérifier la signature HMAC-SHA256 (plusieurs noms d'en-tête possibles).
    signature = (
        request.headers.get('X-Webhook-Signature')
        or request.headers.get('X-Moncash-Signature')
        or request.headers.get('X-Signature')
        or ''
    )
    secret = settings.MONCASH_WEBHOOK_SECRET or ''
    expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()

    if not secret or not hmac.compare_digest(signature, expected):
        logger.warning('Webhook signature mismatch')
        return JsonResponse({'error': 'Invalid signature'}, status=401)

    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    ref_id = data.get('referenceId') or data.get('reference_id') or ''
    status = data.get('status', '')

    if not _is_paid_status(status):
        return JsonResponse({'ok': True, 'info': 'Status noted'})

    with transaction.atomic():
        try:
            payment = Payment.objects.select_for_update().get(reference_id=ref_id)
        except Payment.DoesNotExist:
            logger.warning('Webhook for unknown ref: %s', ref_id)
            return JsonResponse({'error': 'Unknown reference'}, status=404)

        if payment.status == 'completed':
            return JsonResponse({'ok': True, 'info': 'Already processed'})

        had_prior_paid = Payment.objects.filter(
            user=payment.user, status='completed',
        ).exclude(pk=payment.pk).exists()
        payment.status = 'completed'
        payment.paid_at = timezone.now()
        payment.save(update_fields=['status', 'paid_at'])
        plan = PLANS.get(payment.plan, PLANS['monthly'])
        _activate_subscription_and_pay_commission(
            payment.user, plan['days'],
            is_first_paid_subscription=not had_prior_paid,
        )

        if payment.gift_link and not payment.gift_link.is_used:
            payment.gift_link.is_used = True
            payment.gift_link.save(update_fields=['is_used'])

    logger.info('Payment %s completed — plan until %s', ref_id, UserProfile.objects.get(user=payment.user).plan_expiration)
    return JsonResponse({'ok': True})


# ─────────────────── STATUS CHECK (optionnel) ─────────────

@login_required
def check_payment_status(request):
    """Vérifie le statut d'un paiement en cours."""
    ref_id = request.GET.get('ref', '')
    if not ref_id:
        return JsonResponse({'ok': False, 'error': 'Missing ref'}, status=400)

    try:
        payment = Payment.objects.get(reference_id=ref_id, user=request.user)
    except Payment.DoesNotExist:
        return JsonResponse({'ok': False, 'error': 'Not found'}, status=404)

    # Optionnel: interroger MonCash Connect pour mise à jour
    if payment.status == 'pending':
        try:
            data = _moncash_get_status(ref_id)
            if _is_paid_status(data.get('status')) and payment.status != 'completed':
                had_prior_paid = Payment.objects.filter(
                    user=payment.user, status='completed',
                ).exclude(pk=payment.pk).exists()
                payment.status = 'completed'
                payment.paid_at = timezone.now()
                payment.save(update_fields=['status', 'paid_at'])
                plan = PLANS.get(payment.plan, PLANS['monthly'])
                _activate_subscription_and_pay_commission(
                    payment.user, plan['days'],
                    is_first_paid_subscription=not had_prior_paid,
                )
        except requests.RequestException:
            pass

    return JsonResponse({
        'ok': True,
        'status': payment.status,
        'plan': payment.plan,
        'amount': payment.amount,
    })


# ═══════════════════ CADEAU — demander à un proche ═══════════════════

@login_required
def generate_gift_link(request):
    """Génère un lien cadeau et affiche la page de partage."""
    profile, _ = UserProfile.objects.get_or_create(user=request.user)

    # Réutiliser un lien non-utilisé s'il existe (< 7 jours)
    from datetime import timedelta as td
    recent = GiftPaymentLink.objects.filter(
        student=request.user,
        is_used=False,
        created_at__gte=timezone.now() - td(days=7),
    ).first()

    if recent:
        gift = recent
    else:
        contact = profile.phone or request.user.email or request.user.username
        gift = GiftPaymentLink.objects.create(student=request.user, student_contact=contact)

    gift_url = request.build_absolute_uri(f'/cadeau/{gift.token}/')
    student_name = profile.first_name or request.user.username

    # Message pré-écrit convaincant
    message = (
        f"Bonjour 🙏,\n\n"
        f"C'est {student_name}. Je prépare mon BAC cette année et j'utilise "
        f"*OU TOU BON*, une application d'intelligence artificielle qui m'aide "
        f"énormément dans mes révisions.\n\n"
        f"L'app me permet de :\n"
        f"📚 Avoir un tuteur IA disponible 24h/24\n"
        f"✅ Faire des quiz et exercices corrigés\n"
        f"📝 Passer des examens blancs notés\n"
        f"📖 Suivre des cours interactifs\n"
        f"🗓️ Avoir un plan de révision personnalisé\n\n"
        f"Pour continuer à l'utiliser, j'ai besoin d'un abonnement. "
        f"Ça coûte seulement *900 Gourdes par mois* (ou 6 000G pour l'année entière).\n\n"
        f"C'est un investissement dans mes études et mon avenir. "
        f"Si vous pouvez m'aider, cliquez sur ce lien pour me l'offrir :\n\n"
        f"👉 {gift_url}\n\n"
        f"Merci infiniment pour votre soutien ! 🙏❤️"
    )

    # Version WhatsApp (encodée)
    import urllib.parse
    wa_text = urllib.parse.quote(message)
    wa_url = f"https://wa.me/?text={wa_text}"

    return render(request, 'accounts/gift_share.html', {
        'profile': profile,
        'gift_url': gift_url,
        'message': message,
        'wa_url': wa_url,
        'student_name': student_name,
    })


def gift_payment_page(request, token):
    """Page publique — le proche voit les infos de l'élève et peut payer."""
    try:
        gift = GiftPaymentLink.objects.select_related('student__profile').get(token=token)
    except GiftPaymentLink.DoesNotExist:
        return render(request, 'accounts/gift_invalid.html', status=404)

    if gift.is_used:
        return render(request, 'accounts/gift_already_used.html')

    profile = getattr(gift.student, 'profile', None)
    student_name = profile.first_name if profile else gift.student.username
    school = profile.school if profile else ''
    serie = profile.get_serie_display() if profile else ''
    student_contact = gift.student_contact or gift.student.email or gift.student.username

    return render(request, 'accounts/gift_pay.html', {
        'gift': gift,
        'student_name': student_name,
        'school': school,
        'serie': serie,
        'student_contact': student_contact,
        'token': token,
    })


@require_POST
def create_gift_payment(request, token):
    """Crée le paiement MonCash pour le compte de l'élève."""
    try:
        gift = GiftPaymentLink.objects.select_related('student').get(token=token)
    except GiftPaymentLink.DoesNotExist:
        return render(request, 'accounts/gift_invalid.html', status=404)

    if gift.is_used:
        return render(request, 'accounts/gift_already_used.html')

    plan_key = request.POST.get('plan', 'monthly')
    plan = PLANS.get(plan_key)
    if not plan:
        return JsonResponse({'ok': False, 'error': 'Plan invalide'}, status=400)

    ref_id = f"GIFT-{gift.student.pk}-{plan_key}-{uuid.uuid4().hex[:8].upper()}"
    return_url = request.build_absolute_uri(f'/cadeau/{token}/merci/?ref={ref_id}')

    Payment.objects.create(
        user=gift.student,
        reference_id=ref_id,
        plan=plan_key,
        amount=plan['amount'],
        status='pending',
        gift_link=gift,
    )

    try:
        payment_url = _moncash_create_payment(plan['amount'], ref_id, return_url)
    except requests.RequestException as e:
        logger.error('MonCash gift /pay-create error: %s', e)
        return render(request, 'accounts/payment_error.html', {
            'error': 'Erreur de connexion au service de paiement. Réessayez.',
        })

    if not payment_url:
        logger.error('MonCash gift no paymentUrl for %s', ref_id)
        return render(request, 'accounts/payment_error.html', {
            'error': 'Le service de paiement n\'a pas retourné de lien. Réessayez.',
        })

    return redirect(payment_url)


def gift_success_page(request, token):
    """Page merci après paiement cadeau — avec polling."""
    try:
        gift = GiftPaymentLink.objects.select_related('student__profile').get(token=token)
    except GiftPaymentLink.DoesNotExist:
        return render(request, 'accounts/gift_invalid.html', status=404)

    profile = getattr(gift.student, 'profile', None)
    student_name = profile.first_name if profile else gift.student.username

    # Récupérer le dernier paiement lié à ce gift
    last_payment = Payment.objects.filter(gift_link=gift).order_by('-created_at').first()
    ref_id = last_payment.reference_id if last_payment else ''

    return render(request, 'accounts/gift_success.html', {
        'student_name': student_name,
        'ref_id': ref_id,
        'token': token,
    })


def check_gift_payment_status(request):
    """Vérifie le statut d'un paiement cadeau (pas besoin de login)."""
    ref_id = request.GET.get('ref', '')
    if not ref_id:
        return JsonResponse({'ok': False, 'error': 'Missing ref'}, status=400)

    try:
        payment = Payment.objects.get(reference_id=ref_id)
    except Payment.DoesNotExist:
        return JsonResponse({'ok': False, 'error': 'Not found'}, status=404)

    if payment.status == 'pending':
        try:
            data = _moncash_get_status(ref_id)
            if _is_paid_status(data.get('status')) and payment.status != 'completed':
                had_prior_paid = Payment.objects.filter(
                    user=payment.user, status='completed',
                ).exclude(pk=payment.pk).exists()
                payment.status = 'completed'
                payment.paid_at = timezone.now()
                payment.save(update_fields=['status', 'paid_at'])
                # Activer le plan + commissions (comme pour un paiement normal)
                plan = PLANS.get(payment.plan, PLANS['monthly'])
                _activate_subscription_and_pay_commission(
                    payment.user, plan['days'],
                    is_first_paid_subscription=not had_prior_paid,
                )
                # Marquer le gift link comme utilisé
                if payment.gift_link and not payment.gift_link.is_used:
                    payment.gift_link.is_used = True
                    payment.gift_link.save(update_fields=['is_used'])
        except requests.RequestException:
            pass

    return JsonResponse({
        'ok': True,
        'status': payment.status,
    })


# ═══════════════════ NATCASH — paiement manuel ═══════════════════

@login_required
def natcash_notify_view(request):
    """
    Endpoint appelé par le bouton 'J'ai payé' sur la page NatCash.
    Retourne un lien WhatsApp pré-rempli avec les infos du compte.
    """
    plan_key = request.GET.get('plan', 'monthly')
    plan = PLANS.get(plan_key, PLANS['monthly'])
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    contact = profile.phone or request.user.email or request.user.username
    name = profile.first_name or request.user.first_name or request.user.username

    msg = (
        f"Bonjour ! J'ai envoyé {plan['amount']}G via NatCash "
        f"pour l'abonnement {plan['label']} Ou Tou Bon.\n"
        f"Nom : {name} ({contact}).\n"
        f"Je vous envoie la capture d'écran de la transaction ci-dessous. "
        f"Merci d'activer mon abonnement !"
    )
    import urllib.parse
    wa_url = f"https://wa.me/{WHATSAPP_SUPPORT}?text={urllib.parse.quote(msg)}"
    return JsonResponse({'ok': True, 'wa_url': wa_url, 'contact': contact})


# Alias de compatibilité descendante (ancien nom PeyemAPI).
peyem_webhook = moncash_webhook
