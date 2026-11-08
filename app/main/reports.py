"""Reports routes and exports; all reads share one validated report definition."""
import csv
import json
from datetime import datetime
from io import BytesIO, StringIO
from math import ceil
from pathlib import Path
from xml.sax.saxutils import escape

from flask import Response, abort, current_app, render_template, request, url_for, make_response
from flask_login import current_user, login_required

from . import main
from ..services.report_explorer import (
    GROUPS, OPERATORS, RULE_FIELDS, VIEWS, ReportError, build_report, choices, parse_filters,
)

COLUMNS = [('date', 'Date'), ('currency', 'Currency'), ('type', 'Type'), ('account', 'Account'),
           ('recipient', 'Recipient'), ('category', 'Category'), ('amount', 'Signed amount'),
           ('status', 'Status'), ('target', 'Destination'), ('note', 'Note'),
           ('country', 'Recipient country'), ('method', 'Method'), ('service', 'Service'),
           ('sent_krw', 'Recorded KRW sent'), ('received_bdt', 'Recorded BDT received'),
           ('fee_amount', 'Recorded fee'), ('fee_currency', 'Fee currency'), ('fx_rate', 'Recorded FX rate'),
           ('account_type', 'Account type'), ('recipient_kind', 'Recipient type'),
           ('created_at', 'Created date'), ('updated_at', 'Updated date'),
           ('external_ref', 'External reference'), ('transfer_group', 'Transfer group'), ('key', 'Record ID')]
DEFAULT_COLUMNS = ['date', 'currency', 'type', 'account', 'recipient', 'category', 'amount', 'status', 'note']


def _columns():
    selected = request.args.getlist('columns') or DEFAULT_COLUMNS
    allowed = dict(COLUMNS)
    if len(selected) > len(allowed) or any(c not in allowed for c in selected):
        raise ReportError('Invalid report columns.')
    return [(k, allowed[k]) for k in dict.fromkeys(selected)]


def _links(endpoint, **changes):
    allowed = {'view','currency','from','to','status','deleted','direction','target',
               'accounts','account_types','categories','recipients','types','countries',
               'methods','services','children','q','min','max','rules','rule_mode',
               'group_by','then_by','sort','columns','page','group_page'}
    args = {k: request.args.getlist(k) for k in request.args if k in allowed}
    args.update(changes)
    args.pop('page', None) if 'page' not in changes else None
    return url_for(endpoint, **args)


def _filter_labels(f, options):
    labels = [('Report', VIEWS[f['view']]), ('Period', f"{f['from'] or 'All time'} to {f['to'] or 'All time'}"),
              ('Currency', f['currency']), ('Status', f['status']), ('Deleted', f['deleted']),
              ('Direction', f['direction']), ('Destination', f['target']),
              ('Group', GROUPS[f['group_by']] + (' / ' + GROUPS[f['then_by']] if f['then_by'] else ''))]
    for field, pool in [('accounts', {str(a.id): a.name for a in options['accounts']}),
                        ('categories', {str(c['id']): c['name'] for c in options['categories']}),
                        ('recipients', dict(options['recipients']))]:
        if f[field]: labels.append((field.title(), ', '.join(pool.get(x, 'Unavailable selection') for x in f[field])))
    for field in ('types', 'countries', 'services', 'methods', 'account_types'):
        if f[field]: labels.append((field.title(), ', '.join(f[field])))
    if f['categories']: labels.append(('Include child categories', str(f['children'])))
    if f['q']: labels.append(('Search', f['q']))
    if f['min'] or f['max']: labels.append(('Absolute amount', f"{f['min'] or '0'} to {f['max'] or 'No limit'} (per currency)"))
    if f['rules']:
        labels.append(('Advanced rules (' + f['rule_mode'] + ')', '; '.join(
            r['field'] + ' ' + r['op'] + ' ' + r.get('value', '') for r in f['rules'])))
    return labels


def _safe_csv(v):
    if isinstance(v, str) and v.lstrip().startswith(('=', '+', '-', '@')):
        return "'" + v
    return '' if v is None else v


