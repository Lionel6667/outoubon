from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
import json, hashlib

SUBJECTS = [
    ('maths', 'Maths'),
    ('physique', 'Physique'),
    ('chimie', 'Chimie'),
    ('svt', 'SVT'),
    ('francais', 'Kreyòl'),
    ('philosophie', 'Philosophie'),
    ('anglais', 'Anglais'),
    ('general', 'Général'),
]

class QuizQuestion(models.Model):
    subject          = models.CharField(max_length=50, choices=SUBJECTS, db_index=True)
    enonce           = models.TextField()
    options          = models.JSONField(default=list)
    reponse_correcte = models.CharField(max_length=300)
    explication      = models.TextField(blank=True)
    sujet            = models.CharField(max_length=100, blank=True)

    def __str__(self):
        return f"[{self.subject}] {self.enonce[:60]}"

    def to_dict(self):
        options = self.options if isinstance(self.options, list) else []
        try:
            correct_idx = int(self.reponse_correcte)
        except (ValueError, TypeError):
            try:
                correct_idx = options.index(self.reponse_correcte)
            except (ValueError, AttributeError):
                correct_idx = 0
        return {
            'id': self.pk,
            'subject': self.subject,
            'enonce': self.enonce,
            'options': options,
            'reponse_correcte': correct_idx,
            'explication': self.explication,
            'sujet': self.sujet,
        }


class QuizSession(models.Model):
    user         = models.ForeignKey(User, on_delete=models.CASCADE, related_name='quiz_sessions')
    subject      = models.CharField(max_length=50, choices=SUBJECTS)
    score        = models.PositiveIntegerField(default=0)
    total        = models.PositiveIntegerField(default=10)
    details      = models.JSONField(default=list)
    completed_at = models.DateTimeField(auto_now_add=True)

    def get_percentage(self):
        return round((self.score / self.total) * 100) if self.total else 0

    def __str__(self):
        return f"{self.user.username} - {self.subject} {self.get_percentage()}%"


class ChatMessage(models.Model):
    ROLES = [('user', 'User'), ('ai', 'AI')]
    user         = models.ForeignKey(User, on_delete=models.CASCADE, related_name='chat_messages')
    role         = models.CharField(max_length=10, choices=ROLES)
    content      = models.TextField()
    subject      = models.CharField(max_length=50, choices=SUBJECTS, default='general')
    session_key  = models.CharField(max_length=40, blank=True, db_index=True)  # group messages into conversations
    created_at   = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f"{self.user.username} [{self.role}]: {self.content[:50]}"


class UserStats(models.Model):
    user              = models.OneToOneField(User, on_delete=models.CASCADE, related_name='stats')
    quiz_completes    = models.PositiveIntegerField(default=0)
    exercices_resolus = models.PositiveIntegerField(default=0)
    messages_envoyes  = models.PositiveIntegerField(default=0)
    total_points      = models.PositiveIntegerField(default=0)
    minutes_etude     = models.PositiveIntegerField(default=0)  # temps total étudié

    def __str__(self):
        return f"{self.user.username} stats"


# ── Fiches Mémo (Flashcards) ──────────────────────────────────────────────────
class Flashcard(models.Model):
    subject    = models.CharField(max_length=50, choices=SUBJECTS, db_index=True)
    question   = models.TextField()
    answer     = models.TextField()
    hint       = models.CharField(max_length=300, blank=True)
    difficulty = models.IntegerField(default=2)   # 1=easy 2=medium 3=hard
    source     = models.CharField(max_length=100, blank=True)  # 'pdf:geologie.pdf' or 'ai'
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"[{self.subject}] {self.question[:60]}"


class FlashcardProgress(models.Model):
    STATUS = [('new', 'New'), ('review', 'À revoir'), ('known', 'Connu')]
    user       = models.ForeignKey(User, on_delete=models.CASCADE, related_name='flashcard_progress')
    flashcard  = models.ForeignKey(Flashcard, on_delete=models.CASCADE, related_name='progress')
    status     = models.CharField(max_length=10, choices=STATUS, default='new')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('user', 'flashcard')

    def __str__(self):
        return f"{self.user.username} - {self.flashcard_id}: {self.status}"


