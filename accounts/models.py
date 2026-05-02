import uuid
import hashlib
from datetime import timedelta
from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from cloudinary.models import CloudinaryField

SUBJECTS = [
    ('maths', 'Maths'),
    ('physique', 'Physique'),
    ('chimie', 'Chimie'),
    ('svt', 'SVT'),
    ('francais', 'Français'),
    ('philosophie', 'Philosophie'),
    ('histoire', 'Histoire & Géo'),
    ('anglais', 'Anglais'),
    ('espagnol', 'Espagnol'),
]

SERIE_CHOICES = [
    ('SVT', 'SVT — Sciences de la Vie et de la Terre'),
    ('SMP', 'SMP — Sciences Mathématiques et Physiques'),
    ('SES', 'SES — Sciences Économiques et Sociales'),
    ('LLA', 'LLA — Lettres, Langues et Arts'),
]

class School(models.Model):
    name       = models.CharField(max_length=200, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class UserProfile(models.Model):
    user          = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    first_name    = models.CharField(max_length=100, blank=True)
    last_name     = models.CharField(max_length=100, blank=True)
    phone         = models.CharField(max_length=20, blank=True)
    school        = models.CharField(max_length=200, blank=True)
    level         = models.CharField(max_length=50, default='Terminale')
    serie         = models.CharField(max_length=3, choices=SERIE_CHOICES, default='SVT', blank=True)
    avatar        = CloudinaryField('avatar', folder='bacia/avatars', blank=True, null=True)
    streak        = models.PositiveIntegerField(default=0)
    last_activity = models.DateField(null=True, blank=True)
    created_at    = models.DateTimeField(auto_now_add=True)
    preferred_lang    = models.CharField(max_length=5, default='fr', choices=[('fr', 'Français'), ('kr', 'Kreyòl')])
    langue_etrangere  = models.CharField(max_length=20, default='anglais', choices=[('anglais', 'Anglais'), ('espagnol', 'Espagnol')])
    bac_target        = models.PositiveSmallIntegerField(null=True, blank=True, help_text='Note cible sur 1900 définie à l\'inscription')

    # ── Abonnement ──
    plan_expiration = models.DateField(null=True, blank=True, help_text='Date d\'expiration de l\'abonnement actif')

    # ── Device fingerprint (1 appareil par compte) ──
    device_fingerprint = models.CharField(max_length=64, blank=True, default='')
    device_user_agent  = models.TextField(blank=True, default='')
    last_login_device  = models.DateTimeField(null=True, blank=True)
    active_session_key = models.CharField(max_length=40, blank=True, default='')

    # ── Device switch management ──
    pending_device_fingerprint = models.CharField(max_length=64, blank=True, default='')
    pending_device_session_key = models.CharField(max_length=40, blank=True, default='')
    pending_device_at          = models.DateTimeField(null=True, blank=True)
    device_change_locked_until = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.user.username} profile"

    def get_display_name(self):
        return self.first_name or self.user.username

    @property
    def is_premium(self):
        """True si l'abonnement est toujours actif."""
        if not self.plan_expiration:
            return False
        from datetime import date
        return self.plan_expiration >= date.today()


class DiagnosticResult(models.Model):
    user         = models.ForeignKey(User, on_delete=models.CASCADE, related_name='diagnostics')
    subject      = models.CharField(max_length=50, choices=SUBJECTS)
    score        = models.PositiveIntegerField(default=0)   # 0-100
    total_asked  = models.PositiveIntegerField(default=5)
    completed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'subject')

    def __str__(self):
        return f"{self.user.username} - {self.subject}: {self.score}%"


class Friendship(models.Model):
    STATUS_CHOICES = [
        ('pending', 'En attente'),
        ('accepted', 'Accepté'),
        ('declined', 'Refusé'),
    ]
    from_user  = models.ForeignKey(User, on_delete=models.CASCADE, related_name='sent_requests')
    to_user    = models.ForeignKey(User, on_delete=models.CASCADE, related_name='received_requests')
    status     = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('from_user', 'to_user')
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.from_user.username} → {self.to_user.username} ({self.status})"


def _gen_referral_code():
    return uuid.uuid4().hex[:8].upper()


class Agent(models.Model):
    user          = models.OneToOneField(User, on_delete=models.CASCADE, related_name='agent')
    phone         = models.CharField(max_length=20, unique=True)
    referral_code = models.CharField(max_length=16, unique=True, default=_gen_referral_code)
    balance       = models.PositiveIntegerField(default=0)   # en Gourdes
    total_earned  = models.PositiveIntegerField(default=0)   # cumulatif
    is_active     = models.BooleanField(default=True)
    created_at    = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Agent {self.phone} ({self.referral_code})"

    def total_referrals(self):
        return self.referrals.count()

    def paid_referrals(self):
        return self.referrals.filter(paid=True).count()


class AgentReferral(models.Model):
    agent         = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name='referrals')
    referred_user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='referred_by')
    phone_hint    = models.CharField(max_length=30, blank=True)   # snapshot du numéro
    amount        = models.PositiveIntegerField(default=150)       # G gagnés
    paid          = models.BooleanField(default=False)             # paiement confirmé
    created_at    = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Ref {self.agent.referral_code} → {self.phone_hint} (paid={self.paid})"


class AgentWithdrawal(models.Model):
    STATUS = [
        ('pending',   'En attente'),
        ('approved',  'Approuvé'),
        ('rejected',  'Refusé'),
    ]
    agent      = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name='withdrawals')
    amount     = models.PositiveIntegerField()
    moncash    = models.CharField(max_length=20)
    status     = models.CharField(max_length=10, choices=STATUS, default='pending')
    note       = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Retrait {self.amount}G — {self.agent.phone} ({self.status})"


