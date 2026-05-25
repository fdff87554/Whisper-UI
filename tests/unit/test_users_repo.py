from __future__ import annotations

import sqlite3

import pytest

from whisper_ui.storage import users_repo
from whisper_ui.storage.users_repo import LastAdminError


@pytest.fixture
def conn(db) -> sqlite3.Connection:
    """A connection to an initialised, in-memory-style temp DB."""
    return db.conn


def test_create_user_returns_user_with_id_and_hash(conn):
    user = users_repo.create_user(conn, "alice", "password123")

    assert user.id > 0
    assert user.username == "alice"
    assert user.password_hash.startswith("$argon2id$")
    assert user.is_admin is False
    assert user.is_active is True
    assert user.session_version == 0


def test_create_user_admin_flag_persists(conn):
    user = users_repo.create_user(conn, "root", "password123", is_admin=True)

    assert user.is_admin is True


def test_usernames_for_returns_mapping_for_requested_ids(conn):
    alice = users_repo.create_user(conn, "alice", "password123")
    bob = users_repo.create_user(conn, "bob", "password123")

    result = users_repo.usernames_for(conn, [alice.id, bob.id])

    assert result == {alice.id: "alice", bob.id: "bob"}


def test_usernames_for_skips_unknown_ids(conn):
    alice = users_repo.create_user(conn, "alice", "password123")

    result = users_repo.usernames_for(conn, [alice.id, 999999])

    assert result == {alice.id: "alice"}


def test_usernames_for_empty_input_returns_empty_dict(conn):
    users_repo.create_user(conn, "alice", "password123")

    assert users_repo.usernames_for(conn, []) == {}


def test_usernames_for_deduplicates_ids(conn):
    alice = users_repo.create_user(conn, "alice", "password123")

    assert users_repo.usernames_for(conn, [alice.id, alice.id]) == {alice.id: "alice"}


def test_create_user_duplicate_username_raises(conn):
    users_repo.create_user(conn, "alice", "password123")

    with pytest.raises(sqlite3.IntegrityError):
        users_repo.create_user(conn, "alice", "differentpw")


def test_create_user_collation_treats_username_case_insensitively(conn):
    users_repo.create_user(conn, "Alice", "password123")

    with pytest.raises(sqlite3.IntegrityError):
        users_repo.create_user(conn, "alice", "password123")


def test_get_user_by_username_matches_case_insensitively(conn):
    users_repo.create_user(conn, "Alice", "password123")

    found = users_repo.get_user_by_username(conn, "alice")

    assert found is not None
    assert found.username == "Alice"


def test_get_user_by_id_returns_none_for_missing_id(conn):
    assert users_repo.get_user_by_id(conn, 9999) is None


def test_get_user_by_username_returns_none_for_missing_name(conn):
    assert users_repo.get_user_by_username(conn, "nobody") is None


def test_verify_password_correct_returns_true(conn):
    user = users_repo.create_user(conn, "alice", "password123")

    assert users_repo.verify_password(user, "password123") is True


def test_verify_password_wrong_returns_false(conn):
    user = users_repo.create_user(conn, "alice", "password123")

    assert users_repo.verify_password(user, "wrongpassword") is False


def test_dummy_verify_does_not_raise(conn):
    # The function exists to even out timing; it should never propagate
    # exceptions even when the hashing fails internally.
    users_repo.dummy_verify("any password")


def test_count_active_admins_excludes_non_admin(conn):
    users_repo.create_user(conn, "alice", "password123", is_admin=False)
    users_repo.create_user(conn, "root", "password123", is_admin=True)

    assert users_repo.count_active_admins(conn) == 1


def test_count_active_admins_excludes_inactive_admin(conn):
    admin = users_repo.create_user(conn, "root", "password123", is_admin=True)
    users_repo.create_user(conn, "root2", "password123", is_admin=True)
    users_repo.set_active(conn, admin.id, active=False)

    assert users_repo.count_active_admins(conn) == 1


def test_set_password_bumps_session_version(conn):
    user = users_repo.create_user(conn, "alice", "password123")

    users_repo.set_password(conn, user.id, "newpassword")

    updated = users_repo.get_user_by_id(conn, user.id)
    assert updated is not None
    assert updated.session_version == user.session_version + 1
    assert users_repo.verify_password(updated, "newpassword") is True
    assert users_repo.verify_password(updated, "password123") is False


