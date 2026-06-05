from app.services import tax


QC = [{"name": "GST", "rate": 5.0}, {"name": "QST", "rate": 9.975}]
ON = [{"name": "HST", "rate": 13.0}]


def test_quebec_back_calculation():
    result = tax.back_calculate(114.98, QC, taxable=True)
    assert result["amount"] == 100.0
    assert result["breakdown"] == {"GST": 5.0, "QST": 9.98}


def test_ontario_back_calculation():
    result = tax.back_calculate(113.0, ON, taxable=True)
    assert result["amount"] == 100.0
    assert result["breakdown"] == {"HST": 13.0}


def test_non_taxable_passthrough():
    result = tax.back_calculate(1500.0, QC, taxable=False)
    assert result == {"amount": 1500.0, "breakdown": {}}


def test_active_profile_components(conn):
    components = tax.active_components(conn)
    assert components == QC  # Quebec seeded active
