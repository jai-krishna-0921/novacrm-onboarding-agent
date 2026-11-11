"""Defects found in review of the migration checker (plan §12). Regression tests (fixed)."""

from app.services.migration_check import evaluate


def row(i, name="A", email="a@x"):
    return {"id": i, "name": name, "email": email}


def test_empty_source_and_destination_must_not_pass():
    assert not all(r.passed for r in evaluate([], []))


def test_null_destination_id_with_blank_fields_is_reported_not_crashed():
    # Destination has no PK (by design), so a NULL-id row with blank fields is a realistic migration defect.
    results = {r.name: r for r in evaluate([row(1), row(2)], [row(1, email=""), row(None, name="")])}
    assert not results["required_fields"].passed and results["required_fields"].details["actual"] == 2