# ── Plan de Révision ──────────────────────────────────────────────────────────
class RevisionPlan(models.Model):
    user       = models.ForeignKey(User, on_delete=models.CASCADE, related_name='revision_plans')
    serie      = models.CharField(max_length=10, blank=True)
    content    = models.JSONField(default=dict)   # {weeks: [{week_label, days: [{subject, task, duration}]}]}
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.user.username} plan {self.created_at.date()}"


# ── Analyse Quiz ──────────────────────────────────────────────────────────────
class QuizAnalysis(models.Model):
    session    = models.OneToOneField(QuizSession, on_delete=models.CASCADE, related_name='analysis')
    content    = models.TextField()   # AI analysis text
    weak_tags  = models.JSONField(default=list)   # ['Dérivation', 'Suites', ...]
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Analysis for session {self.session_id}"


# ── Mémoire IA persistante ────────────────────────────────────────────────────
class AIMemory(models.Model):
    """
    Observations que BacIA extrait et stocke sur l'élève après chaque interaction.
    Constitue la mémoire long-terme du coach IA — persiste entre toutes les sessions.
    """
    MEMORY_TYPES = [
        ('erreur',      'Erreur récurrente'),
        ('force',       'Point fort'),
        ('style',       'Style d\'apprentissage'),
        ('perso',       'Info personnelle'),
        ('progression', 'Progression notable'),
        ('concept',     'Concept mal compris'),
        ('autre',       'Autre'),
    ]
    user         = models.ForeignKey(User, on_delete=models.CASCADE, related_name='ai_memories')
    memory_type  = models.CharField(max_length=20, choices=MEMORY_TYPES, default='autre')
    subject      = models.CharField(max_length=50, blank=True)
    content      = models.TextField()
    # Chaque mémoire a un tag unique pour éviter les doublons (ex: "maths:derivee_chaine")
    tag          = models.CharField(max_length=100, blank=True, db_index=True)
    importance   = models.IntegerField(default=5)  # 1-10 : 10 = critique à rappeler
    seen_count   = models.PositiveIntegerField(default=1)   # combien de fois détectée
    created_at   = models.DateTimeField(auto_now_add=True)
    updated_at   = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-importance', '-updated_at']
        unique_together = ('user', 'tag')

    def __str__(self):
        return f"[{self.user.username}] {self.memory_type}: {self.content[:60]}"


# ── Index des examens PDF (pre-extraits une seule fois) ───────────────────────
class ExamIndex(models.Model):
    """
    Stocke l'extrait texte de chaque exam PDF indexé.
    Rempli UNE SEULE FOIS via: python manage.py index_exams
    Ensuite get_exam_context() pioche aléatoirement dans cette table → zéro lecture disque.
    """
    subject      = models.CharField(max_length=50, db_index=True)
    filename     = models.CharField(max_length=255)
    year         = models.PositiveSmallIntegerField(null=True, blank=True)
    text_excerpt = models.TextField()   # max ~2500 chars, first pages
    char_count   = models.PositiveIntegerField(default=0)
    indexed_at   = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('subject', 'filename')
        ordering = ['-year', 'filename']

    def __str__(self):
        return f"[{self.subject}] {self.filename} ({self.char_count} chars)"


# ── Questions mémorisées (bookmarks) ─────────────────────────────────────────
class BookmarkedQuestion(models.Model):
    user       = models.ForeignKey(User, on_delete=models.CASCADE, related_name='bookmarks')
    subject    = models.CharField(max_length=50)
    enonce     = models.TextField()
    options    = models.JSONField(default=list)
    reponse_correcte = models.IntegerField(default=0)
    explication      = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.user.username} bookmark: {self.enonce[:50]}"


