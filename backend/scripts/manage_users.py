#!/usr/bin/env python3
"""Manage application logins (the ``users`` table) from the host.

Usage:
    python backend/scripts/manage_users.py list
    python backend/scripts/manage_users.py create <username>
    python backend/scripts/manage_users.py passwd <username>
    python backend/scripts/manage_users.py deactivate <username>
    python backend/scripts/manage_users.py activate <username>
    python backend/scripts/manage_users.py delete <username> [--yes]

Talks to MySQL directly, so it works whether or not the stack is running. The
in-image equivalent is ``python -m app.scripts.create_user``: use that inside a
container (production, where the host may not have the dependencies), and this
one for local administration. This script additionally offers ``list`` and
``delete``.

Unlike the in-image script, ``create`` refuses to overwrite an existing user —
resetting a password is ``passwd``, so a typo in a username cannot silently
replace someone's credentials.

Passwords are always prompted, never taken as arguments: an argv password lands
in shell history and in the process list.
"""
from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from dotenv import load_dotenv  # noqa: E402

# The neighbouring scripts load ``BACKEND_ROOT / ".env"``, which does not exist:
# the real file is at the repository root, and that is also what
# docker-compose.yml hands the backend via ``env_file``. Load that one, then a
# backend-local override if such a file is ever added.
load_dotenv(REPO_ROOT / ".env")
load_dotenv(BACKEND_ROOT / ".env", override=False)

from sqlalchemy.orm import Session  # noqa: E402

from app.core.security import hash_password  # noqa: E402
from app.db.base import SessionLocal, engine  # noqa: E402
from app.db.models.user import User  # noqa: E402

# ``settings.environment == "development"`` builds the engine with echo=True,
# which buries this CLI's output in raw SQL. Turned off here rather than by
# claiming to be production, which would trip the signing-key hard stop in
# app/core/security.py.
engine.echo = False


def _prompt_new_password() -> str:
    """Ask twice. Raises ``ValueError`` if empty or mismatched."""
    password = getpass.getpass("Password: ")
    if not password:
        raise ValueError("password must not be empty")
    if password != getpass.getpass("Confirm:  "):
        raise ValueError("passwords do not match")
    return password


def _find(session: Session, username: str) -> User | None:
    return session.query(User).filter(User.username == username).one_or_none()


def _missing(username: str) -> int:
    print(f"error: no user {username!r}", file=sys.stderr)
    return 1


def cmd_list(session: Session, args: argparse.Namespace) -> int:
    users = session.query(User).order_by(User.id).all()
    if not users:
        print("no users yet — create one with:")
        print("  python backend/scripts/manage_users.py create <username>")
        return 0

    width = max(len("USERNAME"), *(len(u.username) for u in users))
    print(f"{'ID':>4}  {'USERNAME':<{width}}  {'ACTIVE':<6}  CREATED")
    for user in users:
        created = (
            user.created_at.strftime("%Y-%m-%d %H:%M") if user.created_at else "-"
        )
        active = "yes" if user.is_active else "no"
        print(f"{user.id:>4}  {user.username:<{width}}  {active:<6}  {created}")
    return 0


def cmd_create(session: Session, args: argparse.Namespace) -> int:
    if _find(session, args.username) is not None:
        print(
            f"error: {args.username!r} already exists; use `passwd` to reset "
            "their password",
            file=sys.stderr,
        )
        return 1
    try:
        digest = hash_password(_prompt_new_password())
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    user = User(username=args.username, password_hash=digest, is_active=True)
    session.add(user)
    session.commit()
    print(f"created {user.username!r} (id={user.id})")
    return 0


def cmd_passwd(session: Session, args: argparse.Namespace) -> int:
    user = _find(session, args.username)
    if user is None:
        return _missing(args.username)
    try:
        user.password_hash = hash_password(_prompt_new_password())
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    session.commit()
    print(f"password updated for {user.username!r} (id={user.id})")
    return 0


def _set_active(session: Session, username: str, active: bool) -> int:
    user = _find(session, username)
    if user is None:
        return _missing(username)
    if bool(user.is_active) == active:
        print(f"{username!r} is already {'active' if active else 'inactive'}")
        return 0

    user.is_active = active
    session.commit()
    if active:
        print(f"activated {username!r} (id={user.id})")
    else:
        print(
            f"deactivated {username!r} (id={user.id}) — existing tokens stop "
            "working immediately"
        )
    return 0


def cmd_activate(session: Session, args: argparse.Namespace) -> int:
    return _set_active(session, args.username, True)


def cmd_deactivate(session: Session, args: argparse.Namespace) -> int:
    return _set_active(session, args.username, False)


def cmd_delete(session: Session, args: argparse.Namespace) -> int:
    user = _find(session, args.username)
    if user is None:
        return _missing(args.username)

    if not args.yes:
        # Deleting is unrecoverable, so make it cost a deliberate keystroke
        # rather than a stray Enter. --deactivate is almost always what you want.
        print(f"This permanently deletes user {args.username!r} (id={user.id}).")
        print("To revoke access reversibly instead, use `deactivate`.")
        if input("Retype the username to confirm: ") != args.username:
            print("aborted", file=sys.stderr)
            return 1

    session.delete(user)
    session.commit()
    print(f"deleted {args.username!r}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manage application logins (the users table).",
        epilog="Passwords are always prompted, never passed as arguments.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="show every login and whether it is active")

    for name, help_text in (
        ("create", "add a new login (fails if it already exists)"),
        ("passwd", "reset an existing login's password"),
        ("activate", "restore a deactivated login"),
        ("deactivate", "revoke access; existing tokens stop working at once"),
    ):
        child = sub.add_parser(name, help=help_text)
        child.add_argument("username")

    delete = sub.add_parser("delete", help="remove a login permanently")
    delete.add_argument("username")
    delete.add_argument(
        "--yes",
        action="store_true",
        help="skip the retype-to-confirm prompt (for scripted use)",
    )

    return parser


COMMANDS = {
    "list": cmd_list,
    "create": cmd_create,
    "passwd": cmd_passwd,
    "activate": cmd_activate,
    "deactivate": cmd_deactivate,
    "delete": cmd_delete,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    session = SessionLocal()
    try:
        return COMMANDS[args.command](session, args)
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
