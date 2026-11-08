"""Question-led reporting, using the existing scoped transaction reader."""
from collections import defaultdict
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from werkzeug.datastructures import MultiDict

from .report_explorer import ZERO, ReportError, build_report, choices, parse_filters, _decimal

MODES = {'recipient': 'Money sent to someone', 'overview': 'Income & spending', 'category': 'Spending by category'}


def filters(args):
    mode = args.get('mode', 'recipient')
    if mode not in MODES: raise ReportError('Choose a valid report.')
    today = date.today()
    start, end = args.get('from', date(today.year, 1, 1).isoformat()), args.get('to', today.isoformat())
    currency = args.get('currency', 'KRW')
    if currency not in ('KRW', 'BDT'): raise ReportError('Choose KRW or BDT.')
    account = args.get('account', '')
    category = args.get('category', '')
    recipient = args.get('recipient', '')
    if account and not account.isdigit(): raise ReportError('Invalid account.')
    if category and not category.isdigit(): raise ReportError('Invalid category.')
    status = args.get('status', 'all')
    if status not in ('all', 'completed', 'pending'): raise ReportError('Invalid status.')
    rate=args.get('rate','').strip() if mode=='recipient' else ''
    conversion_rate=_decimal(rate,'BDT per KRW') if rate else None
    if conversion_rate is not None and not Decimal('0.000001')<=conversion_rate<=Decimal('10000'):
        raise ReportError('Conversion rate must be between 0.000001 and 10,000 BDT per KRW.')
    raw = MultiDict({'from': start, 'to': end, 'currency': 'both' if mode == 'recipient' else currency,
                     'status': status, 'view': 'all', 'group_by': 'category'})
    if account: raw.add('accounts', account)
    if category and mode == 'category': raw.add('categories', category)
    return dict(mode=mode, **{'from':start,'to':end}, currency=currency, account=account,
                category=category, recipient=recipient, status=status, rate=rate, conversion_rate=conversion_rate, raw=parse_filters(raw))


def recipient_options(options):
    """Merge orphaned historical names only when exactly one current person matches."""
    current = defaultdict(list)
    for key, label in options['recipients']:
        if key.startswith('id:'): current[label.strip().casefold()].append(key)
    result, aliases = [], {}
    for key, label in options['recipients']:
        if key.startswith('name:') and len(current[key[5:]]) == 1:
            aliases.setdefault(current[key[5:]][0], set()).add(key)
        else: result.append((key, label))
    for key, _ in result: aliases.setdefault(key, set()).add(key)
    return result, aliases


def _single(values, issues, label):
    present = {v for v in values if v is not None}
    if len(present) > 1:
        issues.append('Conflicting ' + label)
        return None
    return next(iter(present)) if present else None


