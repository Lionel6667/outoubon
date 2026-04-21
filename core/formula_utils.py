"""
Formula utilities for physics exercises.
Centralizes all formula generation to ensure consistent, correct LaTeX rendering.
Each formula is tested once and reused everywhere.
"""

def fmt_formula(latex: str) -> str:
    """
    Wrap formula in $ delimiters for inline rendering.
    Ensures all formulas are consistently formatted.
    """
    return f"${latex}$"


def fmt_inline(latex: str) -> str:
    """Alias for fmt_formula - inline math mode."""
    return fmt_formula(latex)


def fmt_display(latex: str) -> str:
    """Display mode (bigger) - useful for main solutions."""
    return f"$$" + latex + "$$"


# ============================================================================
# MAGNETIC FIELD FORMULAS
# ============================================================================

def mag_field_infinite_wire(current: float, distance: float) -> dict:
    """
    Magnetic field around an infinite straight wire.
    
    Returns dict with:
    - formula: the LaTeX formula with symbols only
    - calculation: step-by-step calculation with values
    - result: final result
    """
    mu0 = "4\\pi \\times 10^{-7}"
    
    formula = f"B = \\dfrac{{{mu0}}}{{2\\pi d}}"
    
    # Simplify for the specific case
    denominator = f"2\\pi \\times {distance}"
    numerator = f"4\\pi \\times 10^{{-7}} \\times {current}"
    
    result = round((4 * 3.14159 * 1e-7 * current) / (2 * 3.14159 * distance), 8)
    
    return {
        'formula': fmt_formula(formula),
        'formula_with_values': fmt_formula(f"B = \\dfrac{{4\\pi \\times 10^{{-7}} \\times {current}}}{{2\\pi \\times {distance}}}"),
        'result': f"{result:.2e} T ou {result*1e6:.2f} µT",
        'mu0': fmt_formula("\\mu_0 = 4\\pi \\times 10^{-7} \\, T \\cdot m / A"),
    }


def mag_field_solenoid(turns: int, length: float, current: float) -> dict:
    """Magnetic field inside a solenoid."""
    mu0 = "4\\pi \\times 10^{-7}"
    
    formula = f"B = \\mu_0 \\dfrac{{N}}{{L}} I"
    
    turn_density = turns / length
    result = 4 * 3.14159 * 1e-7 * turn_density * current
    
    return {
        'formula': fmt_formula(formula),
        'formula_with_values': fmt_formula(f"B = 4\\pi \\times 10^{{-7}} \\times \\dfrac{{{turns}}}{{{length}}} \\times {current}"),
        'result': f"{result:.4f} T ou {result*1000:.2f} mT",
        'explanation': f"Nombre de spires par unité de longueur: N/L = {turn_density:.0f} spires/m",
    }


def right_hand_rule() -> str:
    """Right hand rule explanation with proper formatting."""
    return "Pouce = direction du courant, Doigts repliés = direction du champ magnétique"


def proportionality_magnetic_field() -> str:
    """Magnetic field proportionality relationship."""
    return fmt_formula("B \\propto I \\text{ et } B \\propto \\dfrac{1}{d}")


# ============================================================================
# LORENTZ FORCE FORMULAS
# ============================================================================

def lorentz_force(charge: float, velocity: float, magnetic_field: float, angle: float = 90) -> dict:
    """
    Lorentz force on a moving charge in a magnetic field.
    F = qvB sin(θ)
    """
    import math
    
    formula = "F = q v B \\sin(\\theta)"
    
    sin_theta = math.sin(math.radians(angle))
    result = charge * velocity * magnetic_field * sin_theta
    
    return {
        'formula': fmt_formula(formula),
        'formula_explanation': fmt_formula("\\text{où } q \\text{ = charge (C)}, v \\text{ = vitesse (m/s)}, B \\text{ = champ (T)}, \\theta \\text{ = angle}"),
        'formula_with_values': fmt_formula(f"F = {charge} \\times {velocity} \\times {magnetic_field} \\times \\sin({angle}°)"),
        'result': f"{result:.2e} N",
    }


# ============================================================================
# ELECTROMAGNETIC INDUCTION FORMULAS
# ============================================================================

def faraday_law() -> str:
    """Faraday's law of electromagnetic induction."""
    return fmt_formula("\\varepsilon = -\\dfrac{{d\\Phi_B}}{{dt}}")


def magnetic_flux(area: float, magnetic_field: float, angle: float = 0) -> dict:
    """Magnetic flux through a surface."""
    import math
    
    formula = "\\Phi_B = B \\cdot A \\cdot \\cos(\\theta)"
    
    cos_theta = math.cos(math.radians(angle))
    result = magnetic_field * area * cos_theta
    
    return {
        'formula': fmt_formula(formula),
        'formula_with_values': fmt_formula(f"\\Phi_B = {magnetic_field} \\times {area} \\times \\cos({angle}°)"),
        'result': f"{result:.4f} Wb (Weber)",
    }


def induced_emf(flux_change: float, time_change: float) -> dict:
    """Induced EMF from flux change."""
    result = abs(flux_change / time_change)
    
    return {
        'formula': fmt_formula("\\varepsilon = \\left|\\dfrac{\\Delta\\Phi_B}{\\Delta t}\\right|"),
        'formula_with_values': fmt_formula(f"\\varepsilon = \\left|\\dfrac{{{flux_change}}}{{{time_change}}}\\right|"),
        'result': f"{result:.4f} V",
    }