# ── Chapitres par matière (extraits 1 seule fois des PDFs, stockés en BDD) ───
class SubjectChapter(models.Model):
    """
    Chapitres détectés dans les examens PDF pour chaque matière.
    PEUPLÉ UNE SEULE FOIS via : python manage.py extract_chapters
    Zéro appel IA après ça pour la liste des chapitres.
    """
    subject        = models.CharField(max_length=50, choices=SUBJECTS, db_index=True)
    subsection     = models.CharField(max_length=50, blank=True, default='')  # ex: 'biologie' ou 'geologie' pour SVT
    title          = models.CharField(max_length=200)          # ex: "Les dérivées"
    description    = models.TextField(blank=True)              # résumé 2-3 phrases
    exam_excerpts  = models.TextField(blank=True)              # extraits d'examens pré-filtrés
    order          = models.PositiveSmallIntegerField(default=0)
    extracted_at   = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['subject', 'order']
        unique_together = ('subject', 'subsection', 'title')

    def __str__(self):
        return f"[{self.subject}] {self.title}"


# ── Session de cours interactif ───────────────────────────────────────────────
class CourseSession(models.Model):
    """
    Une session de cours interactif entre l'élève et BacIA sur un chapitre précis.
    L'historique est stocké ici → zéro requête PDF répétée.
    Depuis la v2 : supporte les chapitres JSON (chapter_subject + chapter_num)
    en plus des anciens chapitres DB (chapter FK nullable).
    """
    STATUS = [('active', 'En cours'), ('completed', 'Terminé')]
    user              = models.ForeignKey(User, on_delete=models.CASCADE, related_name='course_sessions')
    # FK legacy (nullable) — anciens chapitres DB
    chapter           = models.ForeignKey(SubjectChapter, on_delete=models.SET_NULL,
                                          related_name='sessions', null=True, blank=True)
    # Nouveaux champs JSON-backed
    chapter_subject   = models.CharField(max_length=50, blank=True, default='')
    chapter_num       = models.PositiveSmallIntegerField(null=True, blank=True)
    chapter_title     = models.CharField(max_length=200, blank=True, default='')
    chapter_desc      = models.TextField(blank=True, default='')
    messages          = models.JSONField(default=list)   # [{role, content, ts}]
    progress_step     = models.PositiveSmallIntegerField(default=0)  # 0=intro, 1=cours, 2=exos, 3=bilan
    status            = models.CharField(max_length=20, choices=STATUS, default='active')
    created_at        = models.DateTimeField(auto_now_add=True)
    updated_at        = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']

    def get_chapter_title(self):
        """Retourne le titre du chapitre (JSON ou DB legacy)."""
        return self.chapter_title or (self.chapter.title if self.chapter_id else '—')

    def get_chapter_subject(self):
        """Retourne la matière du chapitre (JSON ou DB legacy)."""
        return self.chapter_subject or (self.chapter.subject if self.chapter_id else '')

    def get_url_kwargs(self):
        """Retourne les kwargs pour l'URL chapter_cours_json."""
        return {'subject': self.get_chapter_subject(), 'num': self.chapter_num or 1}

    def __str__(self):
        title = self.get_chapter_title()
        return f"{self.user.username} → {title} (step {self.progress_step})"


class CourseProgressState(models.Model):
    """
    Sauvegarde persistante de l'etat d'un cours riche (scroll, quiz, brouillons).
    Permet a l'eleve de reprendre exactement la ou il s'est arrete.
    """
    user       = models.ForeignKey(User, on_delete=models.CASCADE, related_name='course_progress_states')
    course_key = models.CharField(max_length=100, db_index=True)
    state      = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']
        unique_together = ('user', 'course_key')

    def __str__(self):
        return f"{self.user.username} → {self.course_key}"


