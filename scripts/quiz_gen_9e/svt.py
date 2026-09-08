"""SVT 9e AF — biologie, physique, chimie."""
from __future__ import annotations

import math
import random

from .common import make_q, shuffle_wrong_options

BIO_STATIC = [
    ("La cellule est l'unité de base de", "tous les organismes vivants", ["seulement les animaux", "seulement les roches", "l'eau"], "Biologie cellulaire."),
    ("Le noyau contient", "l'ADN", ["l'eau seule", "le sable", "l'air"], "Matériel génétique."),
    ("Les mitochondries produisent", "l'énergie (ATP)", ["la lumière", "le son", "la gravité"], "Respiration cellulaire."),
    ("La photosynthèse produit", "glucose et O₂", ["CO₂ seul", "pétrole", "métal"], "Plantes — chlorophylle."),
    ("La chlorophylle donne la couleur", "verte", ["rouge", "bleue", "noire"], "Pigment."),
    ("Les prokaryotes n'ont pas de", "noyau", ["membrane", "cytoplasme", "ADN"], "Bactéries."),
    ("Les eucaryotes ont un", "noyau", ["pas de membrane", "pas d'ADN", "pas de cellule"], "Cellules complexes."),
    ("L'osmose est le passage d'eau par", "membrane semi-perméable", ["roche", "métal", "bois"], "Diffusion eau."),
    ("En hypertonique, la cellule", "se déshydrate", ["explose", "grandit", "devient métal"], "Eau sort."),
    ("En hypotonique, la cellule peut", "grossir", ["devenir roche", "disparaître", "devenir métal"], "Eau entre."),
    ("En isotonique, pas de", "mouvement net d'eau", ["vie", "ADN", "noyau"], "Équilibre."),
    ("Enzyme accélère une", "réaction chimique", ["guerre", "route", "montagne"], "Catalyseur biologique."),
    ("L'ADN est une double", "hélice", ["ligne", "cercle", "carré"], "Structure Watson-Crick."),
    ("ARN messager transporte l'info de", "l'ADN aux ribosomes", ["la lune", "mars", "soleil"], "Expression génique."),
    ("Mutation change la", "séquence ADN", ["couleur drapeau", "route", "montagne"], "Génétique."),
    ("Allèle = version d'un", "gène", ["chromosome entier", "organisme", "planète"], "Génétique."),
    ("Dominant masque souvent", "récessif", ["dominant", "rien", "tout"], "Hérédité."),
    ("Homozygote : deux allèles", "identiques", ["différents", "zéro", "infinis"], "Génotype."),
    ("Hétérozygote : deux allèles", "différents", ["identiques", "zéro", "aucun"], "Génotype."),
    ("Mitose produit deux cellules", "identiques", ["différentes", "zéro", "infinies"], "Division."),
    ("Méiose produit cellules avec", "n chromosomes", ["2n", "3n", "4n"], "Gamètes."),
    ("Chromosome humain : 46 en", "cellule somatique", ["23 seul", "92", "12"], "2n=46."),
    ("Gamète humain :", "23 chromosomes", ["46", "92", "12"], "n=23."),
    ("Fécondation restaure", "2n", ["n", "0", "4n"], "Zygote."),
    ("Système nerveux contrôle", "réactions rapides", ["digestion lente", "photosynthèse", "roche"], "Neurones."),
    ("Neurone transmet signal par", "électrochimie", ["pierre", "bois", "sable"], "Synapse."),
    ("Synapse : jonction entre", "neurones", ["rochers", "nuages", "montagnes"], "Neurotransmetteurs."),
    ("Cerveau partie du système", "nerveux central", ["digestif", "respiratoire", "squelettique"], "CNS."),
    ("Œil : rétine capte", "lumière", ["son", "odeur", "taste"], "Vision."),
    ("Oreille interne :", "cochlée", ["rétine", "poumon", "foie"], "Audition."),
    ("Peau : organe de", "toucher", ["vision", "audition", "odorat seul"], "Récepteurs."),
    ("Système digestif absorbe", "nutriments", ["lumière", "son", "magnétisme"], "Intestin."),
    ("Estomac produit", "acide chlorhydrique", ["oxygène", "diamant", "or"], "Digestion."),
    ("Pancréas produit", "insuline", ["hémoglobine", "chlorophylle", "keratine"], "Hormone."),
    ("Insuline régule", "glycémie", ["température", "gravité", "lumière"], "Diabète."),
    ("Foie filtre", "toxines", ["lumière", "son", "magnétisme"], "Détox."),
    ("Poumon échange", "O₂ et CO₂", ["roche et sable", "métal et bois", "eau et pierre"], "Respiration."),
    ("Hémoglobine transporte", "O₂", ["CO₂ seul", "roche", "métal"], "Globules rouges."),
    ("Cœur pompe", "le sang", ["l'air seul", "la lumière", "le son"], "Circulation."),
    ("Artère transporte sang du cœur vers", "organes", ["cœur seul", "lune", "mars"], "Vasculaire."),
    ("Veine ramène sang vers", "le cœur", ["la lune", "mars", "soleil"], "Vasculaire."),
    ("Système immunitaire combat", "pathogènes", ["gravité", "magnétisme", "lumière"], "Anticorps."),
    ("Anticorps ciblent", "antigènes", ["rochers", "nuages", "montagnes"], "Immunité."),
    ("Vaccin stimule", "immunité", ["peur", "guerre", "censure"], "Mémoire immunitaire."),
    ("Antibiotique cible", "bactéries", ["virus typiques", "rochers", "nuages"], "Pas virus."),
    ("Virus besoin d'", "hôte", ["roche", "métal", "bois"], "Parasite."),
    ("Bactérie unicellulaire", "procaryote", ["eucaryote", "multicellulaire", "minéral"], "Prokaryote."),
    ("Champignon :", "eucaryote", ["procaryote", "minéral", "métal"], "Royaume fungi."),
    ("Écosystème = communauté +", "milieu physique", ["lune", "mars", "soleil"], "Biologie."),
    ("Producteur :", "plante", ["décomposeur", "roche", "métal"], "Photosynthèse."),
    ("Décomposeur :", "bactéries/fungi", ["plante", "roche", "métal"], "Cycle matière."),
    ("Chaîne alimentaire : énergie du", "soleil", ["lune", "roche", "métal"], "Flux énergie."),
    ("Pyramide énergétique : perte en", "chaleur", ["roche", "métal", "bois"], "2e loi thermo."),
    ("Niche écologique : rôle d'un", "organisme", ["rocher", "nuage", "montagne"], "Écologie."),
    ("Biodiversité = diversité", "espèces", ["rochers", "nuages", "montagnes"], "Variété vie."),
    ("Espèce endémique : présente seulement en", "une région", ["toute la galaxie", "aucun lieu", "lune"], "Endémisme."),
    ("Déforestation augmente", "érosion", ["forêts", "glace", "neige"], "Sol."),
    ("Pollution eau : eutrophisation par", "nutriments excessifs", ["air pur", "lumière", "son"], "Algues."),
    ("Couche d'ozone protège de", "UV", ["gravité", "magnétisme", "son"], "Stratosphère."),
    ("Acidification oceans par", "CO₂", ["O₂", "N₂", "H₂"], "pH baisse."),
    ("Climat : changement lié aux", "GES", ["rochers", "nuages", "montagnes"], "CO₂ CH4."),
    ("Photosynthèse : CO₂ + H₂O →", "glucose + O₂", ["roche + métal", "sable + pierre", "rien"], "Équation."),
    ("Respiration cellulaire inverse : glucose + O₂ →", "CO₂ + H₂O + énergie", ["roche", "métal", "bois"], "Aérobie."),
    ("Fermentation produit", "alcool ou lactate", ["diamant", "or", "fer pur"], "Anaérobie."),
    ("ADN réplication avant", "mitose", ["guerre", "route", "montagne"], "Semi-conservative."),
    ("Protéine synthèse : ADN → ARN →", "protéine", ["roche", "métal", "bois"], "Central dogma."),
    ("Mutation ponctuelle change", "un nucléotide", ["tout chromosome", "planète", "étoile"], "SNP."),
    ("Génie génétique : insertion gène =", "transgénique", ["roche", "métal", "bois"], "OGM."),
    ("Sélection naturelle : survie des", "plus adaptés", ["moins adaptés", "rochers", "nuages"], "Darwin."),
    ("Homéostasie : équilibre", "interne", ["externe chaos", "guerre", "censure"], "Régulation."),
    ("Feedback négatif stabilise", "variable", ["chaos", "guerre", "censure"], "Thermostat."),
    ("Hormone thyroïde régule", "métabolisme", ["gravité", "magnétisme", "lumière"], "Endocrinien."),
    ("Adrénaline prépare", "fight or flight", ["sommeil", "digestion lente", "roche"], "Stress."),
    ("Squelette protège", "organes", ["nuages", "montagnes", "rivières"], "Os."),
    ("Muscle squelettique : contraction", "volontaire", ["involontaire seule", "aucune", "roche"], "Mouvement."),
    ("Articulation relie", "os", ["nuages", "montagnes", "rivières"], "Synoviale."),
    ("Peau : couche externe", "épiderme", ["foie", "poumon", "cœur"], "Kératine."),
    ("Melanin protège de", "UV", ["gravité", "son", "magnétisme"], "Pigment."),
    ("Reproduction sexuée : diversité", "génétique", ["nulle", "zéro", "infinie négative"], "Méiose."),
    ("Reproduction asexuée : clones", "identiques", ["tous différents", "rochers", "nuages"], "Budding etc."),
    ("Plante : racine absorbe", "eau et minéraux", ["lumière", "son", "magnétisme"], "Absorption."),
    ("Tige transporte", "sève", ["roche", "métal", "bois"], "Vasculaire."),
    ("Feuille : stomates régulent", "transpiration", ["gravité", "magnétisme", "lumière"], "Gaz."),
    ("Pollinisation transfert", "pollen", ["roche", "métal", "bois"], "Reproduction."),
    ("Graine contient", "embryon", ["roche", "métal", "bois"], "Germination."),
    ("Germination besoin", "eau et O₂", ["roche", "métal", "bois"], "Début vie."),
    ("Vertèbre : animal avec", "colonne vertébrale", ["exosquelette", "roche", "métal"], "Chordés."),
    ("Invertébré : sans", "colonne vertébrale", ["tête", "cœur", "cellules"], "Invertébrés."),
    ("Arthropode : exosquelette et", "articulations", ["roche", "métal", "bois"], "Insectes."),
    ("Mollusque : corps", "mou", ["roche", "métal", "bois"], "Escargot."),
    ("Poisson : respiration par", "branchies", ["poumons", "peau seule", "roche"], "Aquatique."),
    ("Amphibien : vie", "eau et terre", ["lune", "mars", "soleil"], "Frog."),
    ("Reptile : peau", "écailles", ["poils", "plumes", "roche"], "Serpent."),
    ("Oiseau : plumes et", "bec", ["écailles", "fourrure", "roche"], "Vol."),
    ("Mammifère : nourrit jeunes avec", "lait", ["roche", "métal", "bois"], "Mammalia."),
    ("Homo sapiens : espèce", "humaine", ["roche", "métal", "bois"], "Primates."),
    ("Système solaire : 8", "planètes", ["100", "0", "1"], "Depuis 2006."),
    ("Terre : 3e planète du", "système solaire", ["galaxie", "univers", "lune"], "Orbite."),
    ("Rotation Terre : jour", "24 h", ["1 h", "365 j", "10 j"], "Autour axe."),
    ("Révolution Terre : année", "365 j", ["24 h", "1 h", "10 j"], "Autour soleil."),
    ("Saison due à inclinaison", "axe terrestre", ["lune", "mars", "jupiter"], "23,5°."),
    ("Lune phases : cycle ~", "29,5 jours", ["1 jour", "365 jours", "10 ans"], "Synodique."),
    ("Éclipse solaire : lune entre", "Terre et Soleil", ["Mars et Jupiter", "lune et mars", "rien"], "Alignement."),
    ("Éclipse lunaire : Terre entre", "Soleil et Lune", ["Mars et Jupiter", "rien", "roche"], "Alignement."),
    ("Tectonique plaques : mouvement", "lentes", ["instant", "nul", "inversé"], "Géologie."),
    ("Volcan : magma à", "surface", ["espace", "lune", "mars"], "Éruption."),
    ("Séisme : rupture", "roches", ["nuages", "montagnes légères", "rêves"], "Frottement."),
    ("Roches ignées : solidification", "magma", ["sable seul", "bois", "métal"], "Volcanique."),
    ("Roches sédimentaires : dépôt et", "compactage", ["fusion", "guerre", "censure"], "Strates."),
    ("Roches métamorphiques : changement par", "chaleur/pression", ["musique", "danse", "peinture"], "Métamorphisme."),
    ("Cycle de l'eau : évaporation,", "condensation, précipitation", ["guerre", "censure", "dictature"], "Hydrologie."),
    ("Atmosphère : gaz majoritaire", "N₂", ["O₂ seul", "CO₂ seul", "H₂ seul"], "78% N2."),
    ("O₂ ~", "21%", ["78%", "1%", "50%"], "Atmosphère."),
    ("CO₂ : gaz à effet de", "serre", ["refroidissement seul", "gravité", "magnétisme"], "GES."),
    ("pH 7 =", "neutre", ["acide", "basique", "métal"], "Échelle."),
    ("pH < 7 =", "acide", ["basique", "neutre", "métal"], "Acide."),
    ("pH > 7 =", "basique", ["acide", "neutre", "métal"], "Alcalin."),
    ("Acide + base →", "eau + sel", ["roche + métal", "bois + pierre", "rien"], "Neutralisation."),
    ("Combustion besoin", "combustible + O₂", ["roche seul", "métal seul", "bois seul"], "Oxydation."),
    ("Oxydation : gain", "oxygène", ["perte O2", "gain H", "rien"], "Rouille."),
    ("Réduction : gain", "électrons", ["perte électrons", "roche", "métal"], "Redox."),
    ("Masse unité SI :", "kilogramme", ["livre", "once", "stone"], "kg."),
    ("Longueur unité SI :", "mètre", ["pied", "yard", "mile"], "m."),
    ("Temps unité SI :", "seconde", ["minute seule", "heure seule", "jour seule"], "s."),
    ("Force unité SI :", "newton", ["joule", "watt", "pascal"], "N."),
    ("Énergie unité SI :", "joule", ["newton", "watt", "pascal"], "J."),
    ("Puissance unité SI :", "watt", ["joule", "newton", "pascal"], "W."),
    ("Pression unité SI :", "pascal", ["newton", "joule", "watt"], "Pa."),
    ("Vitesse = distance /", "temps", ["masse", "volume", "température"], "m/s."),
    ("Accélération = changement vitesse /", "temps", ["masse", "volume", "odeur"], "m/s²."),
    ("Gravité Terre ~", "9,8 m/s²", ["1 m/s²", "100 m/s²", "0 m/s²"], "g."),
    ("Masse vs poids : poids = masse ×", "g", ["vitesse", "température", "odeur"], "Force."),
    ("Inertie : résistance au changement de", "mouvement", ["couleur", "odeur", "taste"], "Newton."),
    ("F=ma : force = masse ×", "accélération", ["vitesse", "température", "odeur"], "2e loi."),
    ("Action-réaction : forces", "égales et opposées", ["nulles", "infinies", "aléatoires"], "3e loi."),
    ("Travail = force ×", "distance", ["temps", "masse", "odeur"], "J."),
    ("Énergie cinétique Ec = ½mv²", "vitesse", ["masse seule", "température", "odeur"], "Mouvement."),
    ("Énergie potentielle gravitaire Ep =", "mgh", ["mv", "ma", "mc"], "Hauteur."),
    ("Conservation énergie : total", "constant", ["infini", "nul", "aléatoire"], "1er principe."),
    ("Chaleur flux de", "énergie thermique", ["masse", "volume", "odeur"], "Température."),
    ("Température mesure", "agitation moléculaire", ["masse", "volume", "odeur"], "Thermo."),
    ("Conducteur thermique :", "métal", ["bois", "plastique", "air stagnant"], "Chaleur."),
    ("Isolant thermique :", "fibre/laine", ["cuivre", "aluminium", "fer"], "Réduit flux."),
    ("Changement état : fusion solide →", "liquide", ["gaz", "plasma", "roche"], "Chaleur."),
    ("Évaporation liquide →", "gaz", ["solide", "plasma", "roche"], "Surface."),
    ("Condensation gaz →", "liquide", ["solide", "plasma", "roche"], "Nuages."),
    ("Courant électrique : flux d'", "électrons", ["protons seuls", "neutrons", "photons seuls"], "Ampère."),
    ("Voltage :", "différence potentiel", ["masse", "volume", "odeur"], "Volt."),
    ("Résistance unité :", "ohm", ["watt", "joule", "newton"], "Ω."),
    ("P = VI : puissance =", "voltage × courant", ["masse × vitesse", "odeur × taste", "rien"], "Watt."),
    ("Circuit série : courant", "identique", ["différent", "nul", "infini"], "Série."),
    ("Circuit parallèle : voltage", "identique", ["différent", "nul", "infini"], "Branches."),
    ("Ampère mesure", "courant", ["voltage", "résistance", "odeur"], "A."),
    ("Volt mesure", "voltage", ["courant", "résistance", "odeur"], "V."),
    ("Ohm mesure", "résistance", ["courant", "voltage", "odeur"], "Ω."),
    ("Magnétisme : pôle Nord attire", "Sud", ["Nord", "rien", "tout"], "Magnét."),
    ("Électromagnét : courant crée", "champ magnétique", ["gravité", "odeur", "taste"], "Solénoïde."),
    ("Lumière vitesse ~", "3×10⁸ m/s", ["3 m/s", "300 m/s", "3×10¹⁵ m/s"], "c."),
    ("Réflexion angle incident = angle", "réfléchi", ["réfracté", "diffracté", "absorbé"], "Optique."),
    ("Réfraction : changement direction en changeant", "milieu", ["masse", "odeur", "taste"], "Indice."),
    ("Prisme disperse", "lumière blanche", ["son", "odeur", "taste"], "Couleurs."),
    ("Son : vibration", "mécanique", ["lumineuse", "magnétique", "gravitationnelle"], "Longitudinale."),
    ("Fréquence son unité", "hertz", ["newton", "joule", "pascal"], "Hz."),
    ("Ultrason fréquence >", "20 kHz", ["20 Hz", "200 Hz", "2 Hz"], "Audible <20k."),
    ("Décibel mesure", "intensité sonore", ["masse", "volume", "odeur"], "dB."),
    ("Onde : amplitude lie à", "énergie", ["masse", "odeur", "taste"], "Hauteur."),
    ("Longueur onde λ liée fréquence f et vitesse v : v =", "fλ", ["f/λ", "λ/f", "f+λ"], "Onde."),
    ("Radioactivité : désintégration", "nucléaire", ["chimique", "biologique", "mécanique"], "Noyau."),
    ("Isotope : même protons, différents", "neutrons", ["protons", "électrons", "photons"], "Nucléaire."),
    ("Demi-vie : temps pour moitié", "désintégrer", ["doubler", "tripler", "rien"], "T½."),
    ("Fusion : combinaison", "noyaux légers", ["atomes", "molécules", "rochers"], "Étoiles."),
    ("Fission : division", "noyau lourd", ["atome", "molécule", "rocher"], "Centrales."),
]