def _csv(report, columns, labels):
    stream = StringIO()
    writer = csv.writer(stream)
    def write(values): writer.writerow([_safe_csv(v) for v in values])
    write(['Reports & Data Explorer'])
    for label, text in labels: write([label, text])
    write(['Currencies are separate. Recorded flows include transfer legs and external recipient records.'])
    write(['Spending excludes card settlements; remittance metadata is counted once per linked transfer.'])
    write([])
    write(['Currency', 'Records', 'Recorded inflow', 'Recorded outflow', 'Recorded net', 'Spending', 'Refunds', 'Net spending'])
    for cur, totals in report['totals'].items():
        write([cur] + [totals[k] for k in ['count', 'inflow', 'outflow', 'net', 'spending', 'refunds', 'net_spending']])
    rem = report['remittances']
    write([])
    write(['Remittance groups', rem['count'], 'Recorded KRW sent', rem['sent'], 'Recorded BDT received', rem['received']])
    write(['Unlinked remittance records', rem['unlinked'], 'Missing metadata fields', rem['missing'], 'Conflicting metadata fields', rem['conflicts']])
    for cur, amount in rem['fees'].items(): write(['Recorded fee metadata (not added to spending)', cur, amount])
    write([])
    write(['Grouped results'])
    write(['Currency', 'Group', 'Subgroup', 'Records', 'Recorded inflow', 'Recorded outflow', 'Net spending', 'Remitted KRW', 'Recipient BDT'])
    for group in report['groups']:
        write([group[k] for k in ('currency', 'name', 'secondary', 'count', 'inflow', 'outflow', 'net_spending')] +
              [group['remittances']['sent'] if group['currency']=='KRW' else '',
               group['remittances']['received'] if group['currency']=='BDT' else ''])
    write([])
    write(['All matching transactions'])
    write([title for _, title in columns])
    for row in report['rows']: write([row[key] for key, _ in columns])
    return ('\ufeff' + stream.getvalue()).encode('utf-8')