def _payment_event(records, kind):
    """Turn a remittance's two legs into one payment; local sends use the debit."""
    records = sorted(records, key=lambda r: (r['currency'] != 'KRW', r['amount'] >= 0, r['key']))
    row = records[0]
    issues = []
    ids = {r['recipient_key'] for r in records if r['recipient_id'] is not None}
    if len(ids) > 1: issues.append('Conflicting recipients')
    names={r['recipient'].strip().casefold() for r in records if r['recipient'] not in ('No recipient','Self')}
    if len(names)>1: issues.append('Conflicting recipient names')
    recipient_keys = {r['recipient_key'] for r in records}
    if kind == 'international':
        source = [r for r in records if r['type']=='transfer_international' and r['currency'] == 'KRW' and r['amount'] < 0]
        received = [r for r in records if r['type']=='transfer_international' and r['currency'] == 'BDT' and r['amount'] > 0]
        if not source and not received: return None
        if not row['transfer_group']: issues.append('Unlinked remittance')
        if len(source) > 1 or len(received) > 1: issues.append('Multiple transfer legs')
        sent = _single([r['sent_krw'] for r in records] + [-r['amount'] for r in source], issues, 'KRW amounts')
        bdt = _single([r['received_bdt'] for r in records] + [r['amount'] for r in received], issues, 'BDT amounts')
        if sent is None: issues.append('KRW amount not recorded')
        if bdt is None: issues.append('BDT amount not recorded')
        local_krw = local_bdt = ZERO
        abroad_krw, abroad_bdt = sent, bdt
        channel = 'KRW → BDT remittance'
    else:
        debits = [r for r in records if r['amount'] < 0]
        if not debits: return None
        if len(debits) != 1: issues.append('Multiple local debit records')
        row = debits[0]
        abroad_krw = abroad_bdt = ZERO
        local_krw = -row['amount'] if row['currency'] == 'KRW' else ZERO
        local_bdt = -row['amount'] if row['currency'] == 'BDT' else ZERO
        channel = 'Local ' + row['currency'] + ' payment'
    own = any(r['target'] == 'own' for r in records if kind!='international' or r['type']=='transfer_international')
    if own: return None
    label_row = next((r for r in records if r['recipient'] != 'No recipient'), row)
    if label_row['recipient'] == 'No recipient': issues.append('Recipient not recorded')
    # No exchange rate is invented. Any missing/conflicting component makes the
    # event reviewable, rather than inflating a complete-looking total.
    return dict(date=row['date'], recipient=label_row['recipient'], recipient_keys=recipient_keys,
                channel=channel, abroad_krw=abroad_krw, abroad_bdt=abroad_bdt,
                local_krw=local_krw, local_bdt=local_bdt,
                total_krw=abroad_krw + local_krw if abroad_krw is not None else None,
                total_bdt=abroad_bdt + local_bdt if abroad_bdt is not None else None,
                counted=not issues, issues=issues, records=records,
                note=' / '.join(dict.fromkeys(r['note'] for r in records if r['note'])),
                key=row['transfer_group'] or row['key'])


def payments(rows):
    intl = defaultdict(list)
    for r in rows:
        if r['type'] == 'transfer_international': intl[r['transfer_group'] or r['key']].append(r)
    for r in rows:
        if r['type']=='transfer_domestic' and r['transfer_group'] in intl:
            intl[r['transfer_group']].append(r)
    result = []
    for records in intl.values():
        event = _payment_event(records, 'international')
        if event: result.append(event)
    local = defaultdict(list)
    for r in rows:
        if r['type'] not in ('transfer_domestic', 'expense') or r['settlement']: continue
        if r['amount'] >= 0: continue
        # A BDT payout represented again under the same remittance group is
        # already counted in the remittance's recipient amount.
        if r['type'] == 'transfer_domestic' and r['transfer_group'] in intl: continue
        if r['recipient'] == 'No recipient': continue
        key = (r['currency'], r['transfer_group'] or r['key']) if r['type'] == 'transfer_domestic' else (r['currency'], r['key'])
        local[key].append(r)
    for records in local.values():
        event = _payment_event(records, 'local')
        if event: result.append(event)
    return sorted(result, key=lambda e: (e['date'], e['key']), reverse=True)


def _sum_events(events):
    good = [e for e in events if e['counted']]
    out = {k:sum((e[k] or ZERO for e in good), ZERO) for k in
           ('abroad_krw', 'abroad_bdt', 'local_krw', 'local_bdt', 'total_krw', 'total_bdt')}
    out.update(count=len(good), review=len(events)-len(good), international_count=sum(e['channel']=='KRW → BDT remittance' for e in good))
    return out


def _percentage(amount, maximum):
    return float(min(Decimal('100'), abs(amount) / maximum * 100)) if maximum else 0


