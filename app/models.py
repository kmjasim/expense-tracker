from __future__ import annotations
from datetime import datetime
from enum import Enum

from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

from sqlalchemy import func, text, select, and_,Index, UniqueConstraint, ForeignKey
from sqlalchemy.orm import relationship, declared_attr, column_property
from .extensions import db
from sqlalchemy.dialects.postgresql import JSONB

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


# models.py
from enum import Enum

class BudgetType(db.Model):
    __tablename__ = "budgets_type"
    __table_args__ = (
        db.UniqueConstraint("user_id", "year", "month", "txn_type",
                            name="uq_btype_user_year_month_type"),
        {"sqlite_autoincrement": True},
    )

    id       = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id  = db.Column(db.Integer, db.ForeignKey("users.id"), index=True, nullable=False)
    year     = db.Column(db.Integer, nullable=False)
    month    = db.Column(db.Integer, nullable=False)
    # store the TxnType enum name (e.g., "transfer_international")
    txn_type = db.Column(db.Enum(TxnType, name="txn_type_enum"), nullable=False)
    amount   = db.Column(db.Numeric(14, 2), nullable=False, default=0)

    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


# --- Salary Tracker Models ---

from datetime import date

class SalarySettings(db.Model):
    __tablename__ = "salary_settings"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, index=True, nullable=False)

    # Defaults based on your numbers
    base_salary = db.Column(db.Numeric(12, 0), nullable=False, default=2096270)  # KRW
    hourly_rate = db.Column(db.Numeric(12, 2), nullable=False, default=10030)   # KRW per hour
    hours_per_day = db.Column(db.Numeric(5, 2), nullable=False, default=8)
    overtime_multiplier = db.Column(db.Numeric(4, 2), nullable=False, default=1.50)
    default_lunch_minutes = db.Column(db.Integer, nullable=False, default=0)

    effective_from = db.Column(db.Date, nullable=False, default=date.today)
    created_at = db.Column(db.Date, nullable=False, default=date.today)


class WorkLog(db.Model):
    __tablename__ = "work_logs"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, index=True, nullable=False)
    work_date = db.Column(db.Date, nullable=False, index=True)

    in_time = db.Column(db.Time, nullable=True)
    out_time = db.Column(db.Time, nullable=True)
    lunch_minutes = db.Column(db.Integer, nullable=False, default=0)

    # Weekly penalty applies ONLY if full-day leave
    is_full_day_leave = db.Column(db.Boolean, nullable=False, default=False)

    note = db.Column(db.String(255), default="")

    # store computed minutes (fast summary and consistent)
    worked_minutes = db.Column(db.Integer, nullable=False, default=0)
    regular_minutes = db.Column(db.Integer, nullable=False, default=0)
    overtime_minutes = db.Column(db.Integer, nullable=False, default=0)

    __table_args__ = (
        db.UniqueConstraint("user_id", "work_date", name="uq_worklog_user_date"),
    )


class Holiday(db.Model):
    __tablename__ = "holidays"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, index=True, nullable=False)

    holiday_date = db.Column(db.Date, nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False, default="")
    kind = db.Column(db.String(20), nullable=False, default="public")  # 'public' | 'company'
    year = db.Column(db.Integer, nullable=False, index=True)

    __table_args__ = (
        db.UniqueConstraint("user_id", "holiday_date", name="uq_holiday_user_date"),
    )


class SalaryAdjust(db.Model):
    """
    Monthly adjustments lines:
      - allowance (positive)
      - deduction (negative)
    Examples: Health insurance, Tax, Meal allowance, etc.
    """
    __tablename__ = "salary_adjustments"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, index=True, nullable=False)

    year = db.Column(db.Integer, nullable=False, index=True)
    month = db.Column(db.Integer, nullable=False, index=True)

    kind = db.Column(db.String(20), nullable=False)   # 'allowance' | 'deduction'
    label = db.Column(db.String(60), nullable=False)
    amount = db.Column(db.Numeric(12, 0), nullable=False, default=0)

    created_at = db.Column(db.Date, nullable=False, default=date.today)

    __table_args__ = (
        db.Index("ix_salary_adjust_user_month", "user_id", "year", "month"),
    )


# app/models_lotto.py (or paste into your existing app/models.py)




