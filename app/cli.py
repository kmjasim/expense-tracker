# app/cli.py
from decimal import Decimal
from datetime import date
from .extensions import db
from .models import (
    Account, Category, Recipient,
    TransactionKRW, TransactionBDT,
    Currency, AccountType, RecipientType, Method, TxnType,
)

def register_cli(app):
    @app.cli.command("seed-min")
    def seed_min():
        """Dev-only: seed a couple of accounts, categories, recipient, and a few txns."""
        USER_ID = 1  # <- change to your dev user id

        def get_or_create(model, defaults=None, **filters):
            obj = db.session.query(model).filter_by(**filters).one_or_none()
            if obj:
                return obj
            params = {**filters, **(defaults or {})}
            obj = model(**params)
            db.session.add(obj)
            return obj

        # Accounts
        a1 = get_or_create(Account, user_id=USER_ID, name="KB Savings",
                           defaults=dict(currency=Currency.KRW, type=AccountType.bank,
                                         initial_balance=Decimal("0"), is_active=True))
        a2 = get_or_create(Account, user_id=USER_ID, name="bKash Wallet",
                           defaults=dict(currency=Currency.BDT, type=AccountType.mobile_wallet,
                                         initial_balance=Decimal("0"), is_active=True))
        db.session.flush()

        # Categories
        internet = get_or_create(Category, name="Internet & Cable TV")
        get_or_create(Category, name="Comcast Xfinity", parent_id=internet.id)
        get_or_create(Category, name="AT&T Internet and Cable", parent_id=internet.id)

        transport = get_or_create(Category, name="Transport")
        bus  = get_or_create(Category, name="Bus", parent_id=transport.id)
        taxi = get_or_create(Category, name="Taxi", parent_id=transport.id)

        # Recipient
        r1 = get_or_create(Recipient, user_id=USER_ID, name="Alice Kim",
                           defaults=dict(type=RecipientType.person, country="KR", default_method=Method.bank))

        # Optional sample txns (comment these if you want 0.00 balances)
        if a1.id:
            db.session.add(TransactionKRW(user_id=USER_ID, account_id=a1.id, date=date.today(),
                                          type=TxnType.income,  amount=Decimal("2500000.00"), note="Salary"))
            db.session.add(TransactionKRW(user_id=USER_ID, account_id=a1.id, date=date.today(),
                                          type=TxnType.expense, amount=Decimal("-1500.00"), category_id=taxi.id,
                                          note="Taxi", recipient_id=r1.id))
        if a2.id:
            db.session.add(TransactionBDT(user_id=USER_ID, account_id=a2.id, date=date.today(),
                                          type=TxnType.income,  amount=Decimal("5000.00"), note="Top-up"))
            db.session.add(TransactionBDT(user_id=USER_ID, account_id=a2.id, date=date.today(),
                                          type=TxnType.expense, amount=Decimal("-1200.00"), category_id=bus.id,
                                          note="Bus fare"))

        db.session.commit()
        print("✅ Seeded minimal data.")
