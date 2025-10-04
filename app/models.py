from datetime import datetime
from enum import Enum

from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

from sqlalchemy import text, select, and_
from sqlalchemy.orm import relationship, declared_attr, column_property
from .extensions import db

# --------------------------
# Users
# --------------------------
class User(UserMixin, db.Model):
    __tablename__ = "users"
    __table_args__ = {"sqlite_autoincrement": True}

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    email = db.Column(db.String(255), unique=True, index=True, nullable=False)
    name = db.Column(db.String(120), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)


# --------------------------
# Enums (Python)
# --------------------------
class Currency(str, Enum):
    KRW = "KRW"
    BDT = "BDT"


class AccountType(str, Enum):
    bank = "bank"
    credit = "credit"
    cash = "cash"
    mobile_wallet = "mobile_wallet"


class TxnType(str, Enum):
    income = "income"
    expense = "expense"
    transfer_domestic = "transfer_domestic"
    transfer_international = "transfer_international"
    refund = "refund"
    fee = "fee"
    adjustment = "adjustment"


class RecipientType(str, Enum):
    person = "person"
    business = "business"
    self_ = "self"  # `self` reserved in Python, so use self_


class Method(str, Enum):
    bank = "bank"
    mobile_wallet = "mobile_wallet"
    cash = "cash"
    other = "other"


# --------------------------
# Accounts
# --------------------------
class Account(db.Model):
    __tablename__ = "accounts"
    __table_args__ = (
        db.Index("ix_accounts_user_currency_type", "user_id", "currency", "type"),
        db.Index("ix_accounts_user_name", "user_id", "name"),
        {"sqlite_autoincrement": True},
    )

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, nullable=False, index=True)
    name = db.Column(db.Text, nullable=False)
    display_order = db.Column(db.Integer, nullable=False, server_default=text("1000"))

    currency = db.Column(db.Enum(Currency, name="currency_enum", native_enum=False), nullable=False)
    type = db.Column(db.Enum(AccountType, name="account_type_enum", native_enum=False), nullable=False)

    initial_balance = db.Column(db.Numeric(14, 2), nullable=False, server_default=text("0"))
    credit_limit = db.Column(db.Numeric(14, 2), nullable=True)  # ✅ Only for credit cards
    is_active = db.Column(db.Boolean, nullable=False, server_default=text("true"))

    created_at = db.Column(db.DateTime(timezone=True), default=datetime.utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)


# --------------------------
# Recipients
# --------------------------
class Recipient(db.Model):
    __tablename__ = "recipients"
    __table_args__ = (
        db.Index("ix_recipients_user_name", "user_id", "name"),
        db.Index("ix_recipients_user_fav", "user_id", "is_favorite"),
        {"sqlite_autoincrement": True},
    )

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, nullable=False, index=True)
    name = db.Column(db.Text, nullable=False)

    type = db.Column(
        db.Enum(RecipientType, name="recipient_type_enum", native_enum=False),
        nullable=False,
        server_default="person",
    )
    country = db.Column(db.Text)  # 'KR', 'BD', etc.

    default_method = db.Column(db.Enum(Method, name="method_enum", native_enum=False))
    default_service_name = db.Column(db.Text)  # 'bKash', 'Nagad', 'Wise'
    default_account_no_masked = db.Column(db.Text)
    notes = db.Column(db.Text)

    is_favorite = db.Column(db.Boolean, nullable=False, server_default=text("false"))

    created_at = db.Column(db.DateTime(timezone=True), default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow
    )


# --------------------------
# Categories (hierarchical)
# --------------------------
class Category(db.Model):
    __tablename__ = "categories"
    __table_args__ = (
        # unique per user within the same parent
        db.UniqueConstraint("user_id", "parent_id", "name", name="uq_categories_user_parent_name"),
        {"sqlite_autoincrement": True},
    )

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), index=True, nullable=False)
    name = db.Column(db.String(120), nullable=False)
    parent_id = db.Column(db.Integer, db.ForeignKey("categories.id"), nullable=True)

    parent = db.relationship(
        "Category",
        remote_side=[id],
        backref=db.backref("children", cascade="all, delete-orphan", lazy="selectin"),
        lazy="joined",
    )

    # (optional) if you want a relationship back to User:
    # user = db.relationship("User", backref=db.backref("categories", lazy="dynamic"))