class LottoGame(db.Model):
    __tablename__ = "lotto_game"

    id = db.Column(db.BigInteger, primary_key=True)
    name = db.Column(db.Text, nullable=False, unique=True)

    numbers_per_draw = db.Column(db.SmallInteger, nullable=False, default=6)
    min_num = db.Column(db.SmallInteger, nullable=False, default=1)
    max_num = db.Column(db.SmallInteger, nullable=False, default=45)
    has_bonus = db.Column(db.Boolean, nullable=False, default=True)
    low_high_split = db.Column(db.SmallInteger, nullable=False, default=22)  # 1..22 low, 23..45 high

    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=datetime.utcnow)

    draws = db.relationship(
        "LottoDraw",
        backref="game",
        lazy=True,
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class LottoDraw(db.Model):
    __tablename__ = "lotto_draw"
    __table_args__ = (
        UniqueConstraint("game_id", "round_no", name="uq_lotto_draw_game_round"),
        Index("idx_lotto_draw_game_date", "game_id", "draw_date"),
        Index("idx_lotto_draw_game_round", "game_id", "round_no"),
    )

    id = db.Column(db.BigInteger, primary_key=True)

    game_id = db.Column(
        db.BigInteger,
        ForeignKey("lotto_game.id", ondelete="CASCADE"),
        nullable=False,
    )

    round_no = db.Column(db.Integer, nullable=False)
    draw_date = db.Column(db.Date, nullable=False)

    # keep bonus here too (convenient), and also store as normalized row in lotto_draw_number
    bonus = db.Column(db.SmallInteger, nullable=True)

    source = db.Column(db.Text, nullable=False, default="manual")
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=datetime.utcnow)

    numbers = db.relationship(
        "LottoDrawNumber",
        backref="draw",
        lazy=True,
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    stats = db.relationship(
        "LottoDrawStats",
        backref="draw",
        uselist=False,
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class LottoDrawNumber(db.Model):
    __tablename__ = "lotto_draw_number"
    __table_args__ = (
        # Prevent duplicate numbers within the same draw (also prevents bonus = main number)
        UniqueConstraint("draw_id", "num", name="uq_draw_num"),
        Index("idx_lotto_draw_number_num", "num"),
        Index("idx_lotto_draw_number_draw", "draw_id"),
        Index("idx_lotto_draw_number_num_draw", "num", "draw_id"),
        # NOTE: do NOT use a normal unique constraint on (draw_id, position) here,
        # because we want it to apply only to main numbers (bonus has position NULL).
        # We'll enforce main-position uniqueness with a partial unique index in SQL:
        #   CREATE UNIQUE INDEX uq_draw_position_main
        #   ON lotto_draw_number(draw_id, position)
        #   WHERE is_bonus = FALSE;
    )

    id = db.Column(db.BigInteger, primary_key=True)

    draw_id = db.Column(
        db.BigInteger,
        ForeignKey("lotto_draw.id", ondelete="CASCADE"),
        nullable=False,
    )

    num = db.Column(db.SmallInteger, nullable=False)

    # For main numbers: 1..6, For bonus: NULL
    position = db.Column(db.SmallInteger, nullable=True)

    # Bonus row: True, main rows: False
    is_bonus = db.Column(db.Boolean, nullable=False, default=False)


class LottoDrawStats(db.Model):
    __tablename__ = "lotto_draw_stats"
    __table_args__ = (
        Index("idx_lotto_draw_stats_sum", "sum_total"),
    )

    draw_id = db.Column(
        db.BigInteger,
        ForeignKey("lotto_draw.id", ondelete="CASCADE"),
        primary_key=True,
    )

    sum_total = db.Column(db.Integer, nullable=False)

    odd_count = db.Column(db.SmallInteger, nullable=False)
    even_count = db.Column(db.SmallInteger, nullable=False)

    low_count = db.Column(db.SmallInteger, nullable=False)
    high_count = db.Column(db.SmallInteger, nullable=False)

    range_span = db.Column(db.SmallInteger, nullable=False)

    avg_gap = db.Column(db.Numeric(6, 3), nullable=False)
    min_gap = db.Column(db.SmallInteger, nullable=False)
    max_gap = db.Column(db.SmallInteger, nullable=False)

    consecutive_pairs_count = db.Column(db.SmallInteger, nullable=False)
    max_consecutive_run = db.Column(db.SmallInteger, nullable=False)

    repeat_from_prev1 = db.Column(db.SmallInteger, nullable=True)
    repeat_from_prev2 = db.Column(db.SmallInteger, nullable=True)

    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=datetime.utcnow)


class LottoPickSet(db.Model):
    __tablename__ = "lotto_pick_set"
    __table_args__ = (
        Index("idx_lotto_pick_set_game_generated", "game_id", "generated_at"),
        Index("idx_lotto_pick_set_game_round", "game_id", "generated_for_round_no"),
        # NOTE: create the GIN index for JSONB in SQL (recommended):
        #   CREATE INDEX IF NOT EXISTS idx_lotto_pick_set_numbers_gin
        #   ON lotto_pick_set USING GIN (numbers);
    )

    id = db.Column(db.BigInteger, primary_key=True)

    game_id = db.Column(
        db.BigInteger,
        ForeignKey("lotto_game.id", ondelete="CASCADE"),
        nullable=False,
    )

    generated_for_round_no = db.Column(db.Integer, nullable=True)
    generated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=datetime.utcnow)

    method = db.Column(db.Text, nullable=False)

    # JSON array of 6 numbers, sorted (e.g. [3,11,18,27,33,41])
    numbers = db.Column(JSONB, nullable=False)

    rules_snapshot = db.Column(JSONB, nullable=True)
    score = db.Column(db.Numeric(10, 4), nullable=True)
    notes = db.Column(JSONB, nullable=True)

    # Backtest results
    result_round_no = db.Column(db.Integer, nullable=True)
    matched_main_count = db.Column(db.SmallInteger, nullable=True)
    matched_bonus = db.Column(db.Boolean, nullable=True)

    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=datetime.utcnow)
# --- Alembic migration snippet ---
    # ### commands auto generated by Alembic - please adjust! ###