def build_insights(uid, f):
    options = choices(uid)
    options['people'], aliases = recipient_options(options)
    raw = build_report(uid, f['raw'])
    rows = raw['rows']
    warnings = []
    if f['mode'] == 'recipient':
        events = payments(rows)
        if f['recipient']:
            if f['recipient'] not in aliases: raise ReportError('Choose a recipient from your list.')
            keys = aliases[f['recipient']]
            events = [e for e in events if e['recipient_keys'] & keys]
        totals = _sum_events(events)
        if f['conversion_rate'] is not None:
            rate=f['conversion_rate']
            totals['equivalent_bdt']=(totals['total_bdt']+totals['local_krw']*rate).quantize(Decimal('0.01'),rounding=ROUND_HALF_UP)
            totals['equivalent_krw']=(totals['total_bdt']/rate+totals['local_krw']).quantize(Decimal('0.01'),rounding=ROUND_HALF_UP)
        monthly = defaultdict(list)
        for e in events: monthly[e['date'][:7]].append(e)
        months = [dict(month=m, **_sum_events(es)) for m, es in sorted(monthly.items())]
        max_bdt = max((m['total_bdt'] for m in months), default=ZERO)
        for m in months:
            m['abroad_width'] = _percentage(m['abroad_bdt'], max_bdt)
            m['local_width'] = _percentage(m['local_bdt'], max_bdt)
        people = defaultdict(list)
        for event in events:
            # Current IDs distinguish people with the same name. Aliases for
            # unlinked historical name records merge only when unambiguous.
            identity = next((k for k in sorted(event['recipient_keys']) if k.startswith('id:')), None)
            if not identity:
                identity = next(iter(sorted(event['recipient_keys'])), 'missing')
                identity = next((k for k, names in aliases.items() if identity in names), identity)
            people[identity].append(event)
        labels=dict(options['people'])
        by_person = [dict(name=labels.get(identity,es[0]['recipient']), **_sum_events(es)) for identity,es in people.items()]
        by_person.sort(key=lambda p:p['total_bdt'],reverse=True)
        if totals['review']: warnings.append(f"{totals['review']} payment record(s) need review and are excluded from totals. Expand the payment to see why.")
        # Native totals mean source cost in KRW and recipient delivery in BDT.
        # They are two views of the same payment, not amounts to add together.
        return dict(mode='recipient', rows=rows, events=events, totals=totals, months=months,
                    categories=[], people=by_person, warnings=warnings, options=options,
                    title=dict(options['people']).get(f['recipient'], 'All recipients'),
                    count=len(events), record_count=sum(len(e['records']) for e in events))
    income, gross, refunds = ZERO, ZERO, ZERO
    monthly = defaultdict(lambda:dict(income=ZERO,spending=ZERO,refunds=ZERO))
    categories = defaultdict(lambda:dict(gross=ZERO,refunds=ZERO,count=0))
    financial = []
    for r in rows:
        if r['settlement']: continue
        is_income = r['type']=='income' and r['amount']>0
        is_spending = r['type'] in ('expense','fee') and r['amount']<0
        is_refund = r['type']=='refund' and r['amount']>0
        if f['mode']=='category' and is_income: continue
        if not (is_income or is_spending or is_refund): continue
        financial.append(r)
        m = monthly[r['date'][:7]]
        if is_income:
            income += r['amount'];m['income'] += r['amount']
        else:
            key = (r['category_id'], r['category'])
            cat = categories[key];cat['count'] += 1
            if is_spending:
                gross -= r['amount'];m['spending'] -= r['amount'];cat['gross'] -= r['amount']
            else:
                refunds += r['amount'];m['refunds'] += r['amount'];cat['refunds'] += r['amount']
    cats = [dict(name=name,net=c['gross']-c['refunds'],**c) for (_,name),c in categories.items()]
    cats.sort(key=lambda c:c['net'],reverse=True)
    largest=max((abs(c['net']) for c in cats),default=ZERO)
    for c in cats: c['width']=_percentage(c['net'],largest)
    months=[dict(month=m,net_spending=t['spending']-t['refunds'],**t) for m,t in sorted(monthly.items())]
    largest=max((max(m['income'],abs(m['net_spending'])) for m in months),default=ZERO)
    for m in months:
        m['income_width']=_percentage(m['income'],largest);m['spending_width']=_percentage(m['net_spending'],largest)
    totals=dict(income=income,gross=gross,refunds=refunds,spending=gross-refunds,balance=income-gross+refunds,
                count=len(financial),review=0)
    title=MODES[f['mode']]
    if f['mode']=='category' and f['category']:
        selected=next((c['name'] for c in options['categories'] if str(c['id'])==f['category']), 'Selected category')
        title='Spending: '+selected
    return dict(mode=f['mode'],rows=financial,events=[],totals=totals,months=months,categories=cats,people=[],
                warnings=warnings,options=options,title=title,count=len(financial),record_count=len(financial))