def _pdf(report, columns, labels):
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.platypus import LongTable, Paragraph, SimpleDocTemplate, Spacer
    except ImportError:
        raise ReportError('PDF export requires reportlab, already listed in your app requirements.')
    font = 'Helvetica'
    configured = current_app.config.get('REPORTS_PDF_FONT')
    paths = [Path(configured)] if configured else []
    paths += [Path(current_app.static_folder) / 'fonts' / 'NotoSans-Regular.ttf']
    for path in paths:
        if path.is_file():
            font = 'ExplorerFont'
            if font not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont(font, str(path)))
            break
    styles = getSampleStyleSheet()
    normal = ParagraphStyle('ExplorerBody', parent=styles['Normal'], fontName=font, fontSize=8, leading=11)
    heading = ParagraphStyle('ExplorerHeading', parent=normal, fontSize=12, leading=16, keepWithNext=True, spaceAfter=7)
    def p(text): return Paragraph(escape(str(text if text is not None else '')), normal)
    out = BytesIO()
    doc = SimpleDocTemplate(out, pagesize=landscape(A4), leftMargin=30, rightMargin=30, topMargin=35, bottomMargin=35)
    story = [Paragraph('Reports &amp; Data Explorer', heading)]
    for label, text in labels: story.append(p(label + ': ' + text))
    story.append(p('Currencies are separate. Recorded flows include transfer legs and external recipient records. Spending excludes card settlements.'))
    story.append(p('Remittance metadata is counted once per linked transfer; recorded fees are not added again to spending.'))
    def table(data, widths):
        tbl = LongTable([[p(x) for x in row] for row in data], colWidths=widths, repeatRows=1,
                        splitByRow=1, splitInRow=1)
        tbl.setStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e2e8f0')),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('GRID', (0, 0), (-1, -1), .3, colors.HexColor('#cbd5e1')),
            ('TOPPADDING', (0, 0), (-1, -1), 5), ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ])
        story.append(tbl)
    story.append(Spacer(0, 12))
    data = [['Currency', 'Records', 'Recorded inflow', 'Recorded outflow', 'Recorded net', 'Spending', 'Refunds', 'Net spending']]
    for cur, totals in report['totals'].items():
        data.append([cur] + [f'{totals[k]:,.2f}' if k != 'count' else totals[k] for k in ['count', 'inflow', 'outflow', 'net', 'spending', 'refunds', 'net_spending']])
    table(data, [doc.width*r for r in (.08, .06, .15, .15, .14, .14, .14, .14)])
    rem = report['remittances']
    story.append(Spacer(0, 10))
    story.append(p(f"Remittance groups: {rem['count']} | Recorded KRW sent: {rem['sent']:,.2f} | Recorded BDT received: {rem['received']:,.2f}"))
    story.append(p(f"Unlinked records: {rem['unlinked']} | Missing metadata fields: {rem['missing']} | Conflicting fields excluded: {rem['conflicts']}"))
    for cur, amount in rem['fees'].items(): story.append(p(f'Recorded fee metadata: {cur} {amount:,.2f}'))
    story.append(Spacer(0, 12))
    story.append(Paragraph('Grouped results', heading))
    data = [['Currency', 'Group / subgroup', 'Records', 'Recorded inflow', 'Recorded outflow', 'Net spending']]
    for group in report['groups']:
        data.append([group['currency'], group['name'] + (' / ' + group['secondary'] if group['secondary'] else '') +
                     (f" | Remitted KRW: {group['remittances']['sent']:,.2f}" if group['currency']=='KRW' and group['remittances']['count'] else
                      f" | Recipient BDT: {group['remittances']['received']:,.2f}" if group['currency']=='BDT' and group['remittances']['count'] else ''),
                     group['count'], f"{group['inflow']:,.2f}", f"{group['outflow']:,.2f}", f"{group['net_spending']:,.2f}"])
    table(data, [doc.width*r for r in (.08, .32, .08, .18, .18, .16)])
    story.append(Spacer(0, 12))
    story.append(Paragraph('All matching transactions', heading))
    # Selected extra fields appear within each transaction cell, keeping every
    # chosen value readable instead of shrinking 21 columns across a page.
    core = [(k, title) for k, title in columns if k in ('date', 'currency', 'amount')]
    extra = [(k, title) for k, title in columns if k not in ('date', 'currency', 'amount')]
    headings = [title for _, title in core] + (['Details'] if extra else [])
    data = [headings]
    for row in report['rows']:
        cells = [str(row[k]) if row[k] is not None else '' for k, _ in core]
        if extra:
            cells.append('\n'.join(title + ': ' + str(row[k] if row[k] is not None else '') for k, title in extra))
        data.append(cells)
    widths = ([doc.width*.12]*len(core) + [doc.width*(1-.12*len(core))]) if extra else [doc.width/len(core)]*len(core)
    # Preserve line breaks while escaping note text (Paragraph recognizes <br/>).
    def p(text):
        return Paragraph(escape(str(text if text is not None else '')).replace('\n', '<br/>'), normal)
    table(data, widths)
    generated = datetime.now().strftime('%Y-%m-%d %H:%M')
    def footer(canvas, _doc):
        canvas.setFont(font, 8)
        canvas.drawString(30, 19, generated)
        canvas.drawRightString(landscape(A4)[0]-30, 19, f'Page {canvas.getPageNumber()}')
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return out.getvalue()


@main.get('/reports/explorer', endpoint='explorer_page')
@login_required
def explorer_page():
    options = choices(current_user.id)
    error = None
    try:
        f = parse_filters(request.args)
        columns = _columns()
        report = build_report(current_user.id, f)
    except ReportError as exc:
        error = str(exc)
        # Keep the submitted values visible; no fallback query or false totals.
        f = request.args.to_dict()
        for key in ('accounts', 'categories', 'recipients', 'types', 'countries', 'services', 'methods', 'account_types'):
            f[key] = request.args.getlist(key)
        f['rules'] = []
        columns, report = [(k, dict(COLUMNS)[k]) for k in DEFAULT_COLUMNS], None
    page = max(1, request.args.get('page', 1, type=int))
    pages = max(1, ceil(report['count']/25)) if report else 1
    page = min(page, pages)
    gp = max(1, request.args.get('group_page', 1, type=int))
    group_pages = max(1, ceil(len(report['groups'])/10)) if report else 1
    gp = min(gp, group_pages)
    response = make_response(render_template('report_explorer.html', page_title='Reports & Data Explorer', page_slug='reports',
        f=f, error=error, report=report, options=options, views=VIEWS, group_options=GROUPS,
        columns=columns, all_columns=COLUMNS, rule_fields=RULE_FIELDS, operators=sorted(OPERATORS),
        rules_json=json.dumps(f.get('rules', [])), page=page, pages=pages, group_page=gp, group_pages=group_pages,
        rows=report['rows'][(page-1)*25:page*25] if report else [],
        groups=report['groups'][(gp-1)*10:gp*10] if report else [],
        link=_links, filter_labels=_filter_labels(f, options) if report else []))
    response.headers['Cache-Control'] = 'private, no-store'
    return response


