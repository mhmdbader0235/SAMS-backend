"""AuthService.change_password.

There are three disjoint password stores depending on role, matching
login_user's own branching: public.super_admins, the global public.parents
row, and each tenant's own users table. A change must verify against, and
write to, the SAME record login_user reads -- writing to the wrong one would
let the change "succeed" while login keeps authenticating against the
untouched hash.

No database and no FastAPI: ControlPlaneRepository and the tenant-pool
getters are patched at the app.domains.auth.service module boundary, mirroring
test_auth_login_multi_school.py.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.domains.auth.service import AuthService
from app.domains.tenant.control_plane_repository import ControlPlaneRepository

OLD_PASSWORD = "correct-horse-battery-staple1"
NEW_PASSWORD = "new-correct-horse-battery2"


@pytest.fixture(autouse=True)
def _no_real_keycloak_calls():
    """change_password best-effort-syncs the new password to Keycloak. Without
    this patch these tests would fire a real network call (and rely on its
    internal try/except to swallow the resulting connection error) on every
    run -- patched instead so the suite stays fast and hermetic, matching how
    login/registration tests already stub out Keycloak sync calls."""
    with patch(
        "app.domains.auth.service.update_user_password_in_keycloak", return_value=True
    ) as mock_sync:
        yield mock_sync


def _fake_cp(**overrides) -> MagicMock:
    cp = MagicMock(spec=ControlPlaneRepository)
    cp.get_super_admin_by_email = AsyncMock(return_value=None)
    cp.get_parent_by_email = AsyncMock(return_value=None)
    cp.update_super_admin_password = AsyncMock(return_value=None)
    cp.update_parent_password = AsyncMock(return_value=None)
    for k, v in overrides.items():
        setattr(cp, k, v)
    return cp


def _patches(cp: MagicMock, *, user_repo: MagicMock | None = None):
    pool = MagicMock()
    return (
        patch("app.domains.auth.service.ControlPlaneRepository", return_value=cp),
        patch(
            "app.domains.auth.service.get_control_plane_pool",
            AsyncMock(return_value=pool),
        ),
        patch("app.domains.auth.service.get_db_pool", AsyncMock(return_value=pool)),
        patch(
            "app.domains.auth.service.UserRepository",
            return_value=user_repo if user_repo is not None else MagicMock(),
        ),
    )


class TestSuperAdminPasswordChange:
    async def test_wrong_current_password_is_rejected(self):
        cp = _fake_cp(
            get_super_admin_by_email=AsyncMock(
                return_value={
                    "id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                    "password_hash": AuthService.hash_password(OLD_PASSWORD),
                }
            )
        )
        with (
            patch("app.domains.auth.service.ControlPlaneRepository", return_value=cp),
            patch(
                "app.domains.auth.service.get_control_plane_pool",
                AsyncMock(return_value=MagicMock()),
            ),
            pytest.raises(ValueError, match="incorrect"),
        ):
            await AuthService.change_password(
                user_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                email="root@example.com",
                role="super_admin",
                tenant_id="",
                current_password="not-the-password",
                new_password=NEW_PASSWORD,
            )
        cp.update_super_admin_password.assert_not_awaited()

    async def test_correct_current_password_rewrites_the_hash(self):
        cp = _fake_cp(
            get_super_admin_by_email=AsyncMock(
                return_value={
                    "id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                    "password_hash": AuthService.hash_password(OLD_PASSWORD),
                }
            )
        )
        with (
            patch("app.domains.auth.service.ControlPlaneRepository", return_value=cp),
            patch(
                "app.domains.auth.service.get_control_plane_pool",
                AsyncMock(return_value=MagicMock()),
            ),
        ):
            await AuthService.change_password(
                user_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                email="root@example.com",
                role="super_admin",
                tenant_id="",
                current_password=OLD_PASSWORD,
                new_password=NEW_PASSWORD,
            )
        cp.update_super_admin_password.assert_awaited_once()
        new_hash = cp.update_super_admin_password.await_args.args[1]
        assert AuthService.verify_password(NEW_PASSWORD, new_hash)


class TestSsoManagedAccountsCannotChangeLocalPassword:
    async def test_placeholder_hash_is_rejected_as_permission_error(self):
        """A 'managed'/'keycloak_managed' placeholder is not a real password --
        it must surface as a distinct, clearer error than 'current password
        incorrect', since no current_password could ever satisfy it."""
        cp = _fake_cp(
            get_super_admin_by_email=AsyncMock(
                return_value={
                    "id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                    "password_hash": "managed",
                }
            )
        )
        with (
            patch("app.domains.auth.service.ControlPlaneRepository", return_value=cp),
            patch(
                "app.domains.auth.service.get_control_plane_pool",
                AsyncMock(return_value=MagicMock()),
            ),
            pytest.raises(PermissionError, match="single sign-on"),
        ):
            await AuthService.change_password(
                user_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                email="sso-user@example.com",
                role="super_admin",
                tenant_id="",
                current_password="anything",
                new_password=NEW_PASSWORD,
            )


