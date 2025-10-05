import csv
from datetime import datetime
from decimal import Decimal, InvalidOperation

from app import create_app, db
from app.models import TransactionBDT, TxnType, Account

# ------------- CONFIG -------------
CSV_FILE   = "home_transactions.csv"   # recipient,date,description,amount
USER_ID    = 1
ACCOUNT_ID = 10                        # your real BDT account
DRY_RUN    = False                     # True = validate only
# ----------------------------------

app = create_app()
app.app_context().push()

# sanity check account exists
acct = db.session.get(Account, ACCOUNT_ID)
if not acct:
    raise SystemExit(f"[ERR] Account id {ACCOUNT_ID} not found.")

def parse_date(v: str):
    v = (v or "").strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(v, fmt).date()
        except Exception:
            continue
    raise ValueError(f"Unsupported date format: {v!r}")

def parse_amount(v: str) -> Decimal:
    try:
        return Decimal((v or "").replace(",", "").strip())
    except (InvalidOperation, AttributeError):
        raise ValueError(f"Invalid amount: {v!r}")

def looks_like_self(recipient: str) -> bool:
    r = (recipient or "").strip().lower()
    return r in {"self", "me", "myself"}

records = []
skipped = 0
total_in = Decimal("0")
total_out = Decimal("0")

with open(CSV_FILE, newline="", encoding="utf-8") as f:
    reader = csv.DictReader(f)
    expected = {"recipient", "date", "description", "amount"}
    if set(map(str.lower, reader.fieldnames or [])) != expected:
        print(f"[WARN] Expected headers: {expected}, got: {reader.fieldnames}")

    for i, row in enumerate(reader, start=1):
        try:
            recipient = (row.get("recipient") or "").strip()
            t_date    = parse_date(row["date"])
            note      = (row.get("description") or "").strip()
            amt       = parse_amount(row["amount"])

            # type is fixed
            t_type = TxnType.transfer_international

            # auto-sign:
            if looks_like_self(recipient):
                # money coming to you (treat as inflow)
                amt = abs(amt)
                total_in += amt
            else:
                # default: sending out internationally (outflow)
                amt = -abs(amt)
                total_out += abs(amt)

            tx = TransactionBDT(
                user_id=USER_ID,
                account_id=ACCOUNT_ID,
                type=t_type,
                date=t_date,
                note=note,
                amount=amt,
                recipient_name=recipient,
                # leave recipient_id NULL and all other fields empty
            )
            records.append(tx)

        except Exception as e:
            skipped += 1
            print(f"[WARN] Row {i} skipped: {e} | data={row}")

print(f"\n[READY] {len(records)} valid, {skipped} skipped.")
print(f"[SUMMARY] Inflows +{total_in}, Outflows -{total_out}, Net = {total_in - total_out}\n")

if DRY_RUN or not records:
    print("[DRY] Not inserting (DRY_RUN=True or no valid rows).")
else:
    db.session.bulk_save_objects(records)
    db.session.commit()
    print(f"[OK] Inserted {len(records)} rows into transactions_bdt (account_id={ACCOUNT_ID}).")
