"""
Pytest test suite wrapping the contract, security boundary, iteration cap, and E2E MVP verifications.
Run via: pytest tests/test_mvp.py -v
"""

import pytest
from verify_mvp import (
    VerificationRecorder,
    verify_contracts_and_security,
    verify_iteration_limit,
    verify_mvp_end_to_end,
)


def test_full_platform_verification():
    rec = VerificationRecorder()
    ws_id = verify_contracts_and_security(rec)
    assert ws_id, "Workspace creation failed during contract verification"

    verify_iteration_limit(rec, ws_id)
    verify_mvp_end_to_end(rec)

    failed = [r for r in rec.results if not r["passed"]]
    assert not failed, f"Failed verification checks: {failed}"
