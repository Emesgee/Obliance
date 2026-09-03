"""Operator CLI — `python -m app.cli <command>` (bidflow ADR-0070, behind SSH).

bootstrap  create an organisation and its first Systemadministrator.
           The only way to get a first login; there is no public signup
           (invitations come in auth increment 2).
"""

from __future__ import annotations

import argparse
import getpass
import sys

from sqlalchemy import func, select

from app.core.config import settings
from app.core.db import SessionLocal
from app.core.security import hash_password
from app.domain.models import MemberRole, Organization, OrganizationMember, Profile


def cmd_bootstrap(args: argparse.Namespace) -> int:
    password = args.password or getpass.getpass("Adgangskode: ")
    if len(password) < settings.password_min_length:
        print(f"Adgangskoden skal være mindst {settings.password_min_length} tegn", file=sys.stderr)
        return 2
    # Identity tables carry no RLS (ADR-0002) — the app role may write them directly.
    with SessionLocal() as s:
        org = s.scalars(select(Organization).where(Organization.slug == args.slug)).first()
        if org is None:
            org = Organization(name=args.org, slug=args.slug)
            s.add(org)
            s.flush()
            print(f"organisation oprettet: {org.name} ({org.slug})")
        else:
            print(f"organisation findes: {org.name} ({org.slug})")

        user = s.scalars(
            select(Profile).where(func.lower(Profile.email) == args.email.lower())
        ).first()
        if user is None:
            user = Profile(email=args.email, name=args.name, password_hash=hash_password(password))
            s.add(user)
            s.flush()
            print(f"bruger oprettet: {user.email}")
        else:
            user.password_hash = hash_password(password)
            print(f"bruger findes: {user.email} — adgangskode opdateret")

        mem = s.get(OrganizationMember, (org.id, user.id))
        if mem is None:
            s.add(
                OrganizationMember(
                    organization_id=org.id, profile_id=user.id, role=MemberRole.systemadministrator
                )
            )
            print("medlemskab: systemadministrator")
        s.commit()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    b = sub.add_parser("bootstrap", help="opret organisation + første Systemadministrator")
    b.add_argument("--org", required=True, help='organisationens navn, fx "Amgros I/S"')
    b.add_argument("--slug", required=True, help="kort id, fx amgros")
    b.add_argument("--email", required=True)
    b.add_argument("--name", required=True)
    b.add_argument("--password", help="udelad for at blive spurgt (anbefalet)")
    b.set_defaults(func=cmd_bootstrap)

    args = parser.parse_args(argv)
    result: int = args.func(args)
    return result


if __name__ == "__main__":
    sys.exit(main())
