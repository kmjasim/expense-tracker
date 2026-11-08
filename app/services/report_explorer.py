"""Read-only transaction reports. No schema changes or balance updates."""
from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation
import json

from sqlalchemy import and_

from ..extensions import db
from ..models import Account, Category, Recipient, TransactionKRW, TransactionBDT

MAX_ROWS = 15000
ZERO = Decimal('0')
MODELS = {'KRW': TransactionKRW, 'BDT': TransactionBDT}
VIEWS = {'all': 'All transactions', 'spending': 'Spending & refunds',
         'income': 'Income', 'remittances': 'International remittances',
         'self': 'Own-account transfers', 'external': 'Transfers to other people',
         'settlements': 'Card settlements', 'refunds': 'Refunds'}
GROUPS = {'recipient': 'Recipient', 'category': 'Category', 'account': 'Account',
          'month': 'Month', 'year': 'Year', 'type': 'Transaction type',
          'country': 'Recipient country', 'service': 'Service', 'method': 'Method',
          'account_type': 'Account type', 'recipient_kind': 'Recipient type',
          'target': 'Transfer destination'}
NUMERIC_FIELDS = {'amount', 'absolute_amount', 'fee_amount', 'fx_rate', 'sent_krw', 'received_bdt'}
TEXT_FIELDS = {'note', 'recipient', 'category', 'account', 'country', 'service',
               'method', 'external_ref', 'transfer_group', 'type', 'recipient_kind', 'account_type', 'currency', 'key'}
DATE_FIELDS = {'date', 'created_at', 'updated_at'}
RULE_FIELDS = sorted(NUMERIC_FIELDS | TEXT_FIELDS | DATE_FIELDS)
OPERATORS = {'eq', 'ne', 'contains', 'not_contains', 'gte', 'lte', 'gt', 'lt', 'empty', 'not_empty'}


class ReportError(ValueError):
    pass


def value(v):
    return getattr(v, 'value', v) or ''


def money(v):
    return Decimal(str(v or 0))


def _choice(args, key, allowed, default):
    result = args.get(key, default)
    if result not in allowed:
        raise ReportError('Invalid ' + key.replace('_', ' ') + '.')
    return result


def _decimal(text, label):
    try:
        result = Decimal(text)
        if not result.is_finite() or abs(result) > Decimal('999999999999.99'):
            raise InvalidOperation
        return result
    except (InvalidOperation, ValueError, TypeError):
        raise ReportError(label + ' must be a finite number.')


def parse_filters(args):
    f = dict(view=_choice(args, 'view', VIEWS, 'all'),
             currency=_choice(args, 'currency', ['both', 'KRW', 'BDT'], 'both'),
             status=_choice(args, 'status', ['all', 'completed', 'pending'], 'all'),
             deleted=_choice(args, 'deleted', ['exclude', 'include', 'only'], 'exclude'),
             direction=_choice(args, 'direction', ['all', 'in', 'out'], 'all'),
             target=_choice(args, 'target', ['all', 'own', 'other', 'unknown'], 'all'),
             group_by=_choice(args, 'group_by', GROUPS, 'recipient'),
             then_by=_choice(args, 'then_by', ['', *GROUPS], ''),
             sort=_choice(args, 'sort', ['newest', 'oldest', 'largest', 'smallest'], 'newest'),
             rule_mode=_choice(args, 'rule_mode', ['all', 'any'], 'all'))
    today = date.today()
    f['from'] = args.get('from', today.replace(day=1).isoformat())
    f['to'] = args.get('to', today.isoformat())
    for key in ('from', 'to'):
        try:
            f[key + '_date'] = date.fromisoformat(f[key]) if f[key] else None
        except ValueError:
            raise ReportError('Use a valid date for ' + key + '.')
    if f['from_date'] and f['to_date'] and f['from_date'] > f['to_date']:
        raise ReportError('From date must be before or equal to To date.')
    for key in ('accounts', 'categories', 'recipients', 'types', 'countries', 'methods', 'services', 'account_types'):
        f[key] = args.getlist(key)
        if len(f[key]) > 150:
            raise ReportError('Too many selected ' + key + '.')
    for key in ('accounts', 'categories'):
        if any(not x.isdigit() for x in f[key]):
            raise ReportError('Invalid ' + key + '.')
    f['children'] = args.get('children', '1') == '1'
    f['q'] = args.get('q', '').strip()[:300]
    for key in ('min', 'max'):
        f[key] = args.get(key, '').strip()
        f[key + '_value'] = _decimal(f[key], 'Amount') if f[key] else None
        if f[key + '_value'] is not None and f[key + '_value'] < 0:
            raise ReportError('Amount bounds use positive absolute amounts.')
    if f['min_value'] is not None and f['max_value'] is not None and f['min_value'] > f['max_value']:
        raise ReportError('Minimum amount exceeds maximum amount.')
    try:
        raw = args.get('rules', '[]')
        if len(raw) > 12000:
            raise ValueError
        rules = json.loads(raw)
        if not isinstance(rules, list) or len(rules) > 12:
            raise ValueError
        for rule in rules:
            if not isinstance(rule, dict) or rule.get('field') not in RULE_FIELDS or rule.get('op') not in OPERATORS:
                raise ValueError
            field, op = rule['field'], rule['op']
            text = rule.get('value', '')
            if not isinstance(text, str) or len(text) > 300:
                raise ValueError
            if field in NUMERIC_FIELDS and op in ('contains', 'not_contains'):
                raise ValueError
            if field in DATE_FIELDS and op not in ('eq', 'ne', 'gte', 'lte', 'gt', 'lt', 'empty', 'not_empty'):
                raise ValueError
            if field in TEXT_FIELDS and op not in ('eq', 'ne', 'contains', 'not_contains', 'empty', 'not_empty'):
                raise ValueError
            if op not in ('empty', 'not_empty'):
                if field in NUMERIC_FIELDS:
                    _decimal(text, field)
                elif field in DATE_FIELDS:
                    date.fromisoformat(text)
        f['rules'] = rules
    except (ValueError, TypeError, KeyError):
        raise ReportError('Invalid advanced rules. Use up to 12 rules with valid fields and values.')
    return f