def test_set_active_deactivate_bumps_session_version(conn):
    user = users_repo.create_user(conn, "alice", "password123")

    users_repo.set_active(conn, user.id, active=False)

    updated = users_repo.get_user_by_id(conn, user.id)
    assert updated is not None
    assert updated.is_active is False
    assert updated.session_version == user.session_version + 1


def test_set_active_activate_does_not_bump_session_version(conn):
    user = users_repo.create_user(conn, "alice", "password123")
    users_repo.set_active(conn, user.id, active=False)
    sv_after_deactivate = users_repo.get_user_by_id(conn, user.id).session_version

    users_repo.set_active(conn, user.id, active=True)

    updated = users_repo.get_user_by_id(conn, user.id)
    assert updated.is_active is True
    assert updated.session_version == sv_after_deactivate


def test_set_active_cannot_deactivate_last_active_admin(conn):
    admin = users_repo.create_user(conn, "root", "password123", is_admin=True)

    with pytest.raises(LastAdminError):
        users_repo.set_active(conn, admin.id, active=False)


def test_set_active_can_deactivate_admin_when_other_admin_exists(conn):
    admin1 = users_repo.create_user(conn, "root", "password123", is_admin=True)
    users_repo.create_user(conn, "root2", "password123", is_admin=True)

    users_repo.set_active(conn, admin1.id, active=False)

    assert users_repo.count_active_admins(conn) == 1


def test_set_admin_cannot_demote_last_active_admin(conn):
    admin = users_repo.create_user(conn, "root", "password123", is_admin=True)

    with pytest.raises(LastAdminError):
        users_repo.set_admin(conn, admin.id, admin=False)


def test_set_admin_can_demote_when_other_admin_exists(conn):
    admin1 = users_repo.create_user(conn, "root", "password123", is_admin=True)
    users_repo.create_user(conn, "root2", "password123", is_admin=True)

    users_repo.set_admin(conn, admin1.id, admin=False)

    updated = users_repo.get_user_by_id(conn, admin1.id)
    assert updated.is_admin is False


def test_set_admin_promote_works(conn):
    user = users_repo.create_user(conn, "alice", "password123")

    users_repo.set_admin(conn, user.id, admin=True)

    updated = users_repo.get_user_by_id(conn, user.id)
    assert updated.is_admin is True


def test_set_admin_demote_inactive_admin_does_not_trigger_last_admin_guard(conn):
    # Inactive admins do not count as "active admins", so demoting an
    # already-inactive admin is allowed even when only one other active admin exists.
    users_repo.create_user(conn, "keeper", "password123", is_admin=True)
    inactive_admin = users_repo.create_user(conn, "old_root", "password123", is_admin=True)
    users_repo.set_active(conn, inactive_admin.id, active=False)

    # `keeper` is the only active admin; demoting `inactive_admin` doesn't
    # touch the active-admin count, so the guard does not trip.
    users_repo.set_admin(conn, inactive_admin.id, admin=False)

    assert users_repo.get_user_by_id(conn, inactive_admin.id).is_admin is False


def test_list_users_returns_users_in_creation_order(conn):
    users_repo.create_user(conn, "alice", "password123")
    users_repo.create_user(conn, "bob", "password123")
    users_repo.create_user(conn, "carol", "password123")

    users = users_repo.list_users(conn)

    assert [u.username for u in users] == ["alice", "bob", "carol"]


def test_set_password_for_missing_user_is_silent_noop(conn):
    # UPDATE matching zero rows is not an error in sqlite — set_password
    # is allowed to be a no-op for a missing id. Callers that need stronger
    # guarantees should check existence first.
    users_repo.set_password(conn, 9999, "newpassword")


def test_set_active_missing_user_raises(conn):
    with pytest.raises(ValueError, match="not found"):
        users_repo.set_active(conn, 9999, active=False)


def test_set_admin_missing_user_raises(conn):
    with pytest.raises(ValueError, match="not found"):
        users_repo.set_admin(conn, 9999, admin=True)
