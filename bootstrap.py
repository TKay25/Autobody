"""One-command local setup.

    python bootstrap.py            # create tables + load demo data
    python bootstrap.py --reset    # wipe everything first
    python bootstrap.py --no-demo  # reference data only (production)
"""
from __future__ import annotations

import argparse
import sys

from app import create_app
from app.extensions import db
from app.seed import run_seed
from config import get_config


def main() -> int:
    parser = argparse.ArgumentParser(description="Set up the Topclass Workshop OS database.")
    parser.add_argument("--reset", action="store_true", help="Drop all tables first.")
    parser.add_argument("--no-demo", action="store_true", help="Skip demo customers and jobs.")
    args = parser.parse_args()

    # Honours FLASK_ENV, so the same command works locally and on a host.
    app = create_app(get_config())
    with app.app_context():
        if args.reset:
            print("Dropping all tables…")
            db.drop_all()
        print("Creating tables…")
        db.create_all()
        print("Seeding…")
        run_seed(with_demo=not args.no_demo)

        from app.models import Booking, Customer, JobCard, Part, User, WaConversation
        from app.seed import DEFAULT_PASSWORD, STAFF, seed_password

        print("\nDone.")
        print(f"  users        {User.query.count()}")
        print(f"  customers    {Customer.query.count()}")
        print(f"  job cards    {JobCard.query.count()}")
        print(f"  stock items  {Part.query.count()}")
        print(f"  bookings     {Booking.query.count()}")
        print(f"  wa threads   {WaConversation.query.count()}")

        # Don't advertise a password the operator overrode: on a host it almost
        # certainly came from SEED_PASSWORD, and claiming the published default
        # is how you end up locked out of your own deploy.
        seeded = seed_password()
        origin = (
            "the published default" if seeded == DEFAULT_PASSWORD
            else "your SEED_PASSWORD setting"
        )
        print(f"\nSign in with {STAFF[0][1]} and {origin} ({len(STAFF)} staff accounts).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
