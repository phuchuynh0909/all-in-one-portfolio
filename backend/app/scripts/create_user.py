"""Create a user, reset a password, or revoke access.

    python -m app.scripts.create_user phuc
    python -m app.scripts.create_user phuc --deactivate
    python -m app.scripts.create_user phuc --activate

Lives under ``app/`` rather than ``backend/scripts/`` because the Docker image
copies only ``app``, ``tasks``, ``alembic`` and ``libs`` — a file in
``backend/scripts/`` would not exist in the container.

The password is prompted, never accepted as an argument: an argv password lands
in shell history and in the process list.
"""
from __future__ import annotations

import argparse
import getpass
import sys

from app.core.security import hash_password
from app.db.base import SessionLocal
from app.db.models.user import User


def _prompt_for_password() -> str:
    password = getpass.getpass("Password: ")
    if not password:
        print("error: password must not be empty", file=sys.stderr)
        raise SystemExit(1)
    if password != getpass.getpass("Confirm:  "):
        print("error: passwords do not match", file=sys.stderr)
        raise SystemExit(1)
    return password


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Create a user, reset their password, or revoke access."
    )
    parser.add_argument("username")
    flags = parser.add_mutually_exclusive_group()
    flags.add_argument(
        "--deactivate",
        action="store_true",
        help="revoke access; existing tokens stop working immediately",
    )
    flags.add_argument(
        "--activate", action="store_true", help="restore a deactivated user"
    )
    args = parser.parse_args(argv)

    session = SessionLocal()
    try:
        user = (
            session.query(User).filter(User.username == args.username).one_or_none()
        )

        if args.deactivate or args.activate:
            if user is None:
                print(f"error: no user {args.username!r}", file=sys.stderr)
                return 1
            user.is_active = args.activate
            session.commit()
            state = "activated" if args.activate else "deactivated"
            print(f"{state} {user.username!r} (id={user.id})")
            return 0

        try:
            digest = hash_password(_prompt_for_password())
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

        if user is None:
            user = User(username=args.username, password_hash=digest, is_active=True)
            session.add(user)
            session.commit()
            print(f"created {user.username!r} (id={user.id})")
        else:
            user.password_hash = digest
            user.is_active = True
            session.commit()
            print(f"updated password for {user.username!r} (id={user.id})")
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