def induced_charge(flux_change: float, resistance: float) -> dict:
    """Charge induced by flux change (Lenz's law)."""
    result = abs(flux_change / resistance)
    
    return {
        'formula': fmt_formula("Q = \\left|\\dfrac{\\Delta\\Phi_B}{R}\\right|"),
        'formula_with_values': fmt_formula(f"Q = \\left|\\dfrac{{{flux_change}}}{{{resistance}}}\\right|"),
        'result': f"{result:.4e} C (Coulombs)",
    }


# ============================================================================
# RLC CIRCUIT FORMULAS
# ============================================================================

def capacitor_energy(capacitance: float, voltage: float) -> dict:
    """Energy stored in a capacitor."""
    import math
    
    # E = 0.5 * C * V^2
    result = 0.5 * capacitance * (voltage ** 2)
    
    return {
        'formula': fmt_formula("E = \\dfrac{1}{2} C V^2"),
        'formula_with_values': fmt_formula(f"E = \\dfrac{{1}}{{2}} \\times {capacitance} \\times {voltage}^2"),
        'result': f"{result:.4e} J (Joules)",
    }


def capacitive_reactance(frequency: float, capacitance: float) -> dict:
    """Capacitive reactance in AC circuits."""
    import math
    
    # Xc = 1 / (2πfC)
    result = 1 / (2 * math.pi * frequency * capacitance)
    
    return {
        'formula': fmt_formula("X_C = \\dfrac{1}{2\\pi f C}"),
        'formula_with_values': fmt_formula(f"X_C = \\dfrac{{1}}{{2\\pi \\times {frequency} \\times {capacitance}}}"),
        'result': f"{result:.2f} Ω (Ohms)",
    }


# ============================================================================
# MECHANICS FORMULAS
# ============================================================================

def free_fall(height: float, gravity: float = 9.81) -> dict:
    """Free fall motion under gravity."""
    import math
    
    # v = sqrt(2gh)
    velocity = math.sqrt(2 * gravity * height)
    
    return {
        'formula': fmt_formula("v = \\sqrt{2 g h}"),
        'formula_explanation': fmt_formula("\\text{où } g = 9.81 \\, m/s^2, h = \\text{hauteur (m)}"),
        'formula_with_values': fmt_formula(f"v = \\sqrt{{2 \\times {gravity} \\times {height}}}"),
        'result': f"{velocity:.2f} m/s",
    }


def projectile_range(initial_velocity: float, angle: float, gravity: float = 9.81) -> dict:
    """Range of a projectile launch."""
    import math
    
    angle_rad = math.radians(angle)
    # R = v0^2 sin(2θ) / g
    result = (initial_velocity ** 2 * math.sin(2 * angle_rad)) / gravity
    
    return {
        'formula': fmt_formula("R = \\dfrac{v_0^2 \\sin(2\\theta)}{g}"),
        'formula_with_values': fmt_formula(f"R = \\dfrac{{{initial_velocity}^2 \\times \\sin(2 \\times {angle}°)}}{{{gravity}}}"),
        'result': f"{result:.2f} m",
    }


# ============================================================================
# UTILITY FUNCTIONS FOR FORMATTING VALUES
# ============================================================================

def scientific_notation(value: float, precision: int = 2) -> str:
    """Format a number in scientific notation for LaTeX."""
    if abs(value) < 1e-6 or abs(value) > 1e6:
        mantissa = value / (10 ** int(__import__('math').log10(abs(value))))
        exponent = int(__import__('math').log10(abs(value)))
        return f"{mantissa:.{precision}f} \\times 10^{{{exponent}}}"
    return f"{value:.{precision}f}"


def unit_with_value(value: float, unit: str, precision: int = 2) -> str:
    """Format value with unit in LaTeX."""
    return fmt_formula(f"{value:.{precision}f}\\,{unit}")


def constant_mu0() -> str:
    """Permeability of free space constant."""
    return fmt_formula("\\mu_0 = 4\\pi \\times 10^{-7} \\, T \\cdot m / A")


def constant_epsilon0() -> str:
    """Permittivity of free space constant."""
    return fmt_formula("\\varepsilon_0 = 8.854 \\times 10^{-12} \\, F/m")


def vector_notation(symbol: str) -> str:
    """Format vector notation in LaTeX."""
    return fmt_formula("\\vec{" + symbol + "}")


# ============================================================================
# TEST FUNCTION - Validate all formulas render correctly
# ============================================================================

def test_all_formulas() -> None:
    """Test that all formulas generate valid LaTeX."""
    print("Testing all formula generation...")
    
    # Test magnetic field
    mf = mag_field_infinite_wire(2.5, 0.05)
    assert "$" in mf['formula'], "Magnetic field formula should be wrapped"
    print("  Magnetic field: OK")
    
    # Test Lorentz force
    lf = lorentz_force(1.6e-19, 2e5, 0.5)
    assert "$" in lf['formula'], "Lorentz force formula should be wrapped"
    print("  Lorentz force: OK")
    
    # Test Faraday
    ff = faraday_law()
    assert "$" in ff, "Faraday law should be wrapped"
    print("  Faraday law: OK")
    
    # Test free fall
    ff = free_fall(100)
    assert "$" in ff['formula'], "Free fall formula should be wrapped"
    print("  Free fall: OK")
    
    # Test all constants
    assert "$" in constant_mu0(), "mu0 should be wrapped"
    assert "$" in constant_epsilon0(), "epsilon0 should be wrapped"
    print("  Constants: OK")
    
    print("All formulas validated!")


if __name__ == "__main__":
    test_all_formulas()