def _base(uid, model, f):
    # Scope every joined table too; malformed cross-user links never expose names.
    q = db.session.query(model, Account, Recipient, Category).outerjoin(
        Account, and_(Account.id == model.account_id, Account.user_id == uid)).outerjoin(
        Recipient, and_(Recipient.id == model.recipient_id, Recipient.user_id == uid)).outerjoin(
        Category, and_(Category.id == model.category_id, Category.user_id == uid)).filter(model.user_id == uid)
    if f['accounts']:
        q = q.filter(model.account_id.in_([int(x) for x in f['accounts']]))
    if f['from_date']:
        q = q.filter(model.date >= f['from_date'])
    if f['to_date']:
        q = q.filter(model.date <= f['to_date'])
    if f['deleted'] != 'include':
        q = q.filter(model.is_deleted.is_(f['deleted'] == 'only'))
    if f['status'] != 'all':
        q = q.filter(model.is_pending.is_(f['status'] == 'pending'))
    return q


def _context(uid, candidates):
    """Look beyond row filters to recognize the other leg; never add it to results."""
    gids = sorted({t.transfer_group_id for _, records in candidates for t, *_ in records
                   if t.transfer_group_id and value(t.type).startswith('transfer_')})
    contexts = defaultdict(list)
    for start in range(0, len(gids), 400):
        for cur, model in MODELS.items():
            q = db.session.query(model, Account).outerjoin(
                Account, and_(Account.id == model.account_id, Account.user_id == uid)).filter(
                    model.user_id == uid, model.transfer_group_id.in_(gids[start:start + 400]),
                    model.is_deleted.is_(False))
            for t, account in q:
                contexts[t.transfer_group_id].append((cur, t, account))
    return contexts


def _destination(t, rec, companions):
    if not value(t.type).startswith('transfer_'):
        return ''
    positives = [(cur, r, a) for cur, r, a in companions if money(r.amount) > 0]
    if value(t.type) == 'transfer_international':
        bdt = [(r, a) for cur, r, a in positives if cur == 'BDT']
        if bdt:
            # This is the sentinel account created by transfers_international().
            if all(a and a.name == 'External (BDT)' and not a.is_active for r, a in bdt):
                return 'other'
            if any(a and not (a.name == 'External (BDT)' and not a.is_active) for r, a in bdt):
                return 'own'
    else:
        negative_accounts = {r.account_id for _, r, _ in companions if money(r.amount) < 0}
        if any(a and r.account_id not in negative_accounts for _, r, a in positives) and negative_accounts:
            return 'own'
    if rec and value(rec.type) == 'self' or (t.recipient_name or '').strip().casefold() in ('self', 'myself'):
        return 'own'
    if rec or (t.recipient_name or '').strip():
        return 'other'
    return 'unknown'


