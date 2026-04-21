"""
Simple and clean physics exercise generator.
Each exercise includes complete content, solution, and formula-based hints.
Formulas are generated via formula_utils.py to ensure consistency and correctness.
"""

import hashlib
import random
from typing import Dict, List, Any
from .formula_utils import (
    fmt_formula, 
    mag_field_infinite_wire,
    mag_field_solenoid,
    faraday_law,
    magnetic_flux,
    induced_charge,
    right_hand_rule,
    proportionality_magnetic_field,
    constant_mu0
)


def _get_seed(section_id: str, index: int) -> int:
    """Generate deterministic seed from section and index"""
    return int(hashlib.md5(f"{section_id}::{index}".encode()).hexdigest()[:8], 16)


def generate_physics_exercise(section_title: str, section_id: str, index: int) -> Dict[str, Any]:
    """
    Generate a complete physics exercise with questions, solution, and formula-based hints.
    
    Returns a dict with:
    - title: Exercise title
    - intro: Introduction/context
    - questions: List of 3 questions
    - solution: Full solution with formulas
    - hints: List of 3 hints (each with formula)
    - conseils: Tips
    - difficulte: Difficulty level
    """
    seed = _get_seed(section_id, index)
    
    # Select random values based on seed
    def choose(values):
        return values[seed % len(values)]
    
    section_low = section_title.lower()
    
    # MAGNETIC FIELD EXERCISES
    if 'champ magn' in section_low or 'magnetic' in section_low:
        variant = index % 3
        
        if variant == 0:
            # Straight wire magnetic field - uses centralized formula utilities
            current = choose([8, 10, 12, 15])
            distance = choose([0.02, 0.03, 0.04, 0.05])
            
            current_str = f"{current}\\,A"
            distance_str = f"{distance}\\,m"
            
            return {
                'title': f'Exercice {index + 1} — {section_title}',
                'intro': (
                    f"Un fil rectiligne très long est parcouru par un courant continu de ${current_str}$. "
                    f"On étudie le champ magnétique en un point situé à ${distance_str}$ du fil."
                ),
                'enonce': f"Fil rectiligne: courant ${current_str}$, distance ${distance_str}$.",
                'questions': [
                    "Calcule l'intensité du champ magnétique $B$ au point considéré.",
                    "Précise la direction et le sens du vecteur champ magnétique.",
                    "Détermine la nouvelle valeur de $B$ si on double la distance."
                ],
                'solution': (
                    f"Pour un fil rectiligne infini: $B = \\dfrac{{\\mu_0 I}}{{2\\pi d}}$ "
                    f"avec $\\mu_0 = 4\\pi \\times 10^{{-7}}$ T.m/A, $I = {current_str}$, $d = {distance_str}$. "
                    f"Le résultat: {mag_field_infinite_wire(current, distance)['result']}. "
                    f"Le champ est tangentiel (règle de la main droite). "
                    f"Si $d$ double, $B$ est divisé par 2."
                ),
                'hints': [
                    f"Applique la formule: $B = \\dfrac{{\\mu_0 I}}{{2\\pi d}}$ avec $\\mu_0 = 4\\pi \\times 10^{{-7}}$ T.m/A. "
                    f"Remplace $I = {current_str}$ et $d = {distance_str}$.",
                    f"La règle de la main droite donne: $\\vec{{B}}$ est tangentiel au cercle autour du fil (perpendiculaire au plan formé par le fil et le point d'étude).",
                    f"Relation: $B \\propto I$ et $B \\propto 1/d$. Si distance double, $B$ diminue de moitié."
                ],
                'conseils': "Les distances doivent être en mètres. Utilise les valeurs de constantes.",
                'difficulte': 'moyen' if index == 0 else 'intermediaire' if index == 1 else 'avance',
                'theme': section_title,
                'source': 'Générateur d\'exercices physique'
            }
        
        elif variant == 1:
            # Solenoid - using centralized formula utilities
            n_spires = choose([300, 400, 500, 600])
            length = choose([0.3, 0.4, 0.5, 0.6])
            current = choose([1.5, 2.0, 2.5, 3.0])
            
            n_str = f"{n_spires}"
            l_str = f"{length}\\,m"
            i_str = f"{current}\\,A"
            
            mag_data = mag_field_solenoid(n_spires, length, current)
            
            return {
                'title': f'Exercice {index + 1} — {section_title}',
                'intro': (
                    f"Un long solénoïde de {n_str} spires et de longueur ${l_str}$ "
                    f"est traversé par un courant de ${i_str}$."
                ),
                'enonce': f"Solénoïde: {n_str} spires, longueur ${l_str}$, courant ${i_str}$.",
                'questions': [
                    "Calcule le champ magnétique $B$ au centre du solénoïde.",
                    "Explique pourquoi le champ est uniforme au voisinage du centre.",
                    "Compare le champ si le nombre de spires double."
                ],
                'solution': (
                    f"Au centre d'un solénoïde long: $B = \\mu_0 \\dfrac{{N}}{{L}} I$ "
                    f"= {mag_data['formula_with_values']}. "
                    f"Résultat: {mag_data['result']}. "
                    f"Le champ est uniforme car les lignes sont parallèles au centre. "
                    f"Si $N$ double (même $L$), $B$ double aussi."
                ),
                'hints': [
                    f"Formule du solénoïde: $B = \\mu_0 \\dfrac{{N}}{{L}} I$ avec $\\mu_0 = 4\\pi \\times 10^{{-7}}$ T.m/A, "
                    f"$N = {n_str}$, $L = {l_str}$, $I = {i_str}$.",
                    "Uniformité: Au centre, les spires créent des champs parallèles et équidistants, donnant $B$ constant.",
                    f"Densité de spires $N/L = {n_spires/length:.0f}$ spires/m. Proportionnalité: $B \\propto N/L$."
                ],
                'conseils': "La clé est la densité de spires $N/L$. Calcule correctement cette densité.",
                'difficulte': 'moyen' if index == 0 else 'intermediaire' if index == 1 else 'avance',
                'theme': section_title,
                'source': 'Générateur d\'exercices physique'
            }
        
        else:
            # Earth's magnetic field
            bh = choose([2.0e-5, 2.2e-5, 2.5e-5, 3.0e-5])
            bv = choose([3.0e-5, 3.4e-5, 3.8e-5, 4.2e-5])
            
            return {
                'title': f'Exercice {index + 1} — {section_title}',
                'intro': (
                    f"Le champ magnétique terrestre possède une composante horizontale ${bh:.2e}\\,T$ "
                    f"et une composante verticale ${bv:.2e}\\,T$."
                ),
                'enonce': f"Champ terrestre: horizontal ${bh:.2e}\\,T$, vertical ${bv:.2e}\\,T$.",
                'questions': [
                    "Calcule l'intensité totale du champ magnétique terrestre.",
                    "Détermine l'angle d'inclinaison magnétique.",
                    "Explique la différence entre inclinaison et déclinaison magnétique."
                ],
                'solution': (
                    f"Intensité totale: $B = \\sqrt{{B_h^2 + B_v^2}} = \\sqrt{{({bh:.2e})^2 + ({bv:.2e})^2}}$. "
                    f"Inclinaison: $\\tan i = \\dfrac{{B_v}}{{B_h}} = \\dfrac{{{bv:.2e}}}{{{bh:.2e}}}$. "
                    f"Inclinaison = angle vertical, déclinaison = angle horizontal."
                ),
                'hints': [
                    f"Intensité totale par Pythagore: $B = \\sqrt{{B_h^2 + B_v^2}}$ avec $B_h = {bh:.2e}\\,T$, $B_v = {bv:.2e}\\,T$.",
                    f"Inclinaison: $\\tan i = \\dfrac{{B_v}}{{B_h}} = \\dfrac{{{bv:.2e}}}{{{bh:.2e}}}$. Utilise l'arctangente.",
                    "Inclinaison mesure l'angle par rapport à l'horizontale. Déclinaison mesure l'écart du nord géographique."
                ],
                'conseils': "Utilise Pythagore pour la magnitude totale. Pour l'angle, utilise l'arctangente du rapport.",
                'difficulte': 'avance',
                'theme': section_title,
                'source': 'Générateur d\'exercices physique'
            }
    
    # INDUCTION / FARADAY EXERCISES
    elif 'induction' in section_low or 'solénoïde' in section_low or 'inductance' in section_low or 'faraday' in section_low or 'flux' in section_low:
        variant = index % 3
        
        if variant == 0:
            # Magnetic flux
            b = choose([0.2, 0.25, 0.3, 0.4])
            area = choose([8e-4, 1e-3, 1.2e-3, 1.5e-3])
            angle = choose([0, 30, 45, 60])
            
            return {
                'title': f'Exercice {index + 1} — {section_title}',
                'intro': (
                    f"Une spire plane de surface ${area:.2e}\\,m^2$ est dans un champ uniforme de ${b}\\,T$. "
                    f"La normale fait un angle de ${angle}°$ avec le champ."
                ),
                'enonce': f"Flux: champ ${b}\\,T$, surface ${area:.2e}\\,m^2$, angle ${angle}°$.",
                'questions': [
                    "Calcule le flux magnétique $\\Phi$ à travers la spire.",
                    "Quel flux si la spire devient parallèle aux lignes de champ?",
                    "Dans quel cas le flux est-il maximal?"
                ],
                'solution': (
                    f"Flux: $\\Phi = BS\\cos\\theta$ avec $B = {b}\\,T$, $S = {area:.2e}\\,m^2$, $\\theta = {angle}°$. "
                    f"Si spire parallèle aux lignes (normale perpendiculaire): $\\theta = 90°$, $\\Phi = 0$. "
                    f"Maximal quand $\\theta = 0°$ ($\\cos 0 = 1$)."
                ),
                'hints': [
                    f"Formule: $\\Phi = BS\\cos\\theta$ where $\\theta$ is angle between FIELD and NORMAL. Use $B = {b}\\,T$, $S = {area:.2e}\\,m^2$, $\\theta = {angle}°$.",
                    "Si spire devient parallèle aux lignes, sa normale devient perpendiculaire au champ ($\\theta = 90°$), donc $\\cos 90° = 0$ et $\\Phi = 0$.",
                    "Flux maximal: $\\Phi_{max} = BS$ quand $\\cos\\theta = 1$, c'est-à-dire quand la normale est parallèle au champ."
                ],
                'conseils': "Attention: l'angle est entre le CHAMP et la NORMALE, pas entre le champ et le plan.",
                'difficulte': 'moyen',
                'theme': section_title,
                'source': 'Générateur d\'exercices physique'
            }
        
        elif variant == 1:
            # Faraday's law
            dphi = choose([2e-3, 3e-3, 4e-3, 5e-3])
            dt = choose([0.02, 0.05, 0.08, 0.1])
            
            return {
                'title': f'Exercice {index + 1} — {section_title}',
                'intro': (
                    f"Le flux à travers un circuit varie de ${dphi:.2e}\\,Wb$ en ${dt}\\,s$. "
                    f"Trouve la f.e.m. induite."
                ),
                'enonce': f"Variation flux: $\\Delta\\Phi = {dphi:.2e}\\,Wb$ en $\\Delta t = {dt}\\,s$.",
                'questions': [
                    "Calcule la f.e.m. induite moyenne.",
                    "Explique le signe moins dans la loi de Faraday-Lenz.",
                    "Quel serait la f.e.m. si la variation était 2 fois plus rapide?"
                ],
                'solution': (
                    f"Loi de Faraday: $e = -\\dfrac{{\\Delta\\Phi}}{{\\Delta t}} = -\\dfrac{{{dphi:.2e}}}{{{dt}}} = {-dphi/dt:.4e}\\,V$. "
                    f"Le signe moins (Lenz) signifie que le courant induit s'oppose au changement. "
                    f"Si variation 2x plus rapide: $e = -\\dfrac{{\\Delta\\Phi}}{{\\Delta t/2}} = 2|e|$."
                ),
                'hints': [
                    f"Loi de Faraday: $|e| = \\left|\\dfrac{{\\Delta\\Phi}}{{\\Delta t}}\\right| = \\dfrac{{{dphi:.2e}}}{{{dt}}}$. "
                    f"Le signe moins traduit l'opposition de Lenz.",
                    f"Loi de Lenz: $e = -\\dfrac{{d\\Phi}}{{dt}}$ (signe moins). Le courant induit s'oppose au changement.",
                    f"Si variation 2x plus rapide ($\\Delta t/2$): $|e|$ double. Formule: $|e| \\propto 1/\\Delta t$."
                ],
                'conseils': "Calcule d'abord la valeur absolue. Le signe a une signification physique (direction du courant).",
                'difficulte': 'intermediaire',
                'theme': section_title,
                'source': 'Générateur d\'exercices physique'
            }
        
        else:
            # Induced charge
            dphi = choose([1.5e-3, 2.0e-3, 2.5e-3, 3.0e-3])
            r = choose([2, 4, 5, 8])
            
            return {
                'title': f'Exercice {index + 1} — {section_title}',
                'intro': (
                    f"Dans un circuit de résistance ${r}\\,\\Omega$, le flux varie de ${dphi:.2e}\\,Wb$. "
                    f"Trouve la charge induite."
                ),
                'enonce': f"Circuit: $R = {r}\\,\\Omega$, variation flux $\\Delta\\Phi = {dphi:.2e}\\,Wb$.",
                'questions': [
                    "Calcule la charge induite $Q$ qui traverse le circuit.",
                    "De quelles grandeurs dépend $Q$?",
                    "Pourquoi $Q$ est indépendant du temps?"
                ],
                'solution': (
                    f"Charge induite: $Q = \\dfrac{{|\\Delta\\Phi|}}{{R}} = \\dfrac{{{dphi:.2e}}}{{{r}}} = {dphi/r:.4e}\\,C$. "
                    f"$Q$ dépend de $|\\Delta\\Phi|$ et de $R$, mais PAS du temps $\\Delta t$. "
                    f"Raison: si variation plus rapide, $e$ plus grande mais durée plus courte → charge totale constante."
                ),
                'hints': [
                    f"La charge totale: $Q = \\dfrac{{|\\Delta\\Phi|}}{{R}}$ avec $\\Delta\\Phi = {dphi:.2e}\\,Wb$, $R = {r}\\,\\Omega$.",
                    f"Résistance: Plus haute $R$ → moins de courant. Loi: $I = \\dfrac{{e}}{{R}} = \\dfrac{{\\Delta\\Phi}}/{{\\Delta t \\cdot R}}$.",
                    f"$Q$ est indépendant de $\\Delta t$ parce que: $Q = \\dfrac{{|\\Delta\\Phi|}}{{R}}$ ne contient pas $t$."
                ],
                'conseils': "Charge totale vs f.e.m.: ce sont deux concepts distincts avec des formules différentes.",
                'difficulte': 'avance',
                'theme': section_title,
                'source': 'Générateur d\'exercices physique'
            }
    
    # LAPLACE FORCE EXERCISES
    elif 'laplace' in section_low or 'force de laplace' in section_low or 'galvanométre' in section_low:
        i = choose([2, 3, 5, 10])
        l = choose([0.05, 0.1, 0.15, 0.2])
        b = choose([0.1, 0.2, 0.5, 1.0])
        return {
            'title': f'Exercice {index + 1} — {section_title}',
            'intro': f"Un conducteur de longueur ${l}\\,m$ est immergé dans un champ $B = {b}\\,T$ et parcouru par ${i}\\,A$.",
            'enonce': f"Force Laplace: $I = {i}\\,A$, $L = {l}\\,m$, $B = {b}\\,T$.",
            'questions': [
                "Calcule la force de Laplace $F$ sur le conducteur.",
                "Quelle est la direction de cette force (règle de la main droite)?",
                "Que se passe-t-il si le champ est parallèle au conducteur?"
            ],
            'solution': f"$F = BIL\\sin\\theta$ où $\\theta = 90°$ (perpendiculaire), donc $F = {b} \\times {i} \\times {l} = {b*i*l}\\,N$.",
            'hints': [
                f"Formule: $F = BIL\\sin\\theta$ avec $B = {b}\\,T$, $I = {i}\\,A$, $L = {l}\\,m$, $\\theta = 90°$.",
                f"Pour $\\theta = 90°$: $\\sin 90° = 1$, donc $F = BIL = {b*i*l}\\,N$.",
                "Si champ parallèle au fil ($\\theta = 0°$): $\\sin 0° = 0$ donc $F = 0$. Force perpendiculaire au champ."
            ],
            'conseils': "Angle $\\theta$ est entre le CHAMP et le CONDUCTEUR, pas avec la normale.",
            'difficulte': 'intermediaire',
            'theme': section_title,
            'source': 'Générateur d\'exercices physique'
        }
    
    # AC CURRENT EXERCISES
    elif 'courant alternatif' in section_low or 'sinusoïdal' in section_low or 'ac' in section_low or 'alternatif' in section_low:
        amplitude = choose([310, 330, 360, 400])
        freq = choose([50, 60, 100])
        
        rms = amplitude / 1.414
        period = 1 / freq
        
        return {
            'title': f'Exercice {index + 1} — {section_title}',
            'intro': f"Tension sinusoïdale: $U(t) = {amplitude}\\sin(2\\pi\\times{freq}t)$ (volts).",
            'enonce': f"AC: Amplitude ${amplitude}\\,V$, fréquence ${freq}\\,Hz$.",
            'questions': [
                "Calcule la tension efficace $U_{{rms}}$.",
                "Calcule la période $T$ et la pulsation $\\omega$.",
                "À quel instant $t$ la tension atteint-elle son maximum pour la première fois?"
            ],
            'solution': (
                f"$U_{{rms}} = \\dfrac{{U_0}}{{\\sqrt{{2}}}} = \\dfrac{{{amplitude}}}{{\\sqrt{{2}}}} \\approx {rms:.0f}\\,V$. "
                f"$T = \\dfrac{{1}}{{f}} = \\dfrac{{1}}{{{freq}}} \\approx {period:.3f}\\,s$. "
                f"$\\omega = 2\\pi f = 2\\pi \\times {freq} \\approx {2*3.14159*freq:.1f}\\,rad/s$. "
                f"Maximum quand $\\sin = 1$: $t = \\dfrac{{T}}{{4}}$."
            ),
            'hints': [
                f"Efficace ($U_{{rms}}$): $U_{{rms}} = \\dfrac{{U_0}}{{\\sqrt{{2}}}} = \\dfrac{{{amplitude}}}{{1.414}} \\approx {rms:.0f}\\,V$.",
                f"Période: $T = \\dfrac{{1}}{{f}} = \\dfrac{{1}}{{{freq}}} = {period:.4f}\\,s$. Pulsation: $\\omega = 2\\pi f \\approx {2*3.14159*freq:.1f}\\,rad/s$.",
                f"Le maximum ($U = {amplitude}\\,V$) arrive à $t = \\dfrac{{T}}{{4}} = \\dfrac{{{period:.4f}}}{{4}} \\approx {period/4:.5f}\\,s$."
            ],
            'conseils': "Distingue amplitude vs valeur efficace. $U_{{rms}} = U_0/\\sqrt{{2}} \\approx 0.707\\times U_0$.",
            'difficulte': 'intermediaire',
            'theme': section_title,
            'source': 'Générateur d\'exercices physique'
        }
    
    # DEFAULT FALLBACK - Create meaningful exercises for unsupported sections
    else:
        # For chapters we don't have specific formulas yet, create generic but formula-containing exercises
        if 'pendule' in section_low or 'oscillation' in section_low:
            period = choose([0.5, 1.0, 1.5, 2.0])
            length = period ** 2 / (4 * 3.14159)  # T = 2π√(L/g)
            return {
                'title': f'Exercice {index + 1} — {section_title}',
                'intro': f"Un pendule simple de longueur ${length:.3f}\\,m$ oscille. Calcule sa période.",
                'enonce': f"Pendule: $L = {length:.3f}\\,m$, $g = 9.81\\,m/s^2$.",
                'questions': [
                    f"Calcule la période $T$ du pendule.",
                    "Explique l'indépendance de la période par rapport à l'amplitude (pour petites oscillations).",
                    "Que se passe-t-il si on double la longueur?"
                ],
                'solution': f"$T = 2\\pi\\sqrt{{\\dfrac{{L}}{{g}}}} = 2\\pi\\sqrt{{\\dfrac{{{length:.3f}}}{{9.81}}}} = {period:.3f}\\,s$.",
                'hints': [
                    f"Formule du pendule: $T = 2\\pi\\sqrt{{\\dfrac{{L}}{{g}}}}$ avec $L = {length:.3f}\\,m$, $g = 9.81\\,m/s^2$.",
                    f"Indépendance masse: $T$ ne contient PAS $m$. Donc $T$ pareil pour $m = 0.1\\,kg$ et $m = 1\\,kg$.",
                    f"Si L double: $T_{{nouv}} = \\sqrt{{2}} \\cdot T_{{anc}} \\approx 1.414 \\cdot T$."
                ],
                'conseils': "Utilise la longueur en mètres. sqrt(L/g) est la partie clé.",
                'difficulte': 'moyen',
                'theme': section_title,
                'source': 'Générateur d\'exercices physique'
            }
        
        elif 'projectile' in section_low or 'balistique' in section_low:
            v0 = choose([10, 15, 20, 25])
            angle = choose([30, 45, 60])
            return {
                'title': f'Exercice {index + 1} — {section_title}',
                'intro': f"Un projectile est lancé avec vitesse initiale ${v0}\\,m/s$ à ${angle}°$ de l'horizontale.",
                'enonce': f"Balistique: $v_0 = {v0}\\,m/s$, $\\theta = {angle}°$, $g = 10\\,m/s^2$.",
                'questions': [
                    "Calcule la portée (distance horizontale).",
                    "Calcule la hauteur maximale atteinte.",
                    "Calcule le temps de vol total."
                ],
                'solution': f"$R = \\dfrac{{v_0^2 \\sin(2\\theta)}}{{g}} = \\dfrac{{{v0}^2 \\times \\sin({2*angle}°)}}{{10}}$.",
                'hints': [
                    f"Portée: $R = \\dfrac{{v_0^2 \\sin(2\\theta)}}{{g}}$ avec $v_0 = {v0}\\,m/s$, $\\theta = {angle}°$.",
                    "Hauteur max: $h = \\dfrac{{(v_0 \\sin\\theta)^2}}{{2g}}$ (utilise la composante verticale de v₀).",
                    f"Temps de vol: $t = \\dfrac{{2 v_0 \\sin\\theta}}{{g}}$. Portée max quand $\\theta = 45°$."
                ],
                'conseils': "Décompose en composantes horizontale et verticale. sin(2θ) = 2sinθ cosθ.",
                'difficulte': 'intermediaire',
                'theme': section_title,
                'source': 'Générateur d\'exercices physique'
            }
        
        elif 'onde' in section_low or 'acoustique' in section_low:
            freq = choose([100, 200, 500, 1000])
            speed = choose([300, 340, 1500])
            return {
                'title': f'Exercice {index + 1} — {section_title}',
                'intro': f"Une onde de fréquence ${freq}\\,Hz$ se propage à ${speed}\\,m/s$.",
                'enonce': f"Onde: $f = {freq}\\,Hz$, $v = {speed}\\,m/s$.",
                'questions': [
                    "Calcule la longueur d'onde $\\lambda$.",
                    "La fréquence change-t-elle si le milieu change?",
                    "Que se passe-t-il si l'onde passe dans un milieu plus dense?"
                ],
                'solution': f"$\\lambda = \\dfrac{{v}}{{f}} = \\dfrac{{{speed}}}{{{freq}}} = {speed/freq:.3f}\\,m$.",
                'hints': [
                    f"Longueur d'onde: $\\lambda = \\dfrac{{v}}{{f}}$ où $v = {speed}\\,m/s$, $f = {freq}\\,Hz$.",
                    f"Invariance: La fréquence $f$ ne dépend que de la source. Toujours $f = {freq}\\,Hz$ partout.",
                    f"Milieu dense: $v \\downarrow \\Rightarrow \\lambda = v/f \\downarrow$ (car $f$ constant)."
                ],
                'conseils': "Relations clés: $v = \\lambda f$ et $T = 1/f$. La fréquence est invariante.",
                'difficulte': 'moyen',
                'theme': section_title,
                'source': 'Générateur d\'exercices physique'
            }
        
        elif 'cinématique' in section_low or 'mouvement' in section_low or 'mécanique' in section_low:
            v0 = choose([5, 10, 15, 20])
            a = choose([2, 3, 5, 10])
            t = choose([1, 2, 3, 5])
            return {
                'title': f'Exercice {index + 1} — {section_title}',
                'intro': f"Une particule part de vitesse initiale ${v0}\\,m/s$ avec accélération ${a}\\,m/s^2$ pendant ${t}\\,s$.",
                'enonce': f"Mouvement: $v_0 = {v0}\\,m/s$, $a = {a}\\,m/s^2$, $t = {t}\\,s$.",
                'questions': [
                    "Calcule la vitesse finale.",
                    "Calcule la distance parcourue.",
                    "Quelle est l'accélération moyenne?"
                ],
                'solution': (
                    f"$v = v_0 + at = {v0} + {a}\\times{t} = {v0 + a*t}\\,m/s$. "
                    f"$d = v_0 t + \\dfrac{{1}}{{2}}at^2 = {v0*t} + {0.5*a*t**2:.1f} = {v0*t + 0.5*a*t**2:.1f}\\,m$."
                ),
                'hints': [
                    f"Équations: $v = v_0 + at$ et $d = v_0 t + \\dfrac{{1}}{{2}}at^2$ (mouvement uniformément accéléré).",
                    f"Vitesse finale: $v = {v0} + {a}\\times{t} = {v0 + a*t}\\,m/s$.",
                    "Aussi: $v^2 = v_0^2 + 2ad$ (utile si tu ne connais pas $t$)."
                ],
                'conseils': "Les équations fondamentales sont $v = v_0 + at$ et $d = v_0 t + \\frac{1}{2}at^2$.",
                'difficulte': 'moyen',
                'theme': section_title,
                'source': 'Générateur d\'exercices physique'
            }
        
        # Ultimate fallback if nothing matches
        else:
            return {
                'title': f'Exercice {index + 1} — {section_title}',
                'intro': f'Exercice sur {section_title}.',
                'enonce': f'Exercice sur {section_title}.',
                'questions': [
                    "Identifie les données et la formule appropriée.",
                    "Effectue le calcul étape par étape.",
                    "Interprète le résultat physiquement."
                ],
                'solution': 'Voir le cours pour les formules appropriées à cette section.',
                'hints': [
                    "Commence par identifier clairement quelles données tu as et quelles données tu cherches.",
                    "Écris la formule appropriée de la section.",
                    "Substitue les valeurs et calcule en gardant les unités."
                ],
                'conseils': 'Réfère-toi au cours pour les formules clés de cette section.',
                'difficulte': 'moyen',
                'theme': section_title,
                'source': 'Générateur d\'exercices physique'
            }
