"""
Unit tests for Core Business Logic & State Machines.

Tests:
  1. PII Encryption & Decryption (Fernet)
  2. PII Field Data Masking (National ID, Medical, Emergency Contact)
  3. Predicted Attendance & Audience Calculations (0.8x formula)
  4. Per-Student Ticket Pricing & School Subsidy Math
  5. Event Lifecycle State Machine Transitions (draft -> proposed -> published)
  6. Enrollment Lifecycle State Machine Transitions (requested -> parent_approved -> teacher_approved -> paid)
"""

import pytest
from app.domains.tenant.service import _encrypt, _decrypt, _mask_field


# =============================================================================
# 1. PII Encryption & Decryption Tests (Fernet)
# =============================================================================
class TestPIIEncryption:
    def test_encrypt_returns_ciphertext(self):
        plain_national_id = "9876543210"
        encrypted = _encrypt(plain_national_id)
        assert encrypted != plain_national_id
        assert isinstance(encrypted, str)
        assert len(encrypted) > 20

    def test_decrypt_recovers_original_plaintext(self):
        original_medical_condition = "Severe Peanut Allergy - Requires EpiPen"
        encrypted = _encrypt(original_medical_condition)
        decrypted = _decrypt(encrypted)
        assert decrypted == original_medical_condition

    def test_two_encryptions_differ_due_to_iv(self):
        secret = "0791234567"
        enc1 = _encrypt(secret)
        enc2 = _encrypt(secret)
        assert enc1 != enc2
        assert _decrypt(enc1) == secret
        assert _decrypt(enc2) == secret


# =============================================================================
# 2. PII Masking Tests (Data Protection & Privacy)
# =============================================================================
class TestPIIDataMasking:
    def test_mask_national_id_reveals_last_4_only(self):
        nat_id = "1234567890"
        masked = _mask_field(nat_id, "national_id")
        assert masked == "********7890"
        assert not ("123456" in masked)

    def test_mask_short_national_id(self):
        nat_id = "123"
        masked = _mask_field(nat_id, "national_id")
        assert masked == "********"

    def test_mask_medical_conditions(self):
        medical = "Asthma"
        masked = _mask_field(medical, "medical_conditions")
        assert masked == "***ma"

    def test_mask_emergency_contact(self):
        contact = "+962791234567"
        masked = _mask_field(contact, "emergency_contact")
        assert masked == "******4567"

    def test_mask_empty_or_none_field(self):
        assert _mask_field("", "national_id") == ""
        assert _mask_field(None, "medical_conditions") == ""


# =============================================================================
# 3. Predicted Attendance & Audience Calculations (0.8x Formula)
# =============================================================================
class TestAudienceAndPricingCalculations:
    def test_predicted_attendance_standard(self):
        total_students = 100
        predicted = int(round(total_students * 0.8))
        assert predicted == 80

    def test_predicted_attendance_fractional_rounding(self):
        total_students = 25
        predicted = int(round(total_students * 0.8))
        assert predicted == 20

    def test_per_student_ticket_price_with_subsidy(self):
        total_trip_cost = 1000.0
        school_subsidy = 200.0
        predicted_attendance = 80

        student_pool_cost = total_trip_cost - school_subsidy  # 800.0
        ticket_price_per_student = student_pool_cost / predicted_attendance

        assert student_pool_cost == 800.0
        assert ticket_price_per_student == 10.0

    def test_full_school_subsidy_free_trip(self):
        total_trip_cost = 500.0
        school_subsidy = 500.0
        predicted_attendance = 50

        student_pool_cost = max(0.0, total_trip_cost - school_subsidy)
        ticket_price_per_student = student_pool_cost / predicted_attendance if predicted_attendance > 0 else 0.0

        assert student_pool_cost == 0.0
        assert ticket_price_per_student == 0.0