class GeneratedCourseAsset(models.Model):
    """
    Cache partage des contenus de cours generes par l'IA.
    Une lecon ou un mini-quiz normal est genere une fois, puis reutilise
    pour tous les utilisateurs.
    """
    ASSET_TYPES = [
        ('lesson', 'Lecon'),
        ('quiz', 'Mini quiz'),
        ('exercise_bank', 'Banque exercices'),
        ('summary', 'Résumé chapitre'),
    ]

    course_key = models.CharField(max_length=100, db_index=True)
    section_id = models.CharField(max_length=120, db_index=True)
    section_title = models.CharField(max_length=255, blank=True, default='')
    asset_type = models.CharField(max_length=20, choices=ASSET_TYPES)
    mode = models.CharField(max_length=20, default='normal')
    payload = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['course_key', 'section_id', 'asset_type']
        unique_together = ('course_key', 'section_id', 'asset_type', 'mode')

    def __str__(self):
        return f"{self.course_key} → {self.section_id} ({self.asset_type}/{self.mode})"


# ── Répétition espacée — mauvaises réponses de quiz ─────────────────────────
class MistakeTracker(models.Model):
    """
    Enregistre chaque question ratée en quiz et planifie sa révision selon
    l'algorithme SM-2 (répétition espacée).
    - wrong_count       : nb total de fois ratée
    - correct_streak    : nb de fois correcte consécutive (mastered dès 3)
    - interval_days     : nb de jours avant prochaine révision
    - ease_factor       : coefficient SM-2 (2.5 par défaut)
    - next_review       : date cible de prochaine révision
    - mastered          : True dès correct_streak >= 3
    """
    user             = models.ForeignKey(User, on_delete=models.CASCADE, related_name='mistakes')
    subject          = models.CharField(max_length=50, db_index=True)
    question_hash    = models.CharField(max_length=32, db_index=True)   # MD5(enonce[:200])
    enonce           = models.TextField()
    options          = models.JSONField(default=list)
    reponse_correcte = models.IntegerField(default=0)
    explication      = models.TextField(blank=True)
    theme            = models.CharField(max_length=100, blank=True)
    # SM-2
    ease_factor      = models.FloatField(default=2.5)
    interval_days    = models.PositiveSmallIntegerField(default=1)
    repetitions      = models.PositiveSmallIntegerField(default=0)   # fois réussies suite
    wrong_count      = models.PositiveIntegerField(default=1)
    correct_streak   = models.PositiveSmallIntegerField(default=0)
    mastered         = models.BooleanField(default=False, db_index=True)
    next_review      = models.DateField(db_index=True)
    created_at       = models.DateTimeField(auto_now_add=True)
    updated_at       = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('user', 'question_hash')
        ordering = ['next_review']
        indexes = [
            models.Index(fields=['user', 'mastered', 'next_review']),
        ]

    def __str__(self):
        return f"{self.user.username} [{self.subject}] streak={self.correct_streak}: {self.enonce[:50]}"

    @staticmethod
    def make_hash(enonce: str) -> str:
        return hashlib.md5(enonce[:200].encode('utf-8')).hexdigest()

    def apply_sm2(self, correct: bool):
        """
        Met à jour les champs SM-2 selon la réponse.
        correct=True  → intervalle augmente, ease_factor ajusté
        correct=False → repart de 0, intervalle = 1 jour
        """
        from datetime import date, timedelta
        if correct:
            self.correct_streak += 1
            self.repetitions    += 1
            if self.repetitions == 1:
                self.interval_days = 1
            elif self.repetitions == 2:
                self.interval_days = 6
            else:
                self.interval_days = max(1, round(self.interval_days * self.ease_factor))
            # Quality score fixé à 4 (bonne réponse sans hésitation)
            q = 4
            self.ease_factor = max(1.3, self.ease_factor + 0.1 - (5 - q) * (0.08 + (5 - q) * 0.02))
            if self.correct_streak >= 3:
                self.mastered = True
        else:
            self.wrong_count    += 1
            self.correct_streak  = 0
            self.repetitions     = 0
            self.interval_days   = 1
            self.ease_factor     = max(1.3, self.ease_factor - 0.2)
        self.next_review = date.today() + timedelta(days=self.interval_days)