def _record(cur, t, account, rec, cat, contexts):
    companions = contexts.get(t.transfer_group_id, [])
    note = t.note or ''
    settlement = 'credit card settlement' in note.casefold() or (
        value(t.type) == 'refund' and note.casefold().startswith('settlement from '))
    # Include the paired card credit only as a settlement, never a purchase refund.
    target = _destination(t, rec, companions)
    recipient = (t.recipient_name or '').strip() or (rec.name if rec else '')
    if not recipient:
        recipient = 'Self' if target == 'own' else 'No recipient'
    r = dict(key=f'{cur}:{t.id}', id=t.id, currency=cur, date=t.date.isoformat(),
             type=value(t.type), amount=money(t.amount), absolute_amount=abs(money(t.amount)),
             account=account.name if account else 'Unknown account', account_id=t.account_id,
             account_type=value(account.type) if account else '',
             category=cat.name if cat else 'Uncategorized', category_id=t.category_id,
             recipient=recipient, recipient_id=rec.id if rec else None,
             recipient_kind=value(rec.type) if rec else '', country=(rec.country or '') if rec else '',
             note=note, method=value(t.method), service=t.service_name or '',
             fee_amount=money(t.fee_amount) if t.fee_amount is not None else None,
             fee_currency=value(t.fee_currency),
             sent_krw=money(t.amount_sent_krw) if t.amount_sent_krw is not None else None,
             received_bdt=money(t.amount_received_bdt) if t.amount_received_bdt is not None else None,
             fx_rate=money(t.fx_rate_used) if t.fx_rate_used is not None else None,
             external_ref=t.external_ref or '', transfer_group=t.transfer_group_id or '',
             pending=t.is_pending, deleted=t.is_deleted, settlement=settlement, target=target,
             created_at=t.created_at.date().isoformat() if t.created_at else '',
             updated_at=t.updated_at.date().isoformat() if t.updated_at else '')
    r['recipient_key'] = 'id:' + str(rec.id) if rec else 'name:' + recipient.casefold()
    r['status'] = ('Deleted / ' if t.is_deleted else '') + ('Pending' if t.is_pending else 'Completed')
    return r


def _rule(r, rule):
    field, op = rule['field'], rule['op']
    v = r.get(field)
    if op == 'empty':
        return v is None or v == ''
    if op == 'not_empty':
        return v is not None and v != ''
    if v is None or v == '':
        return False
    target = rule.get('value', '')
    if field in NUMERIC_FIELDS:
        target = _decimal(target, field)
    else:
        v, target = str(v).casefold(), target.casefold()
    if op == 'eq': return v == target
    if op == 'ne': return v != target
    if op == 'contains': return target in v
    if op == 'not_contains': return target not in v
    if op == 'gte': return v >= target
    if op == 'lte': return v <= target
    if op == 'gt': return v > target
    if op == 'lt': return v < target
    return False


def _matches(r, f, cats):
    view, typ = f['view'], r['type']
    if view == 'spending' and (typ not in ('expense', 'fee', 'refund') or r['settlement']): return False
    if view == 'income' and typ != 'income': return False
    if view == 'remittances' and typ != 'transfer_international': return False
    if view == 'self' and r['target'] != 'own': return False
    if view == 'external' and r['target'] != 'other': return False
    if view == 'settlements' and not r['settlement']: return False
    if view == 'refunds' and (typ != 'refund' or r['settlement']): return False
    if f['target'] != 'all' and r['target'] != f['target']: return False
    if f['direction'] == 'in' and r['amount'] <= 0: return False
    if f['direction'] == 'out' and r['amount'] >= 0: return False
    for k, field in [('accounts', 'account_id'), ('account_types', 'account_type'), ('categories', 'category_id'), ('types', 'type'),
                     ('recipients', 'recipient_key'), ('countries', 'country'), ('services', 'service'), ('methods', 'method')]:
        selected = cats if k == 'categories' else f[k]
        if selected and str(r[field]) not in selected: return False
    if f['min_value'] is not None and r['absolute_amount'] < f['min_value']: return False
    if f['max_value'] is not None and r['absolute_amount'] > f['max_value']: return False
    if f['q'] and f['q'].casefold() not in ' '.join(str(r[x]) for x in TEXT_FIELDS).casefold(): return False
    if f['rules']:
        checks = (_rule(r, rule) for rule in f['rules'])
        if not (all(checks) if f['rule_mode'] == 'all' else any(checks)): return False
    return True


def _totals(rows):
    out = {}
    for cur in MODELS:
        selected = [r for r in rows if r['currency'] == cur]
        if not selected: continue
        inflow = sum((r['amount'] for r in selected if r['amount'] > 0), ZERO)
        outflow = sum((-r['amount'] for r in selected if r['amount'] < 0), ZERO)
        spending = sum((-r['amount'] for r in selected if r['type'] in ('expense', 'fee') and not r['settlement']), ZERO)
        refunds = sum((r['amount'] for r in selected if r['type'] == 'refund' and not r['settlement']), ZERO)
        out[cur] = dict(count=len(selected), inflow=inflow, outflow=outflow, net=inflow-outflow,
                        spending=spending, refunds=refunds, net_spending=spending-refunds)
    return out


