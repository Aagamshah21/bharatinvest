#!/usr/bin/env python3
"""
purge_starter_holdings.py

One-time cleanup script for BharatInvest broker:
- Lists users who have holdings in the database but NO executed trades backing them.
- Shows those unbacked holdings.
- With the --confirm flag: deletes those unbacked holdings and fires a HOLDINGS_CHANGED
  outbox event for each affected user in the same transaction.
- Real executed trades are NEVER touched.
"""

import sys
import os
import argparse

# Ensure project root is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal
from app.models import User
from app.services.holdings_service import find_unbacked_holdings, purge_unbacked_holdings

def main():
    parser = argparse.ArgumentParser(
        description="List and purge unbacked starter/seeded holdings for users without executed trades."
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Actually delete unbacked holdings and enqueue HOLDINGS_CHANGED outbox events. Defaults to dry-run."
    )
    parser.add_argument(
        "--email",
        type=str,
        default=None,
        help="Filter by specific user email (optional)."
    )
    parser.add_argument(
        "--user-id",
        type=int,
        default=None,
        help="Filter by specific user ID (optional)."
    )

    args = parser.parse_args()

    db = SessionLocal()
    try:
        query = db.query(User)
        if args.email:
            query = query.filter(User.email == args.email.strip().lower())
        if args.user_id:
            query = query.filter(User.id == args.user_id)

        users = query.all()
        if not users:
            print("No matching users found.")
            return

        print(f"\n=======================================================")
        print(f" BharatInvest: Starter Holdings Audit & Purge Tool")
        print(f" Mode: {'LIVE PURGE (--confirm)' if args.confirm else 'DRY RUN (Use --confirm to execute)'}")
        print(f" Users scanned: {len(users)}")
        print(f"=======================================================\n")

        total_unbacked_count = 0
        affected_users_count = 0

        for user in users:
            unbacked = find_unbacked_holdings(db, user)
            if not unbacked:
                continue

            affected_users_count += 1
            total_unbacked_count += len(unbacked)

            print(f"User #{user.id}: {user.full_name} <{user.email}> ({user.client_code})")
            print(f"  Found {len(unbacked)} unbacked holding(s):")
            for h in unbacked:
                sym = h.instrument.symbol if h.instrument else f"Inst#{h.instrument_id}"
                name = h.instrument.name if h.instrument else "Unknown"
                print(f"    - {sym:12} | Qty: {h.quantity:4} | Avg Price: Rs. {h.average_price:8.2f} | ({name})")

            if args.confirm:
                purged = purge_unbacked_holdings(db, user)
                print(f"  ==> [PURGED] Deleted {purged} unbacked holdings and enqueued HOLDINGS_CHANGED outbox event.\n")
            else:
                print(f"  ==> [DRY RUN] Will be deleted when run with --confirm.\n")

        print("-------------------------------------------------------")
        if affected_users_count == 0:
            print("Clean state: No unbacked or fake starter holdings found!")
        else:
            if args.confirm:
                print(f"Success: Purged {total_unbacked_count} unbacked holdings across {affected_users_count} users.")
                print("HOLDINGS_CHANGED outbox events enqueued for all affected users.")
            else:
                print(f"Audit Result: Found {total_unbacked_count} unbacked holdings across {affected_users_count} users.")
                print("Run with '--confirm' to perform deletion and fire outbox events.")
        print("Note: Real executed trades and orders were NEVER touched.")
        print("-------------------------------------------------------\n")

    finally:
        db.close()

if __name__ == "__main__":
    main()