def _bio_static() -> list[dict]:
    out = []
    for q, c, w, e in BIO_STATIC:
        out.append(make_q(q, [c] + w[:3], 0, e, "Biologie", "moyen"))
    return out


def _physics_calc(rng: random.Random) -> list[dict]:
    out = []
    for _ in range(80):
        m = rng.randint(2, 20)
        v = rng.randint(2, 15)
        ec = 0.5 * m * v * v
        q = f"Énergie cinétique : m={m} kg, v={v} m/s. Ec = ? (J)"
        wrongs = [str(ec + rng.randint(5, 30)), str(max(1, ec - rng.randint(1, 10))), str(m * v)]
        opts, ci = shuffle_wrong_options(str(int(ec)), wrongs, rng)
        out.append(make_q(q, opts, ci, f"Ec = ½mv² = 0,5×{m}×{v}² = {int(ec)} J.", "Physique", "moyen"))
    for _ in range(60):
        d = rng.randint(10, 200)
        t = rng.randint(2, 20)
        v = d / t
        q = f"Distance {d} m en {t} s. Vitesse moyenne ? (m/s)"
        wrongs = [str(v + rng.randint(1, 5)), str(max(1, v - 1)), str(d + t)]
        opts, ci = shuffle_wrong_options(str(int(v)), wrongs, rng)
        out.append(make_q(q, opts, ci, f"v = d/t = {d}/{t} = {int(v)} m/s.", "Physique", "facile"))
    for _ in range(50):
        f = rng.randint(5, 50)
        d = rng.randint(2, 15)
        w = f * d
        q = f"Force {f} N, distance {d} m. Travail W ? (J)"
        wrongs = [str(w + 10), str(max(1, w - 5)), str(f + d)]
        opts, ci = shuffle_wrong_options(str(w), wrongs, rng)
        out.append(make_q(q, opts, ci, f"W = F×d = {f}×{d} = {w} J.", "Physique", "facile"))
    return out