def _remittances(rows):
    # Metadata is duplicated on the KRW and BDT legs. Count each group once.
    groups = defaultdict(list)
    for r in rows:
        if r['type'] == 'transfer_international':
            groups[r['transfer_group'] or r['key']].append(r)
    sent, received, fees = ZERO, ZERO, defaultdict(lambda: ZERO)
    missing, conflicts, unlinked = 0, 0, 0
    for records in groups.values():
        if not records[0]['transfer_group']: unlinked += 1
        for field in ('sent_krw', 'received_bdt'):
            values = {r[field] for r in records if r[field] is not None}
            if len(values) > 1: conflicts += 1
            if not values: missing += 1
            # If metadata is contradictory, do not silently choose a total.
            if len(values) == 1:
                if field == 'sent_krw': sent += next(iter(values))
                else: received += next(iter(values))
        amounts = {(r['fee_currency'], r['fee_amount']) for r in records if r['fee_amount'] is not None}
        if len(amounts) == 1:
            cur, amt = next(iter(amounts))
            fees[cur or 'Unspecified'] += amt
        elif len(amounts) > 1: conflicts += 1
    return dict(count=len(groups), sent=sent, received=received, fees=dict(fees),
                missing=missing, conflicts=conflicts, unlinked=unlinked)


def build_report(uid, f):
    candidates, count = [], 0
    for cur, model in MODELS.items():
        if f['currency'] not in ('both', cur): continue
        q = _base(uid, model, f)
        count += q.count()
        if count > MAX_ROWS:
            raise ReportError(f'More than {MAX_ROWS:,} records in this period. Narrow the dates or currency; no records were truncated.')
        candidates.append((cur, q.order_by(model.date.desc(), model.id.desc()).all()))
    contexts = _context(uid, candidates)
    categories = db.session.query(Category).filter_by(user_id=uid).all()
    cats = set(f['categories'])
    if cats and f['children']:
        while True:
            expanded = cats | {str(c.id) for c in categories if str(c.parent_id) in cats}
            if expanded == cats: break
            cats = expanded
    rows = [_record(cur, *record, contexts) for cur, records in candidates for record in records]
    rows = [r for r in rows if _matches(r, f, cats)]
    if f['sort'] in ('newest', 'oldest'):
        rows.sort(key=lambda r: (r['date'], r['id'], r['currency']), reverse=f['sort'] == 'newest')
    else:
        rows.sort(key=lambda r: (r['absolute_amount'], r['date'], r['key']), reverse=f['sort'] == 'largest')
    grouped = defaultdict(list)
    for r in rows:
        def label(field):
            if field == 'month': return r['date'][:7]
            if field == 'year': return r['date'][:4]
            if field == 'target': return {'own': 'Own accounts', 'other': 'Other recipients', 'unknown': 'Unknown'}.get(r['target'], 'Not a transfer')
            return r[field] or 'Not recorded'
        primary = label(f['group_by'])
        # IDs keep two people/accounts with identical names separate.
        identity = r['recipient_key'] if f['group_by'] == 'recipient' else (
            r['account_id'] if f['group_by'] == 'account' else primary)
        secondary = label(f['then_by']) if f['then_by'] else ''
        grouped[(r['currency'], str(identity), primary, secondary)].append(r)
    groups = []
    for (cur, _, name, secondary), members in grouped.items():
        totals = _totals(members)[cur]
        groups.append(dict(currency=cur, name=name, secondary=secondary, rows=members, remittances=_remittances(members), **totals))
    groups.sort(key=lambda g: (g['currency'], -g['outflow'], g['name'], g['secondary']))
    return dict(rows=rows, groups=groups, totals=_totals(rows), remittances=_remittances(rows), count=len(rows))


def choices(uid):
    accounts = db.session.query(Account).filter_by(user_id=uid).order_by(Account.name).all()
    recipients = db.session.query(Recipient).filter_by(user_id=uid).order_by(Recipient.name).all()
    categories = db.session.query(Category).filter_by(user_id=uid).order_by(Category.name).all()
    snapshots, services = set(), set()
    for model in MODELS.values():
        snapshots.update(n.strip() for n, in db.session.query(model.recipient_name).filter(
            model.user_id == uid, model.recipient_id.is_(None), model.recipient_name.isnot(None)).distinct() if n and n.strip())
        services.update(n for n, in db.session.query(model.service_name).filter(
            model.user_id == uid, model.service_name.isnot(None)).distinct() if n)
    category_names = {c.id: c.name for c in categories}
    safe_categories = [dict(id=c.id, name=c.name, parent_name=category_names.get(c.parent_id, '')) for c in categories]
    return dict(accounts=accounts, categories=safe_categories,
                recipients=[('id:' + str(r.id), r.name) for r in recipients] +
                           [('name:' + n.casefold(), n + ' (historical name)') for n in sorted(snapshots)],
                countries=sorted({r.country for r in recipients if r.country}), services=sorted(services))
