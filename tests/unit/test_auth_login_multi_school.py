"""Login for a person who belongs to more than one school.

`user_tenant_map` has been keyed `(email, tenant_id)` since alembic cp_0002, so
one email legitimately holds several memberships -- a parent with children at
two schools, a manager working at two, a teacher who parents a pupil elsewhere.
These tests pin the two properties `AuthService.login_user` must have for that
to be safe:

1. A school named in the REQUEST cannot become a school the caller belongs to.
   `UserLoginRequest.tenant_id` is arbitrary caller input, and the parents
   branch verifies the password against the GLOBAL `public.parents` hash rather
   than a per-tenant one -- so membership has to be checked, never created.

2. Which of several schools a login lands in is the user's choice, not an
   ORDER BY.

No database and no FastAPI: `ControlPlaneRepository` and the tenant-pool getters
are patched at the `app.domains.auth.service` module boundary, which is where
`login_user` resolves them.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.domains.auth.service import AuthService
from app.domains.tenant.control_plane_repository import ControlPlaneRepository

PASSWORD = "correct-horse-battery-staple"


def _fake_cp(
    *,
    memberships: list[dict] | None = None,
    parent: dict | None = None,
    parent_tenants: list[str] | None = None,
    linked_tenants: set[str] | None = None,
    all_tenants: list[dict] | None = None,
) -> MagicMock:
    """A ControlPlaneRepository double covering only what login_user calls."""
    linked = linked_tenants or set()
    memberships = memberships or []

    # spec= pins the double to the real repository's surface, so a typo in a
    # method name here fails loudly instead of silently auto-creating an
    # attribute. It also means `create_parent_tenant_link` does NOT exist on
    # this double -- matching reality, and the reason the escalation only ever
    # showed up as a 500.
    cp = MagicMock(spec=ControlPlaneRepository)
    cp.get_super_admin_by_email = AsyncMock(return_value=None)
    cp.get_tenants_for_email = AsyncMock(return_value=memberships)
    cp.get_parent_by_email = AsyncMock(return_value=parent)
    cp.get_tenants_for_parent = AsyncMock(return_value=parent_tenants or [])
    cp.get_all_tenants = AsyncMock(return_value=all_tenants or [])
    cp.check_parent_tenant_link = AsyncMock(side_effect=lambda _pid, tid: tid in linked)
    cp.upsert_user_tenant_map = AsyncMock(return_value=None)
    cp.add_parent_tenant_link = AsyncMock(return_value=None)
    # Multi-School Membership (Phase 1): every login now resolves/creates an
    # identity and issues a refresh token alongside the access token.
    cp.get_or_create_identity = AsyncMock(
        return_value={"id": "identity-0000-0000-0000-000000000000", "email": "x@example.com"}
    )
    cp.create_refresh_token = AsyncMock(return_value=None)
    cp.create_selection_challenge = AsyncMock(return_value=None)
    # The lossy single-row view login_user used to resolve through. Stubbed so
    # these tests exercise the OLD code path meaningfully too -- without it a
    # pre-fix run dies on an un-awaitable MagicMock, which would look like a
    # red test for the wrong reason.
    cp.get_tenant_for_email = AsyncMock(return_value=memberships[0] if memberships else None)
    return cp


def _patches(cp: MagicMock, *, get_db_pool: AsyncMock | None = None):
    pool = MagicMock()
    return (
        patch("app.domains.auth.service.ControlPlaneRepository", return_value=cp),
        patch(
            "app.domains.auth.service.get_control_plane_pool",
            AsyncMock(return_value=pool),
        ),
        patch(
            "app.domains.auth.service.get_db_pool",
            get_db_pool or AsyncMock(return_value=pool),
        ),
    )


class TestParentCannotSelfEnrol:
    async def test_parent_cannot_self_link_to_unmembered_tenant(self):
        """A tenant_id in the request body must not become a membership.

        This is the regression test for the escalation that the missing
        `create_parent_tenant_link` method was accidentally hiding. Any
        control-plane parent who knew their own password could POST
        {email, password, tenant_id: "<any school>"} and, once that method name
        was corrected, be auto-linked into it -- given a tenant-local users +
        parenets row and a valid `parent` token for a school they have no
        relationship with. `get_pool()` also auto-registers unknown tenant ids,
        so the reachable set was not even limited to schools that exist.
        """
        parent = {
            "id": "11111111-1111-1111-1111-111111111111",
            "password_hash": AuthService.hash_password(PASSWORD),
            "phone": None,
        }
        # Genuinely a parent -- at school A only.
        cp = _fake_cp(
            memberships=[{"tenant_id": "tenant_a", "role": "parent"}],
            parent=parent,
            parent_tenants=["tenant_a"],
            linked_tenants={"tenant_a"},
        )
        spy_get_db_pool = AsyncMock(return_value=MagicMock())
        p1, p2, p3 = _patches(cp, get_db_pool=spy_get_db_pool)

        with p1, p2, p3, pytest.raises(ValueError) as exc_info:
            await AuthService.login_user(
                email="parent-of-one@example.com",
                password=PASSWORD,
                tenant_id="tenant_c",  # a school they have nothing to do with
            )

        # Generic credential error: whether tenant_c exists, and whether this
        # person belongs to it, must not be probeable by an unauthenticated
        # caller.
        assert "Invalid email or password" in str(exc_info.value)

        # The membership must not have been created...
        cp.add_parent_tenant_link.assert_not_awaited()
        # ...and nothing may have been written into tenant_c: no pool for it was
        # ever opened, so no users/parenets row could have been provisioned.
        for call in spy_get_db_pool.await_args_list:
            assert call.args[0] != "tenant_c"

    async def test_parent_linked_but_school_not_requested_still_denied(self):
        """Password alone is not authority for an unlinked school.

        Reaching this branch means the caller authenticated against the global
        `public.parents` hash. Without the link check that is authentication
        without authorization.
        """
        parent = {
            "id": "22222222-2222-2222-2222-222222222222",
            "password_hash": AuthService.hash_password(PASSWORD),
            "phone": None,
        }
        # A membership row exists for tenant_b, but no parent_tenant_links row.
        cp = _fake_cp(
            memberships=[{"tenant_id": "tenant_b", "role": "parent"}],
            parent=parent,
            parent_tenants=[],
            linked_tenants=set(),
        )
        p1, p2, p3 = _patches(cp)

        with p1, p2, p3, pytest.raises(ValueError) as exc_info:
            await AuthService.login_user(
                email="unlinked@example.com", password=PASSWORD, tenant_id="tenant_b"
            )

        assert "Invalid email or password" in str(exc_info.value)
        cp.add_parent_tenant_link.assert_not_awaited()

    async def test_linked_parent_can_log_in_to_their_own_school(self):
        """The happy path must keep working -- this is not a lockout."""
        parent = {
            "id": "33333333-3333-3333-3333-333333333333",
            "password_hash": AuthService.hash_password(PASSWORD),
            "phone": None,
        }
        cp = _fake_cp(
            memberships=[{"tenant_id": "tenant_a", "role": "parent"}],
            parent=parent,
            parent_tenants=["tenant_a"],
            linked_tenants={"tenant_a"},
        )
        p1, p2, p3 = _patches(cp)

        user_repo = MagicMock()
        user_repo.get_user_by_email = AsyncMock(return_value={"id": 42})

        with (
            p1,
            p2,
            p3,
            patch("app.domains.auth.service.UserRepository", return_value=user_repo),
            patch("app.domains.auth.service.TenantRepository", return_value=MagicMock()),
        ):
            result = await AuthService.login_user(
                email="parent-of-one@example.com",
                password=PASSWORD,
                tenant_id="tenant_a",
            )

        payload = AuthService.decode_access_token(result["access_token"])
        assert payload["tenant_id"] == "tenant_a"
        assert payload["role"] == "parent"
        assert result["refresh_token"]

    async def test_wrong_password_is_rejected_before_any_link_check(self):
        parent = {
            "id": "44444444-4444-4444-4444-444444444444",
            "password_hash": AuthService.hash_password(PASSWORD),
            "phone": None,
        }
        cp = _fake_cp(
            memberships=[{"tenant_id": "tenant_a", "role": "parent"}],
            parent=parent,
            parent_tenants=["tenant_a"],
            linked_tenants={"tenant_a"},
        )
        p1, p2, p3 = _patches(cp)

        with p1, p2, p3, pytest.raises(ValueError) as exc_info:
            await AuthService.login_user(
                email="parent-of-one@example.com",
                password="not-the-password",
                tenant_id="tenant_a",
            )

        assert "Invalid email or password" in str(exc_info.value)


class TestMembershipResolution:
    async def test_supplied_tenant_outside_memberships_is_rejected(self):
        """Also true for staff, not just parents."""
        cp = _fake_cp(memberships=[{"tenant_id": "tenant_a", "role": "teacher"}])
        p1, p2, p3 = _patches(cp)

        with p1, p2, p3, pytest.raises(ValueError) as exc_info:
            await AuthService.login_user(
                email="teacher@example.com", password=PASSWORD, tenant_id="tenant_b"
            )

        assert "Invalid email or password" in str(exc_info.value)

    async def test_single_membership_resolves_without_a_selection(self):
        cp = _fake_cp(memberships=[{"tenant_id": "tenant_b", "role": "teacher"}])
        p1, p2, p3 = _patches(cp)

        user_repo = MagicMock()
        user_repo.get_user_by_email = AsyncMock(
            return_value={
                "id": 7,
                "role": "teacher",
                "password_hash": AuthService.hash_password(PASSWORD),
                "roles": [],
                "permissions": [],
            }
        )

        with p1, p2, p3, patch("app.domains.auth.service.UserRepository", return_value=user_repo):
            result = await AuthService.login_user(email="teacher@example.com", password=PASSWORD)

        payload = AuthService.decode_access_token(result["access_token"])
        assert payload["tenant_id"] == "tenant_b"
        assert payload["role"] == "teacher"

    async def test_several_memberships_without_a_selection_refuse_to_guess(self):
        """get_tenant_for_email() would have silently picked the newest row.

        That meant a two-school user was signed into whichever school they last
        touched, with no indication another existed. Ordering is not consent.

        Post-Phase-1, "refuse to guess" is a selection challenge naming both
        matched schools, not a hard error -- but it must still never silently
        pick one, and it must still cost a real password check against BOTH
        tenant_a's and tenant_b's own stores (both membership rows here have a
        role other than 'parent', so both are real distinct-store candidates).
        """
        user_repo = MagicMock()
        user_repo.get_user_by_email = AsyncMock(
            return_value={
                "id": 7,
                "role": "teacher",
                "password_hash": AuthService.hash_password(PASSWORD),
                "roles": [],
                "permissions": [],
            }
        )
        cp = _fake_cp(
            memberships=[
                {"tenant_id": "tenant_b", "role": "manager"},
                {"tenant_id": "tenant_a", "role": "teacher"},
            ]
        )
        p1, p2, p3 = _patches(cp)

        with p1, p2, p3, patch("app.domains.auth.service.UserRepository", return_value=user_repo):
            result = await AuthService.login_user(
                email="teaches-here-manages-there@example.com", password=PASSWORD
            )

        assert result["status"] == "select_tenant"
        assert result["selection_token"]
        assert {c["tenant_id"] for c in result["choices"]} == {"tenant_a", "tenant_b"}
        cp.create_selection_challenge.assert_awaited_once()

    async def test_parent_links_count_as_memberships_for_ambiguity(self):
        """A parent with no user_tenant_map rows still has parent_tenant_links.

        Those are memberships too, so two of them is ambiguous rather than a
        silent pick -- resolved the same way as the mixed-role case above, via
        a selection challenge naming both.
        """
        parent = {
            "id": "55555555-5555-5555-5555-555555555555",
            "password_hash": AuthService.hash_password(PASSWORD),
            "phone": None,
        }
        cp = _fake_cp(
            memberships=[],
            parent=parent,
            parent_tenants=["tenant_a", "tenant_b"],
            linked_tenants={"tenant_a", "tenant_b"},
        )
        p1, p2, p3 = _patches(cp)

        with p1, p2, p3:
            result = await AuthService.login_user(
                email="parent-of-two@example.com", password=PASSWORD
            )

        assert result["status"] == "select_tenant"
        assert {c["tenant_id"] for c in result["choices"]} == {"tenant_a", "tenant_b"}

    async def test_no_membership_anywhere_fails_closed(self):
        """Never a `tenant_a` default -- an unresolvable tenant is an error."""
        cp = _fake_cp(memberships=[], all_tenants=[])
        p1, p2, p3 = _patches(cp)

        with p1, p2, p3, pytest.raises(ValueError) as exc_info:
            await AuthService.login_user(email="nobody@example.com", password=PASSWORD)

        assert "not associated with a school" in str(exc_info.value)


class TestCrossIdentityCandidateMatching:
    """A `public.parents` row is keyed by email alone, not per tenant. When the
    same email is ALSO a distinct tenant-local user elsewhere (the "teacher at
    A, parent at B" case this whole feature exists for), `_fetch_login_candidate`
    used to short-circuit on `if parent:` for EVERY candidate tenant, including
    ones the parent identity has no link to -- so tenant_a's candidate got
    checked against the global parent's password hash instead of its own local
    teacher hash, and (when the passwords happened to match, as they usually do
    for one human) got mislabeled "parent" in the selection challenge. Gating on
    `check_parent_tenant_link` per tenant, not just the record's existence, is
    the fix under test here.
    """

    async def test_parent_record_does_not_shadow_a_distinct_tenant_user_role(self):
        parent = {
            "id": "66666666-6666-6666-6666-666666666666",
            "password_hash": AuthService.hash_password(PASSWORD),
            "phone": None,
        }
        # This human is a teacher at tenant_a (a plain tenant_user, no
        # relationship to the parent identity) and a parent at tenant_b only.
        cp = _fake_cp(
            memberships=[
                {"tenant_id": "tenant_a", "role": "teacher"},
                {"tenant_id": "tenant_b", "role": "parent"},
            ],
            parent=parent,
            parent_tenants=["tenant_b"],
            linked_tenants={"tenant_b"},
        )
        user_repo = MagicMock()
        user_repo.get_user_by_email = AsyncMock(
            return_value={
                "id": 9,
                "role": "teacher",
                "password_hash": AuthService.hash_password(PASSWORD),
                "roles": [],
                "permissions": [],
            }
        )
        p1, p2, p3 = _patches(cp)

        with p1, p2, p3, patch("app.domains.auth.service.UserRepository", return_value=user_repo):
            result = await AuthService.login_user(
                email="teaches-here-parents-there@example.com", password=PASSWORD
            )

        assert result["status"] == "select_tenant"
        choices_by_tenant = {c["tenant_id"]: c["role"] for c in result["choices"]}
        assert choices_by_tenant == {"tenant_a": "teacher", "tenant_b": "parent"}
        # tenant_a's candidate must have gone through the real tenant-local
        # lookup, not been satisfied purely by the parent record's hash.
        user_repo.get_user_by_email.assert_awaited()