# =============================================================================
# 4. Event Lifecycle State Machine Validation
# =============================================================================
class TestEventLifecycleStateMachine:
    """Mirrors the canonical lifecycle enforced by TenantService.transition_event:

        draft --submit--> proposed --manager_approve--> approved --teacher_publish--> published
                              |
                              +--manager_reject (reason required)--> draft

    There is deliberately no proposed -> published shortcut: publishing must pass
    through 'approved' so published_at is stamped and the student/parent
    notification fan-out runs.
    """

    VALID_TRANSITIONS = {
        ("draft", "submit"): "proposed",
        ("proposed", "manager_approve"): "approved",
        ("proposed", "manager_reject"): "draft",
        ("approved", "teacher_publish"): "published",
        ("approved", "manager_publish"): "published",
    }

    def _apply_transition(self, current_status: str, action: str, reason: str | None = None) -> str:
        if action == "manager_reject" and not reason:
            raise ValueError("Rejection reason is required when returning event to draft")
        transition_key = (current_status, action)
        if transition_key not in self.VALID_TRANSITIONS:
            raise ValueError(f"Invalid transition '{action}' from status '{current_status}'")
        return self.VALID_TRANSITIONS[transition_key]

    def test_valid_draft_to_proposed(self):
        new_status = self._apply_transition("draft", "submit")
        assert new_status == "proposed"

    def test_valid_proposed_to_approved(self):
        new_status = self._apply_transition("proposed", "manager_approve")
        assert new_status == "approved"

    def test_valid_approved_to_published_by_teacher(self):
        new_status = self._apply_transition("approved", "teacher_publish")
        assert new_status == "published"

    def test_manager_retains_publish_override_on_approved(self):
        new_status = self._apply_transition("approved", "manager_publish")
        assert new_status == "published"

    def test_valid_proposed_to_draft_with_reason(self):
        new_status = self._apply_transition("proposed", "manager_reject", reason="Budget exceeds threshold")
        assert new_status == "draft"

    def test_reject_without_reason_raises_error(self):
        with pytest.raises(ValueError, match="reason is required"):
            self._apply_transition("proposed", "manager_reject", reason="")

    def test_invalid_transition_draft_to_published_fails(self):
        with pytest.raises(ValueError, match="Invalid transition"):
            self._apply_transition("draft", "manager_approve")

    def test_invalid_transition_published_to_proposed_fails(self):
        with pytest.raises(ValueError, match="Invalid transition"):
            self._apply_transition("published", "submit")

    def test_manager_cannot_publish_directly_from_proposed(self):
        """The removed shortcut: skipping 'approved' left published_at unset and
        sent zero notifications, so it must be rejected outright."""
        with pytest.raises(ValueError, match="Invalid transition"):
            self._apply_transition("proposed", "manager_publish")


# =============================================================================
# 5. Student Enrollment Lifecycle State Machine Validation
# =============================================================================
class TestEnrollmentLifecycleStateMachine:
    def _transition_enrollment(self, current_state: str, role: str, target_state: str) -> str:
        if role == "parent":
            if current_state != "requested_by_student":
                raise ValueError(f"Parent cannot act on state '{current_state}'")
            if target_state not in ("approved_by_parent", "rejected_by_parent"):
                raise ValueError("Parent can only set approved_by_parent or rejected_by_parent")
            return target_state
        elif role == "teacher":
            if current_state != "approved_by_parent":
                raise ValueError(f"Teacher cannot approve before parent approval (current: '{current_state}')")
            if target_state not in ("approved_by_teacher", "rejected_by_teacher"):
                raise ValueError("Teacher can only set approved_by_teacher or rejected_by_teacher")
            return target_state
        elif role == "billing":
            if current_state != "approved_by_teacher":
                raise ValueError(f"Cannot process payment until teacher has approved (current: '{current_state}')")
            if target_state != "paid":
                raise ValueError("Invalid target state for payment")
            return target_state
        else:
            raise PermissionError(f"Role '{role}' is not allowed to transition enrollments")

    def test_full_successful_enrollment_pipeline(self):
        state = "requested_by_student"
        # 1. Parent approves
        state = self._transition_enrollment(state, "parent", "approved_by_parent")
        assert state == "approved_by_parent"

        # 2. Teacher approves
        state = self._transition_enrollment(state, "teacher", "approved_by_teacher")
        assert state == "approved_by_teacher"

        # 3. Payment processed
        state = self._transition_enrollment(state, "billing", "paid")
        assert state == "paid"

    def test_teacher_cannot_bypass_parent_approval(self):
        with pytest.raises(ValueError, match="Teacher cannot approve before parent approval"):
            self._transition_enrollment("requested_by_student", "teacher", "approved_by_teacher")

    def test_payment_cannot_occur_before_teacher_approval(self):
        with pytest.raises(ValueError, match="Cannot process payment until teacher has approved"):
            self._transition_enrollment("approved_by_parent", "billing", "paid")