# --------------------------
# Transactions (Mixin)
# --------------------------
class _TxnBase:
    __abstract__ = True

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, nullable=False, index=True)
    account_id = db.Column(db.Integer, db.ForeignKey("accounts.id"), nullable=False)

    date = db.Column(db.Date, nullable=False)
    type = db.Column(db.Enum(TxnType, name="txn_type_enum", native_enum=False), nullable=False)
    amount = db.Column(db.Numeric(14, 2), nullable=False)  # negative for outflow, positive for inflow

    category_id = db.Column(db.Integer)  # optional FK to categories.id (keep nullable for flexibility)
    note = db.Column(db.Text)
    is_pending = db.Column(db.Boolean, nullable=False, server_default=text("false"))
    is_deleted = db.Column(db.Boolean, nullable=False, server_default=text("false"))

    # transfer/recipient fields
    recipient_id = db.Column(db.Integer, db.ForeignKey("recipients.id"))
    recipient_name = db.Column(db.Text)  # snapshot for historical readability

    # international-only metadata (nullable for domestic)
    method = db.Column(db.Enum(Method, name="method_enum", native_enum=False))
    service_name = db.Column(db.Text)  # 'bKash', 'Nagad', 'Wise', etc.
    amount_sent_krw = db.Column(db.Numeric(14, 2))
    amount_received_bdt = db.Column(db.Numeric(14, 2))
    fx_rate_used = db.Column(db.Numeric(12, 6))
    fee_amount = db.Column(db.Numeric(14, 2))
    fee_currency = db.Column(db.Enum(Currency, name="currency_enum", native_enum=False))

    # store UUID as text for SQLite portability
    transfer_group_id = db.Column(db.String(36))
    external_ref = db.Column(db.Text)

    created_at = db.Column(db.DateTime(timezone=True), default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow
    )
    @declared_attr
    def category_name(cls):
        # safe outer lookup of Category.name by (category_id, user_id)
        return column_property(
            select(Category.name)
            .where(and_(Category.id == cls.category_id,
                        Category.user_id == cls.user_id))
            .correlate_except(Category)
            .scalar_subquery()
        )
    # relationships in a mixin MUST use @declared_attr
    @declared_attr
    def account(cls):
        return relationship("Account", foreign_keys=[cls.account_id])

    @declared_attr
    def recipient(cls):
        return relationship("Recipient", foreign_keys=[cls.recipient_id])


# --------------------------
# Transactions tables
# --------------------------
class TransactionKRW(db.Model, _TxnBase):
    __tablename__ = "transactions_krw"
    __table_args__ = (
        db.Index("ix_t_krw_user_date", "user_id", "date"),
        db.Index("ix_t_krw_user_type_date", "user_id", "type", "date"),
        db.Index("ix_t_krw_user_recipient_date", "user_id", "recipient_id", "date"),
        db.Index("ix_t_krw_user_method_date", "user_id", "method", "date"),
        db.Index("ix_t_krw_user_category_date", "user_id", "category_id", "date"),
        {"sqlite_autoincrement": True},
    )


class TransactionBDT(db.Model, _TxnBase):
    __tablename__ = "transactions_bdt"
    __table_args__ = (
        db.Index("ix_t_bdt_user_date", "user_id", "date"),
        db.Index("ix_t_bdt_user_type_date", "user_id", "type", "date"),
        db.Index("ix_t_bdt_user_recipient_date", "user_id", "recipient_id", "date"),
        db.Index("ix_t_bdt_user_method_date", "user_id", "method", "date"),
        db.Index("ix_t_bdt_user_category_date", "user_id", "category_id", "date"),
        {"sqlite_autoincrement": True},
    )



class RecurringFrequency(str, Enum):
    daily = "daily"
    weekly = "weekly"
    monthly = "monthly"

