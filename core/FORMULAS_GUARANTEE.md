# Formulas Guarantee - Absolue Certitude des Formules

## RÈGLE D'OR ⭐

**JAMAIS écrire une formule LaTeX directement dans le code.**

**TOUJOURS** créer/utiliser une fonction dans `formula_utils.py`.

## Pourquoi ?

- ❌ **Avant**: Formules écrites partout dans le code → Erreurs d'échappement, oublis, incohérence
- ✅ **Maintenant**: Formules définies UNE FOIS dans `formula_utils.py` → Testées UNE FOIS, utilisées partout

## Structure du Système

```
formula_utils.py (Source de vérité)
    ↓
    ├─ mag_field_infinite_wire(I, d) → dict avec 'formula', 'result', etc.
    ├─ mag_field_solenoid(N, L, I) → dict
    ├─ faraday_law() → LaTeX string
    ├─ induced_charge(dPhi, R) → dict
    ├─ free_fall(h) → dict
    └─ ... (tous les problèmes physiques)
    ↓
exercise_generator.py (Utilise les formules)
    ↓
views.py (Affiche les exercices)
```

## Ajouter une Nouvelle Formule

**ÉTAPE 1**: Définir dans `formula_utils.py`

```python
def resistance_power(voltage: float, current: float) -> dict:
    """Power dissipated in a resistor: P = U*I"""
    result = voltage * current
    
    return {
        'formula': fmt_formula("P = U \\times I"),
        'formula_with_values': fmt_formula(f"P = {voltage} \\times {current}"),
        'result': f"{result} W",
        'explanation': "Puissance dissipée en watts"
    }
```

**ÉTAPE 2**: Importer dans `exercise_generator.py`

```python
from .formula_utils import resistance_power
```

**ÉTAPE 3**: Utiliser dans l'exercice

```python
power_data = resistance_power(220, 5)
# Accès: power_data['formula'], power_data['result'], power_data['explanation']
```

## Convention de Nommage

TOUTES les formules doivent:
1. ✅ Être wrappées par `fmt_formula()` → `$...$`
2. ✅ Retourner un dict avec `'formula'`, `'result'`, `'explanation'` quand applicable
3. ✅ Utiliser `\\dfrac{}{}` pour les fractions (pas `\frac`)
4. ✅ Utiliser `\\times` pour la multiplication (pas `*` ou `·`)
5. ✅ Utiliser `\\propto` pour la proportionnalité
6. ✅ Documenter avec docstring

## LaTeX Standards

```python
# ✅ BON
$B = \\dfrac{\\mu_0 I}{2\\pi d}$  # Grand symbole de division
$B \\propto I$                      # Proportionnalité
$I = 3\\times 10^{-5}$             # Multiplication explicite
$X_C = \\omega L$                   # Indice avec _

# ❌ MAUVAIS
$B = \frac{\mu_0 I}{2\pi d}$       # Trop petit, pas assez clair
$v = 2*3$                           # * au lieu de \times
$B α I$                             # Caracte unicode, pas LaTeX
3e-5                                # Pas de formatage LaTeX
```

## Liste Actuelle des Formules Validées

### Magnétisme

| Fonction | Retourne | Paramètres |
|----------|----------|-----------|
| `mag_field_infinite_wire(I, d)` | dict (formula, result) | current (A), distance (m) |
| `mag_field_solenoid(N, L, I)` | dict | turns, length (m), current (A) |
| `right_hand_rule()` | string | - |
| `proportionality_magnetic_field()` | string | - |

### Induction

| Fonction | Retourne | Paramètres |
|----------|----------|-----------|
| `faraday_law()` | LaTeX string | - |
| `magnetic_flux(B, A, angle)` | dict | field (T), area (m²), angle (°) |
| `induced_emf(flux_change, time)` | dict | ΔΦ (Wb), Δt (s) |
| `induced_charge(flux_change, R)` | dict | ΔΦ (Wb), resistance (Ω) |

### Mécanique

| Fonction | Retourne | Paramètres |
|----------|----------|-----------|
| `free_fall(h, g)` | dict | height (m), gravity (m/s²) |
| `projectile_range(v0, angle)` | dict | velocity (m/s), angle (°) |

### Électricité

| Fonction | Retourne | Paramètres |
|----------|----------|-----------|
| `capacitor_energy(C, V)` | dict | capacitance (F), voltage (V) |
| `capacitive_reactance(f, C)` | dict | frequency (Hz), capacitance (F) |

### Constantes

```python
constant_mu0()       # μ₀ = 4π × 10⁻⁷
constant_epsilon0()  # ε₀ = 8.854 × 10⁻¹²
```

## Test et Validation

Chaque formule doit être testée AVANT d'être utilisée en production.

```bash
cd c:\Users\LE SANG DE JESUS\OneDrive\Desktop\project coding\BacIA_Django
python -c "from core.formula_utils import test_all_formulas; test_all_formulas()"
```

Output attendu:
```
Testing all formula generation...
  Magnetic field: OK
  Lorentz force: OK
  Faraday law: OK
  Free fall: OK
  Constants: OK
All formulas validated!
```

## Futur: Ajouter Plus de Formules

Pour les sections manquantes (SVT, Chimie, Français, etc.), créer des sections dans `formula_utils.py`:

```python
# ============================================================================
# CHEMISTRY FORMULAS
# ============================================================================

def molarity_concentration(moles: float, volume_L: float) -> dict:
    """Concentration = n/V"""
    result = moles / volume_L
    return {
        'formula': fmt_formula("C = \\dfrac{n}{V}"),
        'formula_with_values': fmt_formula(f"C = \\dfrac{{{moles}}}{{{volume_L}}}"),
        'result': f"{result:.3f} mol/L"
    }
```

Chaque matière peut avoir son propre module si nécessaire.

## Garanties de Qualité

✅ **Après cette refactorisation**:
- Formules écrites UNE FOIS seulement
- Testées et validées
- Échappement LaTeX centralisé
- Pas de doublons ni d'incohérences
- Facile à maintenir à long terme

❌ **Avant**:
- Formules écrites partout → erreurs infinies
- Pas standardisé
- Difficile à déboguer
- Oublis constants d'échappement

---

**Maintenu par**: System physique robuste
**Dernière mise à jour**: 2026-03-28
**Status**: Production-ready ✅