# ─── Translation cache (UI + AI content) ─────────────────────────────────────
class TranslationCache(models.Model):
    """Stores French→Kreyòl translations to avoid repeated AI calls."""
    content_hash = models.CharField(max_length=64, db_index=True, unique=True)
    source_text  = models.TextField()
    translated   = models.TextField()
    lang         = models.CharField(max_length=10, default='kr')  # 'fr' or 'kr'
    context      = models.CharField(max_length=100, blank=True)   # page hint
    created_at   = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=['content_hash', 'lang'])]

    @staticmethod
    def make_hash(text: str, lang: str) -> str:
        return hashlib.sha256(f"{lang}:{text}".encode()).hexdigest()

    @classmethod
    def get_or_none(cls, text: str, lang: str):
        h = cls.make_hash(text, lang)
        try:
            return cls.objects.get(content_hash=h)
        except cls.DoesNotExist:
            return None

    @classmethod
    def store(cls, text: str, lang: str, translated: str, context: str = ''):
        h = cls.make_hash(text, lang)
        obj, _ = cls.objects.update_or_create(
            content_hash=h,
            defaults={'source_text': text[:4000], 'translated': translated, 'lang': lang, 'context': context}
        )
        return obj


# ─── Vrai exercices du BAC (100% original, pas d'IA) ─────────────────────────
class BACExercise(models.Model):
    """
    Stocke les vrais exercices extraits des examens du BAC.
    Ces exercices ne sont PAS générés par l'IA, ce sont des vrais sujets du BAC.
    """
    chapter = models.ForeignKey(SubjectChapter, on_delete=models.CASCADE, related_name='bac_exercises')
    
    # Source information
    exam_file = models.CharField(max_length=255)  # Nom du fichier PDF original
    exam_year = models.CharField(max_length=4)    # Année de l'examen (ex: "2019")
    exam_series = models.CharField(max_length=50) # Série (SVT, SMP, SES, etc.)
    
    # Exercise details
    problem_number = models.IntegerField()        # Numéro du problème dans l'examen (1, 2, etc.)
    title = models.CharField(max_length=255)      # Titre du problème
    content = models.TextField()                  # Texte complet du problème
    theme = models.CharField(max_length=100)      # Thème général (condensateur, induction, etc.)
    points = models.IntegerField(default=0)       # Points attribués au BAC
    
    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    order = models.IntegerField(default=0)        # Pour le tri
    
    class Meta:
        ordering = ['chapter', 'exam_year', 'problem_number']
        indexes = [
            models.Index(fields=['chapter', 'theme']),
            models.Index(fields=['exam_year']),
        ]
    
    def __str__(self):
        return f"[{self.exam_year}] {self.chapter.title} - Problème {self.problem_number}"

# ─────────────────────────────────────────────────────────────────────────────
# QUIZ DUEL (mode défi en ligne)
# ─────────────────────────────────────────────────────────────────────────────
import secrets as _secrets

class QuizDuel(models.Model):
    STATUS_CHOICES = [
        ('waiting',  'En attente du challenger'),
        ('active',   'En cours'),
        ('finished', 'Terminé'),
        ('expired',  'Expiré'),
    ]
    code                = models.CharField(max_length=8, unique=True, db_index=True)
    creator             = models.ForeignKey(User, on_delete=models.CASCADE, related_name='duels_created')
    challenger          = models.ForeignKey(User, on_delete=models.CASCADE, related_name='duels_joined', null=True, blank=True)
    subject             = models.CharField(max_length=50)
    questions           = models.JSONField(default=list)   # liste de dicts {enonce, options, reponse_correcte, explication, theme}
    creator_answers     = models.JSONField(default=list)   # [{q_idx, chosen, correct}]
    challenger_answers  = models.JSONField(default=list)
    creator_score       = models.IntegerField(default=0)
    challenger_score    = models.IntegerField(default=0)
    creator_finished    = models.BooleanField(default=False)
    challenger_finished = models.BooleanField(default=False)
    status              = models.CharField(max_length=10, choices=STATUS_CHOICES, default='waiting')
    created_at          = models.DateTimeField(auto_now_add=True)
    expires_at          = models.DateTimeField()

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        ch = self.challenger.username if self.challenger else '?'
        return f"Duel {self.code}: {self.creator.username} vs {ch} ({self.status})"

    def is_expired(self):
        from django.utils import timezone as _tz
        return _tz.now() > self.expires_at

    @staticmethod
    def generate_code():
        """Génère un code d'invitation unique à 6 caractères majuscules."""
        while True:
            code = _secrets.token_hex(3).upper()  # 6 hex chars
            if not QuizDuel.objects.filter(code=code).exists():
                return code


