def safe_float(val):
    try:
        if val is None or val == '':
            return 0.0
        return float(val)
    except (TypeError, ValueError):
        return 0.0

def calculate_old_regime(data):
    income = safe_float(data.get('gross_salary', 0))
    deductions = sum([
        safe_float(data.get('deduction_80c', 0)),
        safe_float(data.get('deduction_80d', 0)),
        safe_float(data.get('standard_deduction', 50000)),
        safe_float(data.get('professional_tax', 0)),
        safe_float(data.get('hra_received', 0)),
    ])
    taxable = max(0, income - deductions)
    tax = 0
    if taxable > 250000:
        if taxable <= 500000:
            tax = (taxable - 250000) * 0.05
        elif taxable <= 1000000:
            tax = 12500 + (taxable - 500000) * 0.2
        else:
            tax = 12500 + 100000 + (taxable - 1000000) * 0.3
    cess = tax * 0.04
    return round(tax + cess, 2)

def calculate_new_regime(data):
    income = safe_float(data.get('gross_salary', 0))
    deductions = safe_float(data.get('standard_deduction', 50000))
    taxable = max(0, income - deductions)
    tax = 0
    slabs = [
        (300000, 0.05),
        (600000, 0.1),
        (900000, 0.15),
        (1200000, 0.2),
        (1500000, 0.3)
    ]
    prev = 0
    for limit, rate in slabs:
        if taxable > limit:
            tax += (limit - prev) * rate
            prev = limit
        else:
            tax += (taxable - prev) * rate
            break
    if taxable > 1500000:
        tax += (taxable - 1500000) * 0.3
    cess = tax * 0.04
    return round(tax + cess, 2)

def compare_regimes(data, selected_regime):
    old_tax = calculate_old_regime(data)
    new_tax = calculate_new_regime(data)
    best = 'old' if old_tax < new_tax else 'new'
    return {
        'tax_old_regime': old_tax,
        'tax_new_regime': new_tax,
        'best_regime': best,
        'selected_regime': selected_regime
    } 