@main.get('/reports/explorer/export/<fmt>', endpoint='explorer_export')
@login_required
def explorer_export(fmt):
    if fmt not in ('csv', 'pdf'): abort(404)
    try:
        f = parse_filters(request.args)
        columns = _columns()
        report = build_report(current_user.id, f)
        labels = _filter_labels(f, choices(current_user.id))
        payload = _csv(report, columns, labels) if fmt == 'csv' else _pdf(report, columns, labels)
    except ReportError as exc:
        return Response(str(exc), status=400, mimetype='text/plain')
    response = Response(payload, mimetype='text/csv' if fmt == 'csv' else 'application/pdf',
                        headers={'Content-Disposition': f'attachment; filename="expense-report.{fmt}"'})
    response.headers['Cache-Control'] = 'private, no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response

# The default page answers common money questions. The full explorer remains at
# /reports/explorer with its original filtering/export functionality.
from datetime import date as calendar_date
from ..services.report_insights import filters as insight_filters, build_insights, recipient_options
from ..services.report_insight_exports import csv_export as insight_csv, pdf_export as insight_pdf


def _simple_link(**changes):
    allowed={'mode','recipient','category','currency','from','to','account','status','page','rate'}
    params={k:request.args.get(k) for k in allowed if k in request.args}
    params.update(changes)
    return url_for('main.reports_page',**params)


def _simple_export(fmt):
    allowed={'mode','recipient','category','currency','from','to','account','status','rate'}
    params={k:request.args.get(k) for k in allowed if k in request.args}
    return url_for('main.reports_export',fmt=fmt,**params)


@main.get('/reports',endpoint='reports_page')
@login_required
def reports_page():
    error=None;insight=None
    try:
        f=insight_filters(request.args)
        insight=build_insights(current_user.id,f)
        options=insight['options']
    except ReportError as exc:
        error=str(exc)
        f=dict(mode=request.args.get('mode','recipient'),recipient=request.args.get('recipient',''),
               category=request.args.get('category',''),currency=request.args.get('currency','KRW'),
               account=request.args.get('account',''),status=request.args.get('status','all'),rate=request.args.get('rate',''),
               **{'from':request.args.get('from',''),'to':request.args.get('to','')})
        options=choices(current_user.id);options['people']=recipient_options(options)[0]
    today=calendar_date.today()
    presets=dict(month={'from':today.replace(day=1).isoformat(),'to':today.isoformat(),'page':1},
                 year={'from':today.replace(month=1,day=1).isoformat(),'to':today.isoformat(),'page':1},
                 all={'from':'','to':'','page':1})
    count=insight['count'] if insight else 0
    pages=max(1,ceil(count/20));page=min(max(1,request.args.get('page',1,type=int)),pages)
    response=make_response(render_template('reports.html',page_title='My Money Reports',page_slug='reports',
        f=f,insight=insight,options=options,error=error,presets=presets,page=page,pages=pages,
        events=insight['events'][(page-1)*20:page*20] if insight else [],
        rows=insight['rows'][(page-1)*20:page*20] if insight else [],
        simple_link=_simple_link,simple_export=_simple_export))
    response.headers['Cache-Control']='private, no-store'
    return response


@main.get('/reports/export/<fmt>',endpoint='reports_export')
@login_required
def reports_export(fmt):
    if fmt not in ('csv','pdf'):abort(404)
    try:
        f=insight_filters(request.args);insight=build_insights(current_user.id,f)
        payload=insight_csv(insight,f) if fmt=='csv' else insight_pdf(insight,f)
    except ReportError as exc:
        return Response(str(exc),status=400,mimetype='text/plain')
    return Response(payload,mimetype='text/csv' if fmt=='csv' else 'application/pdf',headers={
        'Content-Disposition':f'attachment; filename="my-money-report.{fmt}"',
        'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})