# ─── Maîtrise par matière (adaptive learning) ────────────────────────────────
class SubjectMastery(models.Model):
    """
    Suivi détaillé de la maîtrise de chaque matière par élève.
    Mis à jour après chaque quiz, exercice, ou session de cours.
    """
    CONFIDENCE_LEVELS = [
        ('debutant',    'Débutant'),
        ('apprenti',    'Apprenti'),
        ('intermediaire', 'Intermédiaire'),
        ('avance',      'Avancé'),
        ('expert',      'Expert'),
    ]
    user            = models.ForeignKey(User, on_delete=models.CASCADE, related_name='subject_masteries')
    subject         = models.CharField(max_length=50)
    mastery_score   = models.FloatField(default=0.0)          # 0-100, weighted EMA
    correct_count   = models.PositiveIntegerField(default=0)
    error_count     = models.PositiveIntegerField(default=0)
    avg_time_seconds = models.FloatField(null=True, blank=True)
    confidence_level = models.CharField(max_length=20, choices=CONFIDENCE_LEVELS, default='debutant')
    # Last 10 errors: [{question, date, error_type}]
    recent_errors   = models.JSONField(default=list)
    # Last 10 correct answers: [{question, answer, date}]
    recent_correct  = models.JSONField(default=list)
    # Topics where mastered (accumulated set)
    mastered_topics = models.JSONField(default=list)
    # Topics still weak
    weak_topics     = models.JSONField(default=list)
    last_updated    = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('user', 'subject')
        ordering = ['-mastery_score']

    def __str__(self):
        return f"{self.user.username} [{self.subject}] {self.mastery_score:.0f}% ({self.confidence_level})"

    def update_confidence_level(self):
        """Met à jour le niveau de confiance basé sur le score de maîtrise."""
        if self.mastery_score >= 85:
            self.confidence_level = 'expert'
        elif self.mastery_score >= 70:
            self.confidence_level = 'avance'
        elif self.mastery_score >= 50:
            self.confidence_level = 'intermediaire'
        elif self.mastery_score >= 25:
            self.confidence_level = 'apprenti'
        else:
            self.confidence_level = 'debutant'

    def total_attempts(self):
        return self.correct_count + self.error_count

    def accuracy_pct(self):
        total = self.total_attempts()
        return round(self.correct_count / total * 100) if total else 0


# ─── Résumés de sessions de chat (historique structuré) ──────────────────────
class ChatSessionSummary(models.Model):
    """
    Résumé structuré JSON généré par l'IA à la fin de chaque session de chat.
    Accumule tous les résumés (pas de remplacement) pour analyse longitudinale.
    """
    user            = models.ForeignKey(User, on_delete=models.CASCADE, related_name='chat_summaries')
    session_key     = models.CharField(max_length=40, db_index=True)
    # JSON structure: {subjects, strengths, weaknesses, confidence, observations, key_questions}
    summary         = models.JSONField(default=dict)
    subjects_covered = models.JSONField(default=list)   # ['maths', 'physique', ...]
    message_count   = models.PositiveIntegerField(default=0)
    created_at      = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        unique_together = ('user', 'session_key')

    def __str__(self):
        return f"{self.user.username} summary {self.session_key[:8]} ({self.created_at.date()})"