class RecurringRule(db.Model):
    __tablename__ = "recurring_rules"
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)

    # ownership
    user_id = db.Column(db.Integer, nullable=False, index=True)

    # what to create
    account_id = db.Column(db.Integer, db.ForeignKey("accounts.id"), nullable=False)
    type = db.Column(db.Enum(TxnType, name="txn_type_enum", native_enum=False), nullable=False)  # reuse your enum
    amount = db.Column(db.Numeric(14, 2), nullable=False)  # POSITIVE input; sign applied by logic
    category_id = db.Column(db.Integer)  # nullable on purpose
    note = db.Column(db.Text)

    # schedule
    frequency = db.Column(db.Enum(RecurringFrequency, name="recurring_frequency_enum", native_enum=False), nullable=False)
    every_n = db.Column(db.Integer, nullable=False, server_default=text("1"))  # every 1 day/week/month by default
    start_date = db.Column(db.Date, nullable=False)        # first eligible run date (inclusive)
    next_run = db.Column(db.Date, nullable=False)          # computed; when to run next
    end_date = db.Column(db.Date)                          # optional hard stop

    weekday = db.Column(db.Integer)   # 0=Mon..6=Sun (use for weekly if you want to pin)
    day_of_month = db.Column(db.Integer)  # 1..31 (use for monthly pin; auto-clamped)
    is_enabled = db.Column(db.Boolean, nullable=False, server_default=text("true"))

    # bookkeeping
    last_run = db.Column(db.Date)  # last date we created a txn

    account = db.relationship("Account", backref="recurring_rules", lazy="joined")

# models.py

class Budget(db.Model):
    __tablename__ = "budgets"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)

    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey("categories.id"), nullable=False)

    year = db.Column(db.Integer, nullable=False)   # e.g. 2025
    month = db.Column(db.Integer, nullable=False)  # 1–12
    amount = db.Column(db.Numeric(14, 2), nullable=False, default=0)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationships
    user = db.relationship("User", backref="budgets", lazy=True)
    category = db.relationship("Category", backref="budgets", lazy=True)

    __table_args__ = (
        db.UniqueConstraint("user_id", "category_id", "year", "month", name="uq_budget_user_cat_month"),
    )


# --- Debt models (simple) ---

from enum import Enum

class DebtDirection(str, Enum):
    owe = "owe"          # you borrowed (you owe)
    lend = "lend"        # you lent (they owe you)

class DebtItem(db.Model):
    __tablename__ = "debt_items"
    __table_args__ = ({"sqlite_autoincrement": True},)

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, nullable=False, index=True)

    direction = db.Column(db.Enum(DebtDirection, name="debt_direction_enum", native_enum=False), nullable=False)
    currency = db.Column(db.Enum(Currency, name="currency_enum", native_enum=False), nullable=False)

    recipient_id = db.Column(db.Integer, db.ForeignKey("recipients.id"), nullable=False)
    recipient = db.relationship("Recipient", lazy="joined")

    original_principal = db.Column(db.Numeric(14, 2), nullable=False)
    outstanding_principal = db.Column(db.Numeric(14, 2), nullable=False)

    start_date = db.Column(db.Date, nullable=False)
    note = db.Column(db.Text)

    status = db.Column(db.String(20), nullable=False, server_default="active")  # active/settled
    created_at = db.Column(db.DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

class DebtTxn(db.Model):
    __tablename__ = "debt_txns"
    __table_args__ = (
        db.Index("ix_debt_txns_user_date", "user_id", "date"),
        {"sqlite_autoincrement": True},
    )

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, nullable=False, index=True)
    item_id = db.Column(db.Integer, db.ForeignKey("debt_items.id"), nullable=False)
    item = db.relationship("DebtItem", backref=db.backref("txns", lazy="dynamic"))

    # action: add principal (opening/top-up) OR repayment (principal/interest/fee)
    action = db.Column(db.String(20), nullable=False)  # "add" | "repayment"

    date = db.Column(db.Date, nullable=False)
    amount = db.Column(db.Numeric(14, 2), nullable=False)  # entered amount
    principal_portion = db.Column(db.Numeric(14, 2), nullable=False, server_default=text("0"))
    interest_portion = db.Column(db.Numeric(14, 2), nullable=False, server_default=text("0"))
    fee_portion = db.Column(db.Numeric(14, 2), nullable=False, server_default=text("0"))

    note = db.Column(db.Text)
    created_at = db.Column(db.DateTime(timezone=True), default=datetime.utcnow, nullable=False)
