"""
MonCash payment integration via PeyemAPI.

Plans:
  - Mensuel:  750G / mois  (prix barré: 1000G → -25%)
  - Annuel:   500G / mois  → 6 000G facturé en une fois
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


# ── Helper: activer abonnement + payer commission agent (1er mois) ──────────
def _activate_subscription_and_pay_commission(user, days: int):
    """
    Active ou prolonge l'abonnement d'un utilisateur et verse automatiquement
    la commission à l'agent parrain si c'est le PREMIER abonnement de l'élève.
    Appelé depuis webhook MonCash ET depuis l'activation manuelle admin.
    """
    from accounts.models import AgentReferral
    profile, _ = UserProfile.objects.get_or_create(user=user)
    today = date.today()

    # Vérifier si c'est le premier abonnement (pas encore eu de plan actif avant)
    is_first_subscription = (profile.plan_expiration is None or profile.plan_expiration < today)

    start = profile.plan_expiration if (profile.plan_expiration and profile.plan_expiration > today) else today
    profile.plan_expiration = start + timedelta(days=days)
    profile.save(update_fields=['plan_expiration'])

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
    """Appelle PeyemAPI /pay et redirige vers MonCash."""
    plan_key = request.POST.get('plan', 'monthly')
    plan = PLANS.get(plan_key)
    if not plan:
        return JsonResponse({'ok': False, 'error': 'Plan invalide'}, status=400)

    ref_id = f"BACIA-{request.user.pk}-{plan_key}-{uuid.uuid4().hex[:8].upper()}"
    return_url = request.build_absolute_uri('/payment-success/')

    # Sauvegarder le paiement en attente
    Payment.objects.create(
        user=request.user,
        reference_id=ref_id,
        plan=plan_key,
        amount=plan['amount'],
        status='pending',
    )

    # Appeler PeyemAPI
    try:
        resp = requests.post(
            f"{settings.PEYEM_API_URL}/pay",
            json={
                'amount': plan['amount'],
                'referenceId': ref_id,
                'returnUrl': return_url,
            },
            headers={
                'Authorization': f'Bearer {settings.PEYEM_SECRET_KEY}',
                'Content-Type': 'application/json',
            },
            timeout=15,
        )
        data = resp.json()
    except requests.RequestException as e:
        logger.error('PeyemAPI /pay error: %s', e)
        return render(request, 'accounts/payment_error.html', {
            'error': 'Erreur de connexion au service de paiement. Réessaie.',
        })

    payment_url = data.get('payment_url')
    if not payment_url:
        logger.error('PeyemAPI no payment_url: %s', data)
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
@csrf_exempt
@require_POST
def peyem_webhook(request):
    """Reçoit la notification PeyemAPI quand le paiement est confirmé."""
    signature = request.headers.get('X-Webhook-Signature', '')
    payload = request.body

    # Vérifier la signature HMAC-SHA256
    expected = hmac.new(
        settings.PEYEM_WEBHOOK_SECRET.encode(),
        payload,
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(signature, expected):
        logger.warning('Webhook signature mismatch')
        return JsonResponse({'error': 'Invalid signature'}, status=401)

    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    ref_id = data.get('referenceId', '')
    status = data.get('status', '')

    if status != 'completed':
        return JsonResponse({'ok': True, 'info': 'Status noted'})

    try:
        payment = Payment.objects.get(reference_id=ref_id)
    except Payment.DoesNotExist:
        logger.warning('Webhook for unknown ref: %s', ref_id)
        return JsonResponse({'error': 'Unknown reference'}, status=404)

    if payment.status == 'completed':
        return JsonResponse({'ok': True, 'info': 'Already processed'})

    # Marquer payé
    payment.status = 'completed'
    payment.paid_at = timezone.now()
    payment.save(update_fields=['status', 'paid_at'])

    # Activer abonnement + commission agent
    plan = PLANS.get(payment.plan, PLANS['monthly'])
    _activate_subscription_and_pay_commission(payment.user, plan['days'])

    # Si c'est un paiement cadeau, marquer le lien comme utilisé
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

    # Optionnel: interroger PeyemAPI pour mise à jour
    if payment.status == 'pending':
        try:
            resp = requests.get(
                f"{settings.PEYEM_API_URL}/status",
                params={'referenceId': ref_id},
                headers={'Authorization': f'Bearer {settings.PEYEM_SECRET_KEY}'},
                timeout=10,
            )
            data = resp.json()
            if data.get('status') == 'completed' and payment.status != 'completed':
                payment.status = 'completed'
                payment.paid_at = timezone.now()
                payment.save(update_fields=['status', 'paid_at'])
                # Activer plan + commission
                plan = PLANS.get(payment.plan, PLANS['monthly'])
                _activate_subscription_and_pay_commission(payment.user, plan['days'])
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
    return_url = request.build_absolute_uri(f'/cadeau/{token}/merci/')

    Payment.objects.create(
        user=gift.student,
        reference_id=ref_id,
        plan=plan_key,
        amount=plan['amount'],
        status='pending',
        gift_link=gift,
    )

    try:
        resp = requests.post(
            f"{settings.PEYEM_API_URL}/pay",
            json={
                'amount': plan['amount'],
                'referenceId': ref_id,
                'returnUrl': return_url,
            },
            headers={
                'Authorization': f'Bearer {settings.PEYEM_SECRET_KEY}',
                'Content-Type': 'application/json',
            },
            timeout=15,
        )
        data = resp.json()
    except requests.RequestException as e:
        logger.error('PeyemAPI gift /pay error: %s', e)
        return render(request, 'accounts/payment_error.html', {
            'error': 'Erreur de connexion au service de paiement. Réessayez.',
        })

    payment_url = data.get('payment_url')
    if not payment_url:
        logger.error('PeyemAPI gift no payment_url: %s', data)
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
            resp = requests.get(
                f"{settings.PEYEM_API_URL}/status",
                params={'referenceId': ref_id},
                headers={'Authorization': f'Bearer {settings.PEYEM_SECRET_KEY}'},
                timeout=10,
            )
            data = resp.json()
            if data.get('status') == 'completed' and payment.status != 'completed':
                payment.status = 'completed'
                payment.paid_at = timezone.now()
                payment.save(update_fields=['status', 'paid_at'])
                # Activer plan pour l'élève
                plan = PLANS.get(payment.plan, PLANS['monthly'])
                profile, _ = UserProfile.objects.get_or_create(user=payment.user)
                today = date.today()
                start = profile.plan_expiration if (profile.plan_expiration and profile.plan_expiration > today) else today
                profile.plan_expiration = start + timedelta(days=plan['days'])
                profile.save(update_fields=['plan_expiration'])
                # Marquer le gift link comme utilisé
                if payment.gift_link:
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
