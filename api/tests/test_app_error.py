from app.errors import AppError


def test_appperror_stores_details():
    err = AppError("duplicate_suspected", "Possible duplicate", 409,
                   details={"txn": {"id": 7}, "reason": "fields"})
    assert err.code == "duplicate_suspected"
    assert err.status == 409
    assert err.details == {"txn": {"id": 7}, "reason": "fields"}


def test_appperror_details_defaults_none():
    err = AppError("nope", "no")
    assert err.details is None
    assert err.status == 400
