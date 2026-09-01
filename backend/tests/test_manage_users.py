"""The host-side user admin CLI in ``backend/scripts/manage_users.py``.

The script is not importable as a package member (``scripts/`` is not a
package, and deliberately is not copied into the image), so it is loaded by
path. Every database test runs on the rolled-back ``db`` fixture, so the rows
it creates never survive.
"""
from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path

import pytest

from app.db.models.user import User
from tests.conftest import requires_mysql

BACKEND_ROOT = Path(__file__).resolve().parent.parent


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "manage_users", BACKEND_ROOT / "scripts" / "manage_users.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


manage_users = _load_module()


def _args(**kwargs) -> argparse.Namespace:
    kwargs.setdefault("yes", False)
    return argparse.Namespace(**kwargs)


@pytest.fixture
def existing(db):
    user = User(
        username="cli-test-user",
        password_hash=manage_users.hash_password("original-password"),
        is_active=True,
    )
    db.add(user)
    db.flush()
    return user


# --- argument parsing (no database) -----------------------------------------

def test_every_command_is_wired_to_a_handler():
    """A subparser with no entry in COMMANDS would KeyError at runtime."""
    parser = manage_users.build_parser()
    choices = parser._subparsers._group_actions[0].choices
    assert set(choices) == set(manage_users.COMMANDS)


def test_a_missing_subcommand_is_an_error():
    with pytest.raises(SystemExit):
        manage_users.build_parser().parse_args([])


def test_delete_requires_a_username():
    with pytest.raises(SystemExit):
        manage_users.build_parser().parse_args(["delete"])


def test_delete_defaults_to_asking_for_confirmation():
    assert manage_users.build_parser().parse_args(["delete", "x"]).yes is False
    assert manage_users.build_parser().parse_args(["delete", "x", "--yes"]).yes is True


# --- not-found paths --------------------------------------------------------

@requires_mysql
@pytest.mark.parametrize(
    "command", ["cmd_passwd", "cmd_activate", "cmd_deactivate", "cmd_delete"]
)
def test_operating_on_an_unknown_user_fails(db, command):
    args = _args(username="definitely-not-a-real-user", yes=True)
    assert getattr(manage_users, command)(db, args) == 1


# --- the lifecycle ----------------------------------------------------------

@requires_mysql
def test_create_then_verify_the_stored_password(db, monkeypatch):
    monkeypatch.setattr(manage_users, "_prompt_new_password", lambda: "chosen-password")
    assert manage_users.cmd_create(db, _args(username="cli-created")) == 0

    from app.core.security import verify_password

    user = manage_users._find(db, "cli-created")
    assert user is not None
    assert verify_password("chosen-password", user.password_hash) is True
    assert user.is_active is True


@requires_mysql
def test_create_refuses_to_overwrite_an_existing_user(db, existing, monkeypatch):
    """The whole point of diverging from the in-image script's upsert."""
    monkeypatch.setattr(manage_users, "_prompt_new_password", lambda: "new-password")
    assert manage_users.cmd_create(db, _args(username=existing.username)) == 1

    from app.core.security import verify_password

    # The original password must survive the refused create.
    assert verify_password("original-password", existing.password_hash) is True


@requires_mysql
def test_passwd_replaces_the_hash(db, existing, monkeypatch):
    before = existing.password_hash
    monkeypatch.setattr(manage_users, "_prompt_new_password", lambda: "second-password")
    assert manage_users.cmd_passwd(db, _args(username=existing.username)) == 0

    from app.core.security import verify_password

    assert existing.password_hash != before
    assert verify_password("second-password", existing.password_hash) is True
    assert verify_password("original-password", existing.password_hash) is False


@requires_mysql
def test_deactivate_then_activate_round_trips(db, existing):
    assert manage_users.cmd_deactivate(db, _args(username=existing.username)) == 0
    assert bool(existing.is_active) is False
    assert manage_users.cmd_activate(db, _args(username=existing.username)) == 0
    assert bool(existing.is_active) is True


@requires_mysql
def test_repeating_deactivate_is_a_no_op_not_an_error(db, existing):
    assert manage_users.cmd_deactivate(db, _args(username=existing.username)) == 0
    assert manage_users.cmd_deactivate(db, _args(username=existing.username)) == 0
    assert bool(existing.is_active) is False


@requires_mysql
def test_delete_with_yes_skips_the_prompt(db, existing, monkeypatch):
    def refuse_input(*_args, **_kwargs):
        raise AssertionError("--yes must not prompt")

    monkeypatch.setattr("builtins.input", refuse_input)
    assert manage_users.cmd_delete(db, _args(username=existing.username, yes=True)) == 0
    assert manage_users._find(db, existing.username) is None


@requires_mysql
def test_delete_aborts_when_the_typed_name_does_not_match(db, existing, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *_: "wrong-name")
    assert manage_users.cmd_delete(db, _args(username=existing.username)) == 1
    assert manage_users._find(db, existing.username) is not None


@requires_mysql
def test_delete_proceeds_when_the_name_is_retyped(db, existing, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *_: existing.username)
    assert manage_users.cmd_delete(db, _args(username=existing.username)) == 0
    assert manage_users._find(db, existing.username) is None


# --- list -------------------------------------------------------------------

@requires_mysql
def test_list_reports_the_user_and_its_state(db, existing, capsys):
    assert manage_users.cmd_list(db, _args()) == 0
    out = capsys.readouterr().out
    assert existing.username in out
    assert "USERNAME" in out


@requires_mysql
def test_list_marks_a_deactivated_user_as_not_active(db, existing, capsys):
    manage_users.cmd_deactivate(db, _args(username=existing.username))
    capsys.readouterr()
    manage_users.cmd_list(db, _args())
    row = [
        line for line in capsys.readouterr().out.splitlines()
        if existing.username in line
    ]
    assert row and " no " in row[0]