class TestParentPasswordChange:
    async def test_updates_global_control_plane_record_and_tenant_mirror(self):
        """login_user's parent branch only ever verifies against the GLOBAL
        public.parents hash (see test_auth_login_multi_school.py) -- so that is
        the record a parent's change must write to. The tenant-local mirror row
        is best-effort so it does not silently drift, even though login never
        reads it for a control-plane parent."""
        parent_id = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
        cp = _fake_cp(
            get_parent_by_email=AsyncMock(
                return_value={
                    "id": parent_id,
                    "password_hash": AuthService.hash_password(OLD_PASSWORD),
                }
            )
        )
        user_repo = MagicMock()
        user_repo.get_user_by_email = AsyncMock(return_value={"id": 99})
        user_repo.update_user_password_hash = AsyncMock(return_value=True)

        p1, p2, p3, p4 = _patches(cp, user_repo=user_repo)
        with p1, p2, p3, p4:
            await AuthService.change_password(
                user_id=99,
                email="parent@example.com",
                role="parent",
                tenant_id="tenant_a",
                current_password=OLD_PASSWORD,
                new_password=NEW_PASSWORD,
            )

        cp.update_parent_password.assert_awaited_once()
        assert cp.update_parent_password.await_args.args[0] == parent_id
        user_repo.update_user_password_hash.assert_awaited_once()
        assert user_repo.update_user_password_hash.await_args.args[0] == 99

    async def test_wrong_current_password_never_touches_any_store(self):
        cp = _fake_cp(
            get_parent_by_email=AsyncMock(
                return_value={
                    "id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
                    "password_hash": AuthService.hash_password(OLD_PASSWORD),
                }
            )
        )
        user_repo = MagicMock()
        user_repo.get_user_by_email = AsyncMock(return_value={"id": 1})
        user_repo.update_user_password_hash = AsyncMock(return_value=True)

        p1, p2, p3, p4 = _patches(cp, user_repo=user_repo)
        with p1, p2, p3, p4, pytest.raises(ValueError, match="incorrect"):
            await AuthService.change_password(
                user_id=1,
                email="parent@example.com",
                role="parent",
                tenant_id="tenant_a",
                current_password="wrong",
                new_password=NEW_PASSWORD,
            )

        cp.update_parent_password.assert_not_awaited()
        user_repo.update_user_password_hash.assert_not_awaited()


class TestTenantUserPasswordChange:
    async def test_teacher_password_change_writes_tenant_users_table(self):
        cp = _fake_cp()
        user_repo = MagicMock()
        user_repo.get_user_by_email = AsyncMock(
            return_value={
                "id": 7,
                "password_hash": AuthService.hash_password(OLD_PASSWORD),
            }
        )
        user_repo.update_user_password_hash = AsyncMock(return_value=True)

        p1, p2, p3, p4 = _patches(cp, user_repo=user_repo)
        with p1, p2, p3, p4:
            result = await AuthService.change_password(
                user_id=7,
                email="teacher@example.com",
                role="teacher",
                tenant_id="tenant_b",
                current_password=OLD_PASSWORD,
                new_password=NEW_PASSWORD,
            )

        user_repo.update_user_password_hash.assert_awaited_once()
        called_id, new_hash = user_repo.update_user_password_hash.await_args.args
        assert called_id == 7
        assert AuthService.verify_password(NEW_PASSWORD, new_hash)
        cp.update_super_admin_password.assert_not_awaited()
        cp.update_parent_password.assert_not_awaited()
        assert result is True

    async def test_reports_when_keycloak_sync_did_not_happen(self, _no_real_keycloak_calls):
        """The return value is what lets a caller (the router, then the UI)
        tell 'SSO has the new password too' apart from 'local change worked
        but Keycloak wasn't reachable' -- silently reporting success either
        way is exactly the gap that made the real regression this guards
        invisible until a user found it by trying to log in via SSO."""
        _no_real_keycloak_calls.return_value = False
        cp = _fake_cp()
        user_repo = MagicMock()
        user_repo.get_user_by_email = AsyncMock(
            return_value={
                "id": 7,
                "password_hash": AuthService.hash_password(OLD_PASSWORD),
            }
        )
        user_repo.update_user_password_hash = AsyncMock(return_value=True)

        p1, p2, p3, p4 = _patches(cp, user_repo=user_repo)
        with p1, p2, p3, p4:
            result = await AuthService.change_password(
                user_id=7,
                email="teacher@example.com",
                role="teacher",
                tenant_id="tenant_b",
                current_password=OLD_PASSWORD,
                new_password=NEW_PASSWORD,
            )

        # The local change must still have gone through even though the
        # Keycloak sync did not.
        user_repo.update_user_password_hash.assert_awaited_once()
        assert result is False

    async def test_new_password_same_as_current_is_rejected(self):
        cp = _fake_cp()
        user_repo = MagicMock()
        user_repo.get_user_by_email = AsyncMock(
            return_value={
                "id": 7,
                "password_hash": AuthService.hash_password(OLD_PASSWORD),
            }
        )
        user_repo.update_user_password_hash = AsyncMock(return_value=True)

        p1, p2, p3, p4 = _patches(cp, user_repo=user_repo)
        with p1, p2, p3, p4, pytest.raises(ValueError, match="different"):
            await AuthService.change_password(
                user_id=7,
                email="teacher@example.com",
                role="teacher",
                tenant_id="tenant_b",
                current_password=OLD_PASSWORD,
                new_password=OLD_PASSWORD,
            )

        user_repo.update_user_password_hash.assert_not_awaited()

    async def test_unknown_account_is_rejected(self):
        cp = _fake_cp()
        user_repo = MagicMock()
        user_repo.get_user_by_email = AsyncMock(return_value=None)
        user_repo.update_user_password_hash = AsyncMock(return_value=True)

        p1, p2, p3, p4 = _patches(cp, user_repo=user_repo)
        with p1, p2, p3, p4, pytest.raises(ValueError, match="not found"):
            await AuthService.change_password(
                user_id=7,
                email="ghost@example.com",
                role="teacher",
                tenant_id="tenant_b",
                current_password=OLD_PASSWORD,
                new_password=NEW_PASSWORD,
            )
