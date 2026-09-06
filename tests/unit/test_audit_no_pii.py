"""No-PII contract for the audit log's `metadata` column.

invariant: `AuditService.record`'s metadata argument must never persist a
key outside the per-action allowlist -- a JSONB column cannot enforce this
by itself, so the sanitization has to happen at the service layer, and this
test is what actually proves it does, not just documents the intent.
"""

from app.domains.audit.service import _sanitize_metadata


def test_disallowed_key_is_stripped_not_stored():
    result = _sanitize_metadata(
        "class.delete",
        {"had_enrollment_history": True, "student_email": "fake@example.com"},
    )
    assert result == {"had_enrollment_history": True}
    assert "student_email" not in result


def test_action_with_empty_allowlist_strips_everything():
    result = _sanitize_metadata(
        "student.health.read",
        {"national_id": "123456789", "note": "anything at all"},
    )
    assert result == {}


def test_unknown_action_strips_everything():
    result = _sanitize_metadata("not.a.real.action", {"anything": "goes here"})
    assert result == {}


def test_none_metadata_returns_empty_dict():
    assert _sanitize_metadata("class.delete", None) == {}
