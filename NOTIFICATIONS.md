# Notifications push OU TOU BON

Les notifications partent via **Firebase Cloud Messaging** (navigateur / PWA), même si l’app est fermée. Un modal interne propose d’abord d’activer les notifs ; le prompt système du navigateur ne s’ouvre qu’après le clic sur **Activer**.

Moment : Messages (~8 s), Accueil (~35 s), ou une page calme après ~50 s / 3 visites. Pas pendant quiz, duel, examen, match, exercices, ni pendant le modal du nom du coach. « Plus tard » = snooze 24 h, puis 7 jours après 3 refus.

Les rappels du soir (~18h Port-au-Prince) partent tout seuls via le scheduler Django (`core/push_scheduler.py`). Commande manuelle :

```bash
python manage.py send_push_digests
```

---

## Instantanées (dès que ça se passe)

### Messages
| ID | Quand | Titre type | Page |
|---|---|---|---|
| `dm` | Message privé reçu | Nom de l’ami + aperçu | Messages, conversation |
| `friend_request` | Quelqu’un t’envoie une demande | Nouvelle demande d’ami | Messages |
| `friend_accepted` | Ta demande est acceptée | Demande acceptée | Messages, chat |
| `group_mention` | On te tague dans un groupe | X t’a mentionné | Messages, groupe |
| `group_reply` | On répond à ton message de groupe | X a répondu | Messages, groupe |
| `group_everyone` | @tout le monde | Groupe · @tout le monde | Messages, groupe |
| `group_invite` | On t’ajoute à un groupe | Invitation dans un groupe | Messages, groupe |
| `admin_annonce` | Annonce OUTOUBON (1 élève ou tout le monde) | Annonce OUTOUBON | Messages |

### Match / duel
| ID | Quand | Titre type | Page |
|---|---|---|---|
| `match_found` | Adversaire trouvé en file d’attente | Adversaire trouvé ! | Match |
| `duel_joined` | Quelqu’un rejoint ton duel privé | Duel rejoint | Duel |
| `duel_result` | L’adversaire a fini le duel | Duel terminé | Match |

### Extra bèt
| ID | Quand | Titre type | Page |
|---|---|---|---|
| `extra_bet_like` | Like sur ta question | Nouveau like Extra bèt | Extra bèt |
| `extra_bet_answer` | Quelqu’un tente ta question | Extra bèt | Extra bèt |

### Groupe de Génies
Toutes les alertes Génies déjà en base (invitation, demande d’équipe, acceptation, transfert de capitaine, défi, match à jouer, résultat, compétition lancée, rappel 15 min) partent aussi en push (`genius`).

### Compte / Premium / XP
| ID | Quand | Titre type | Page |
|---|---|---|---|
| `premium_on` | Abonnement activé (paiement ou cadeau) | Premium activé | Accueil |
| `referral` | Filleul qui s’abonne | Parrainage validé | Gains |
| `xp_withdraw` | Retrait XP approuvé ou refusé | Retrait XP… | Gains |
| `device_switch` | Nouvel appareil essaie de se connecter | Nouvel appareil | Profil |
| `streak_milestone` | Série 7 / 14 / 30 / 60 / 100 jours | Série de X jours ! | Accueil |

---

## Rappels planifiés (`send_push_digests`)

1 envoi max par type et par jour et par élève. La série en danger et l’inactivité ne se cumulent pas le même jour.

| ID | Condition | Message |
|---|---|---|
| `streak_risk` | Série ≥ 2 jours et aucune activité aujourd’hui | Ta série de X jours va se briser — 5 min suffisent |
| `inactive_2d` | Pas vu depuis 2 jours | On ne t’a pas vu depuis 2 jours |
| `inactive_3d` | Pas vu depuis 3 jours | On ne t’a pas vu depuis 3 jours |
| `inactive_7d` | Pas vu depuis 7 jours | On ne t’a pas vu depuis 7 jours |
| `missions_left` | Pas encore d’activité du jour | Missions du jour (quiz / exo / coach) |
| `mistakes_due` | Erreurs à revoir aujourd’hui | Révisions à revoir |
| `plan_today` | Séance du plan de révision ce jour | Séance du plan aujourd’hui |
| `coach_quiet` | Aucune question au coach depuis 3 jours | Ton coach t’attend |
| `premium_3d` | Premium expire dans 3 jours | Premium expire dans 3 jours |
| `premium_1d` | Premium expire demain | Premium expire demain |
| `premium_today` | Premium expire aujourd’hui | Premium expire aujourd’hui |
| `premium_expired` | Premium expiré hier | Premium expiré |

---

## Pages couvertes

| Page | Notifs liées |
|---|---|
| Accueil | Série, missions, inactivité, palier de série |
| Messages | DM, amis, groupes, annonces |
| Match / Duel | File d’attente, duel privé, résultat |
| Extra bèt | Like, réponse sur ta question |
| Groupe de Génies | Toutes les alertes équipe / match / compétition |
| Chat IA | Rappel si tu n’as pas parlé au coach |
| Exercices | Rappels d’erreurs à revoir |
| Plan de révision | Séance du jour |
| Quiz | Missions du jour |
| Cours / Fiches / Bibliothèque / Examen blanc / Favoris | Couverts par missions + inactivité + série (pas de spam à chaque ouverture) |
| Gains / Premium | Parrainage, retrait XP, expiration Premium |
| Profil | Changement d’appareil |

Les pages Cours, Fiches, Bibliothèque, Examen blanc et Favoris n’ont **pas** une push à chaque action (trop bruyant). Elles sont couvertes par la série, les missions du soir et « on ne t’a pas vu ».

---

## Technique

- Jetons : table `PushDevice`
- Anti-doublon rappels : table `PushReceipt`
- Service worker : `/firebase-messaging-sw.js`
- Enregistrement : `POST /dashboard/api/push/register/`
- Credentials Admin SDK : `credentials/htbac-d22cb-firebase-adminsdk.json` (gitignoré)
- Si l’élève est **déjà sur la page concernée**, la push n’est pas affichée au premier plan.
