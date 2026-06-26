from app.services.triage import suggest_category_from_vitals


def test_suggest_category_normal():
    vitals = {
        "oxygen_saturation": 98.0,
        "respiratory_rate": 16,
        "pulse": 72,
        "temperature": 36.6,
        "blood_pressure": "120/80"
    }
    category, reason = suggest_category_from_vitals(vitals)
    assert category == "non_urgent"


def test_suggest_category_emergency_spo2():
    vitals = {
        "oxygen_saturation": 88.0,
        "respiratory_rate": 16,
        "pulse": 72,
        "temperature": 36.6,
    }
    category, reason = suggest_category_from_vitals(vitals)
    assert category == "emergency"
    assert "SpO2" in reason


def test_suggest_category_urgent_rr():
    vitals = {
        "oxygen_saturation": 96.0,
        "respiratory_rate": 26,
        "pulse": 72,
        "temperature": 36.6,
    }
    category, reason = suggest_category_from_vitals(vitals)
    assert category == "urgent"
    assert "Respiratory rate" in reason


def test_suggest_category_semi_urgent_temp():
    vitals = {
        "oxygen_saturation": 97.0,
        "respiratory_rate": 18,
        "pulse": 75,
        "temperature": 38.0,
    }
    category, reason = suggest_category_from_vitals(vitals)
    assert category == "semi_urgent"
    assert "Temperature" in reason