def _chem_ph(rng: random.Random) -> list[dict]:
    out = []
    pairs = [
        ("pH 3", "acide", ["basique", "neutre", "métal"]),
        ("pH 11", "basique", ["acide", "neutre", "métal"]),
        ("pH 7", "neutre", ["acide", "basique", "métal"]),
        ("HCl", "acide", ["base", "sel", "eau seule"]),
        ("NaOH", "base", ["acide", "sel", "gaz"]),
        ("NaCl", "sel", ["acide", "base", "métal"]),
        ("CO₂ dans eau", "acide faible", ["base forte", "neutre", "métal"]),
        ("O₂", "gaz comburant", ["combustible", "acide", "base"]),
        ("H₂", "gaz combustible", ["comburant", "acide", "base"]),
        ("Cuivre symbole", "Cu", ["Co", "C", "Ca"]),
        ("Fer symbole", "Fe", ["F", "Fr", "Fi"]),
        ("Eau formule", "H₂O", ["HO", "H₂O₂", "OH"]),
        ("CO₂ formule", "CO₂", ["CO", "C₂O", "O₂C"]),
        ("NaCl formule", "NaCl", ["NaC", "NCl", "Na₂Cl"]),
    ]
    for q, c, w in pairs:
        opts, ci = shuffle_wrong_options(c, w, rng)
        out.append(make_q(f"En chimie : {q} est", opts, ci, f"{q} → {c}.", "Chimie", "facile"))
    return out


def generate_svt(rng: random.Random, seen: set[str]) -> list[dict]:
    out = _bio_static() + _physics_calc(rng) + _chem_ph(rng)
    rng.shuffle(out)
    return out
