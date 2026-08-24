from __future__ import annotations

import argparse
import getpass

from .auth import AuthRepository, hash_password, normalize_username
from .config import Settings
from .db import Database


def build_repository() -> AuthRepository:
    settings = Settings()
    settings.prepare_directories()
    database = Database(settings.database_path)
    database.initialize()
    return AuthRepository(database)


def prompt_password() -> str:
    password = getpass.getpass("Password: ")
    confirmation = getpass.getpass("Confirm password: ")
    if password != confirmation:
        raise SystemExit("Passwords do not match")
    return password


def create_user(args) -> None:
    repository = build_repository()
    normalized = normalize_username(args.username)
    user = repository.create_user(
        args.username.strip(),
        normalized,
        hash_password(prompt_password()),
        is_admin=args.admin,
    )
    print(f"Created user {user.username} ({user.id})")


def reset_password(args) -> None:
    repository = build_repository()
    user = repository.get_user_by_username(normalize_username(args.username))
    if user is None or user.id == "local":
        raise SystemExit("User not found")
    repository.update_password(user.id, hash_password(prompt_password()))
    print(f"Reset password for {user.username}; existing sessions were revoked")


def set_active(args, active: bool) -> None:
    repository = build_repository()
    user = repository.get_user_by_username(normalize_username(args.username))
    if user is None or user.id == "local":
        raise SystemExit("User not found")
    repository.set_active(user.id, active)
    state = "enabled" if active else "disabled"
    print(f"{state}: {user.username}")


def list_users(_: argparse.Namespace) -> None:
    for user in build_repository().list_users():
        role = "admin" if user.is_admin else "user"
        state = "active" if user.is_active else "disabled"
        print(f"{user.username}\t{role}\t{state}\t{user.created_at}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage invited users")
    commands = parser.add_subparsers(dest="command", required=True)

    create = commands.add_parser("create")
    create.add_argument("--username", required=True)
    create.add_argument("--admin", action="store_true")
    create.set_defaults(handler=create_user)

    reset = commands.add_parser("reset-password")
    reset.add_argument("--username", required=True)
    reset.set_defaults(handler=reset_password)

    disable = commands.add_parser("disable")
    disable.add_argument("--username", required=True)
    disable.set_defaults(handler=lambda args: set_active(args, False))

    enable = commands.add_parser("enable")
    enable.add_argument("--username", required=True)
    enable.set_defaults(handler=lambda args: set_active(args, True))

    listing = commands.add_parser("list")
    listing.set_defaults(handler=list_users)

    args = parser.parse_args()
    args.handler(args)


if __name__ == "__main__":
    main()