# ─── Événements d'apprentissage (journal complet) ────────────────────────────
class LearningEvent(models.Model):
    """
    Journal de tous les événements d'apprentissage significatifs.
    Chaque quiz, exercice, session de chat et progrès de cours est enregistré ici
    pour permettre une analyse continue et complète de l'apprentissage.
    """
    EVENT_TYPES = [
        ('quiz_completed',      'Quiz complété'),
        ('exercise_corrected',  'Exercice corrigé'),
        ('chat_session',        'Session de chat'),
        ('course_chapter',      'Chapitre de cours'),
        ('mastery_gained',      'Maîtrise acquise'),
    ]
    user        = models.ForeignKey(User, on_delete=models.CASCADE, related_name='learning_events')
    event_type  = models.CharField(max_length=30, choices=EVENT_TYPES)
    subject     = models.CharField(max_length=50, blank=True)
    # Données de l'événement: score, questions, erreurs, durée, etc.
    details     = models.JSONField(default=dict)
    score_pct   = models.FloatField(null=True, blank=True)  # % de réussite si applicable
    created_at  = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'subject', 'event_type']),
            models.Index(fields=['user', 'created_at']),
        ]

    def __str__(self):
        return f"{self.user.username} [{self.event_type}] {self.subject} {self.score_pct or ''}%"


# -- Cache des résultats IA pour la progression -- 
class AIProgressCache(models.Model):
    """
    Cache persistant des résultats d'IA pour la page de progression.
    Évite les régénérations et assure la disponibilité des données.
    """
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='ai_progress_cache')
    coaching_advice = models.TextField(blank=True)  # Message principal du Coach IA
    chapter_advice = models.TextField(blank=True)   # Points à travailler par chapitre
    smart_coach_data = models.JSONField(default=dict)  # Données complètes du smart coach
    last_updated = models.DateTimeField(auto_now=True)
    is_valid = models.BooleanField(default=True)  # False si données obsolètes

    class Meta:
        verbose_name = "Cache IA Progression"
        verbose_name_plural = "Caches IA Progression"

    def __str__(self):
        return f"Cache IA {self.user.username} ({'valid' if self.is_valid else 'invalid'})"


# ─── Extra bèt (quiz communautaire avancé) ──────────────────────────────────
class ExtraBetPost(models.Model):
    QUESTION_TYPES = [
        ('direct', 'Réponse directe'),
        ('fill', 'Texte à compléter'),
        ('qcm', 'Choix multiple'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='extra_bet_posts')
    subject = models.CharField(max_length=50, db_index=True)
    question_type = models.CharField(max_length=10, choices=QUESTION_TYPES, default='direct')
    prompt = models.TextField()
    answer = models.TextField()
    options = models.JSONField(default=list, blank=True)
    ai_verdict = models.JSONField(default=dict, blank=True)
    likes = models.ManyToManyField(User, related_name='liked_extra_bets', blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['subject', 'created_at']),
        ]

    def __str__(self):
        return f"{self.user.username} [{self.subject}] {self.prompt[:60]}"


class ExtraBetAttempt(models.Model):
    post = models.ForeignKey(ExtraBetPost, on_delete=models.CASCADE, related_name='attempts')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='extra_bet_attempts')
    submitted_answer = models.TextField()
    is_correct = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']
        unique_together = ('post', 'user')

    def __str__(self):
        return f"{self.user.username} -> post {self.post_id} ({'ok' if self.is_correct else 'ko'})"


# ─── Q&A publiques pour la page démo chat ──────────────────────────────────
class PublicDemoQA(models.Model):
    """
    Questions/réponses réelles générées par l'IA, visibles par tous les visiteurs
    sur la page démo du coach. Une entrée par matière, régénérable par un admin.
    """
    matiere     = models.CharField(max_length=50, unique=True)
    question    = models.TextField()
    answer      = models.TextField()
    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['matiere']

    def __str__(self):
        return f"DemoQA [{self.matiere}] — {self.question[:60]}"