def _generate_auth_token():
    """Génère un token hexadécimal aléatoire pour l'authentification persistante."""
    return uuid.uuid4().hex


def _get_token_expiry():
    return timezone.now() + timedelta(days=365)


# ─────────────────── PAIEMENT MONCASH ───────────────────

PLAN_CHOICES = [
    ('monthly',  'Mensuel'),
    ('annual',   'Annuel'),
]

class Payment(models.Model):
    user         = models.ForeignKey(User, on_delete=models.CASCADE, related_name='payments')
    reference_id = models.CharField(max_length=255, unique=True, db_index=True)
    plan         = models.CharField(max_length=10, choices=PLAN_CHOICES)
    amount       = models.PositiveIntegerField()       # Gourdes
    status       = models.CharField(max_length=20, default='pending',
                                    choices=[('pending','En attente'),('completed','Payé'),('failed','Échoué')])
    created_at   = models.DateTimeField(auto_now_add=True)
    paid_at      = models.DateTimeField(null=True, blank=True)
    # Lien cadeau (si payé par un proche)
    gift_link    = models.ForeignKey('GiftPaymentLink', on_delete=models.SET_NULL,
                                     null=True, blank=True, related_name='payments')

    class Meta:
        ordering = ['-created_at']


def _gen_gift_token():
    return uuid.uuid4().hex


class GiftPaymentLink(models.Model):
    """Lien partageable pour qu'un proche paye l'abonnement d'un élève."""
    student     = models.ForeignKey(User, on_delete=models.CASCADE, related_name='gift_links')
    token       = models.CharField(max_length=64, unique=True, default=_gen_gift_token, db_index=True)
    is_used     = models.BooleanField(default=False)
    created_at  = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Gift link for {self.student.username} (used={self.is_used})"

    def __str__(self):
        return f"{self.reference_id} — {self.amount}G ({self.status})"


class PersistentAuthToken(models.Model):
    """Token pour authentification persistante (auto-login sans mot de passe)."""
    user       = models.OneToOneField(User, on_delete=models.CASCADE, related_name='auth_token')
    token      = models.CharField(max_length=64, unique=True, default=_generate_auth_token)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(default=_get_token_expiry)

    class Meta:
        ordering = ['-created_at']

    def is_valid(self):
        """Vérifier si le token n'est pas expiré."""
        return timezone.now() < self.expires_at

    def __str__(self):
        return f"AuthToken for {self.user.username}"


# ─────────────────── LIMITES GRATUITES ───────────────────

class DailyUsage(models.Model):
    """Compteurs journaliers pour limiter l'usage gratuit."""
    user         = models.ForeignKey(User, on_delete=models.CASCADE, related_name='daily_usage')
    date         = models.DateField()
    chat_count   = models.PositiveIntegerField(default=0)
    quiz_count   = models.PositiveIntegerField(default=0)
    exercise_subjects = models.JSONField(default=dict, blank=True)  # {subject: count}
    extra_bet_count   = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = ('user', 'date')

    def __str__(self):
        return f"{self.user.username} — {self.date}"


# ─────────────────── CHAT AMIS ───────────────────

class FriendMessage(models.Model):
    """Message privé entre amis."""
    sender     = models.ForeignKey(User, on_delete=models.CASCADE, related_name='sent_friend_msgs')
    receiver   = models.ForeignKey(User, on_delete=models.CASCADE, related_name='received_friend_msgs')
    content    = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    is_read    = models.BooleanField(default=False)
    is_system  = models.BooleanField(default=False)


    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f"{self.sender.username} → {self.receiver.username}: {self.content[:40]}"


# ─────────────────── ADMIN MOTIVATIONAL MESSAGE ───────────────────

class AdminMessage(models.Model):
    """One-way motivational message from OUTOUBON (admin) to a user.
    Only the latest message per user matters — previous ones are replaced."""
    receiver   = models.ForeignKey(User, on_delete=models.CASCADE, related_name='admin_messages')
    content    = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    is_read    = models.BooleanField(default=False)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"OUTOUBON → {self.receiver.username}: {self.content[:40]}"


# ─────────────────── ADMIN PANEL CONFIG ───────────────────

class AdminPanelConfig(models.Model):
    """Stores the hashed password for the secret admin panel.
    Only one row should exist (singleton)."""
    password_hash = models.CharField(max_length=128)
    created_at    = models.DateTimeField(auto_now_add=True)
    updated_at    = models.DateTimeField(auto_now=True)

    @classmethod
    def get_instance(cls):
        return cls.objects.first()

    @classmethod
    def set_password(cls, raw_password):
        h = hashlib.sha256(raw_password.encode()).hexdigest()
        obj, _ = cls.objects.get_or_create(pk=1, defaults={'password_hash': h})
        if obj.password_hash != h:
            obj.password_hash = h
            obj.save(update_fields=['password_hash'])
        return obj

    def check_password(self, raw_password):
        return self.password_hash == hashlib.sha256(raw_password.encode()).hexdigest()

    def __str__(self):
        return "Admin Panel Config"


# ─────────────────── VISITOR TRACKING ───────────────────

class SiteVisit(models.Model):
    """Track site visits for admin dashboard analytics."""
    ip_hash    = models.CharField(max_length=64, db_index=True)
    path       = models.CharField(max_length=500, default='/')
    user_agent = models.TextField(blank=True, default='')
    user       = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    visited_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-visited_at']

    def __str__(self):
        return f"Visit {self.ip_hash[:8]}… @ {self.visited_at.strftime('%d/%m %H:%M')}"

