"""Export the same question-led answers, charts and supporting records."""
import csv
from io import BytesIO, StringIO
from pathlib import Path
from xml.sax.saxutils import escape

from flask import current_app

from .report_explorer import ReportError

SUMMARY = {
    'recipient': [('abroad_krw','KRW sent abroad'),('abroad_bdt','BDT from remittances'),
                  ('local_krw','Local KRW payments'),('local_bdt','Local BDT payments'),
                  ('total_krw','Total KRW sent / paid'),('total_bdt','Total BDT delivered'),
                  ('count','Confirmed payments'),('review','Payments excluded for review')],
    'overview': [('income','Income'),('gross','Spending before refunds'),('refunds','Refunds'),
                 ('spending','Net spending'),('balance','Income minus spending')],
    'category': [('gross','Spending before refunds'),('refunds','Refunds'),('spending','Net spending')],
}
RAW = [('key','Record ID'),('date','Date'),('type','Type'),('currency','Currency'),('account','Account'),
       ('recipient','Recipient'),('category','Category'),('amount','Signed amount'),('status','Status'),('note','Note')]
PAYMENT = [('date','Date'),('recipient','Recipient'),('channel','Channel'),('abroad_krw','KRW abroad'),
           ('abroad_bdt','Remittance BDT'),('local_krw','Local KRW'),('local_bdt','Local BDT'),
           ('total_krw','Total KRW'),('total_bdt','Total BDT'),('counted','Included in totals'),('note','Note')]


def summary_items(insight,f):
    items=list(SUMMARY[f['mode']])
    if f['mode']=='recipient' and f['rate']:
        items.extend([('equivalent_bdt','Estimated total BDT equivalent'),('equivalent_krw','Estimated total KRW equivalent')])
    return items


def filter_labels(insight, f):
    options=insight['options']
    account=next((a.name for a in options['accounts'] if str(a.id)==f['account']), 'All accounts')
    category=next((c['name'] for c in options['categories'] if str(c['id'])==f['category']), 'All categories')
    rate_labels=[('Optional conversion','1 KRW = '+f['rate']+' BDT; equivalents are estimates')] if f['rate'] else []
    return rate_labels+[('Report',insight['title']),('From',f['from'] or 'All history'),('To',f['to'] or 'All history'),
            ('Currency','KRW source and BDT recipient totals' if f['mode']=='recipient' else f['currency']),
            ('Account',account),('Status',f['status'])]+([('Category',category+' (including subcategories)')] if f['mode']=='category' else [])


def source_rows(insight):
    if insight['mode']!='recipient':return insight['rows']
    unique={r['key']:r for e in insight['events'] for r in e['records']}
    return list(unique.values())


def csv_export(insight,f):
    buf=StringIO();writer=csv.writer(buf)
    def write(values):
        writer.writerow(["'"+v if isinstance(v,str) and v.lstrip().startswith(('=','+','-','@')) else '' if v is None else v for v in values])
    write(['My Money Report'])
    for pair in filter_labels(insight,f):write(pair)
    write([]);write(['Summary'])
    for key,label in summary_items(insight,f):write([label,insight['totals'][key]])
    for warning in insight['warnings']:write(['Review notice',warning])
    if f['mode']=='recipient':
        write(['KRW and BDT are separate perspectives on the same remittances, not values to add together.'])
        write([]);write(['Monthly payments'])
        monthly=[('month','Month'),('abroad_krw','KRW abroad'),('abroad_bdt','Remittance BDT'),
                 ('local_krw','Local KRW'),('local_bdt','Local BDT'),('total_krw','Total KRW'),('total_bdt','Total BDT')]
        write([label for _,label in monthly])
        for m in insight['months']:write([m[k] for k,_ in monthly])
        write([]);write(['Recipient breakdown'])
        write(['Recipient','Total KRW','Remittance BDT','Local BDT','Total BDT'])
        for p in insight['people']:write([p[k] for k in ('name','total_krw','abroad_bdt','local_bdt','total_bdt')])
        write([]);write(['Payments — one row per payment'])
        write([label for _,label in PAYMENT]+['Review reason'])
        for e in insight['events']:write([e[k] for k,_ in PAYMENT]+['; '.join(e['issues'])])
    else:
        write(['Transfers and card settlements are excluded from income and spending.'])
        write([]);write(['Month','Income','Spending before refunds','Refunds','Net spending'])
        for m in insight['months']:write([m[k] for k in ('month','income','spending','refunds','net_spending')])
        write([]);write(['Category','Spending before refunds','Refunds','Net spending','Records'])
        for c in insight['categories']:write([c[k] for k in ('name','gross','refunds','net','count')])
    write([]);write(['Supporting transactions — raw amounts are not an additional payment total'])
    write([label for _,label in RAW])
    for r in source_rows(insight):write([r[k] for k,_ in RAW])
    return ('\ufeff'+buf.getvalue()).encode('utf-8')


def pdf_export(insight, f):
    """Presentation only: all values come from the unchanged report definition."""
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_RIGHT
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.platypus import (
            SimpleDocTemplate, Paragraph, LongTable, Table, Spacer, CondPageBreak,
        )
        from reportlab.graphics.shapes import Drawing, Rect, String
    except ImportError:
        raise ReportError('PDF export requires reportlab, which is already in your app requirements.')

    font = 'Helvetica'
    configured = current_app.config.get('REPORTS_PDF_FONT')
    paths = ([Path(configured)] if configured else []) + [Path(current_app.static_folder) / 'fonts/NotoSans-Regular.ttf']
    for path in paths:
        if path.is_file():
            font = 'MoneyReportFont'
            if font not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont(font, str(path)))
            break
    bold = 'Helvetica-Bold' if font == 'Helvetica' else font
    ink, muted = colors.HexColor('#163443'), colors.HexColor('#657987')
    teal, blue, coral = [colors.HexColor(c) for c in ('#168c80', '#527eda', '#de8168')]
    line, pale = colors.HexColor('#dce6ea'), colors.HexColor('#f4f8fa')
    base = getSampleStyleSheet()['Normal']
    body = ParagraphStyle('ReportBody', parent=base, fontName=font, fontSize=8.3, leading=12, textColor=ink)
    small = ParagraphStyle('ReportSmall', parent=body, fontSize=7.7, leading=11, textColor=muted)
    right = ParagraphStyle('ReportAmount', parent=body, alignment=TA_RIGHT)
    table_head = ParagraphStyle('ReportTableHead', parent=small, fontName=bold, textColor=colors.white, fontSize=7.5, leading=10)
    head_right = ParagraphStyle('ReportTableHeadRight', parent=table_head, alignment=TA_RIGHT)
    section_style = ParagraphStyle('ReportSection', parent=body, fontName=bold, fontSize=13, leading=18, spaceBefore=18, spaceAfter=5, keepWithNext=True)
    section_note = ParagraphStyle('ReportSectionNote', parent=small, spaceAfter=10, keepWithNext=True)
    card_label = ParagraphStyle('ReportCardLabel', parent=small, fontName=bold, fontSize=8, leading=11)
    card_value = ParagraphStyle('ReportCardValue', parent=body, fontName=bold, fontSize=21, leading=28, spaceBefore=5, spaceAfter=3)
    card_note = ParagraphStyle('ReportCardNote', parent=small, fontSize=7.3, leading=10)
    light_label = ParagraphStyle('ReportLightLabel', parent=small, textColor=colors.HexColor('#add8d0'), fontSize=8, leading=11)
    light_title = ParagraphStyle('ReportLightTitle', parent=body, fontName=bold, fontSize=23, leading=29, textColor=colors.white, spaceBefore=4)
    light_note = ParagraphStyle('ReportLightNote', parent=small, textColor=colors.HexColor('#d2e4e4'), spaceBefore=5)
    def p(text, style=body):
        return Paragraph(escape(str(text if text is not None else '')).replace('\n', '<br/>'), style)
    def fmt(v):
        if v is None: return 'Not recorded'
        return f'{v:,.2f}' if not isinstance(v, (str, bool, int)) else str(v)
    title = insight['title']
    topic = {'recipient': 'RECIPIENT PAYMENTS', 'overview': 'INCOME & SPENDING', 'category': 'CATEGORY SPENDING'}[f['mode']]
    out = BytesIO()
    doc = SimpleDocTemplate(out, pagesize=landscape(A4), leftMargin=36, rightMargin=36, topMargin=38, bottomMargin=38,
                            title='My Money Report - ' + title, author='Expense Tracker', subject=topic)
    story = []
    period = (f['from'] or 'Start of history') + '  to  ' + (f['to'] or 'End of history')
    hero = Table([[[p('MY MONEY  /  ' + topic, light_label), p(title, light_title), p(period, light_note)]]], colWidths=[doc.width])
    hero.setStyle([('BACKGROUND', (0, 0), (-1, -1), ink), ('LEFTPADDING', (0, 0), (-1, -1), 20),
                   ('RIGHTPADDING', (0, 0), (-1, -1), 20), ('TOPPADDING', (0, 0), (-1, -1), 17), ('BOTTOMPADDING', (0, 0), (-1, -1), 17)])
    story.extend([hero, Spacer(0, 12)])
    labels = filter_labels(insight, f)
    generated = datetime.now(ZoneInfo('Asia/Seoul')).strftime('%d %b %Y, %H:%M KST')
    metadata = '  |  '.join(label + ': ' + text for label, text in labels if label not in ('Report', 'From', 'To', 'Optional conversion'))
    story.extend([p(metadata, small), p('Generated ' + generated, small), Spacer(0, 16)])

    def section(name, note='', chart=False):
        if chart:
            story.append(p(name, section_style))
            if note: story.append(p(note, section_note))
        else:
            # Reserve space for a heading plus a table header/first row, but
            # allow a long table to use the rest of the current page.
            story.append(CondPageBreak(110))
            style=ParagraphStyle('TableSection',parent=section_style,keepWithNext=False)
            note_style=ParagraphStyle('TableSectionNote',parent=section_note,keepWithNext=False)
            story.append(p(name,style))
            if note: story.append(p(note,note_style))

    def cards(items):
        gap = 12
        width = (doc.width - gap * (len(items) - 1)) / len(items)
        cells, widths = [], []
        for index, (label, number, note, highlight) in enumerate(items):
            if index:
                cells.append(''); widths.append(gap)
            value_style = ParagraphStyle('Metric' + str(index), parent=card_value, textColor=teal if highlight else ink)
            inner = Table([[[p(label, card_label), p(fmt(number), value_style), p(note, card_note)]]], colWidths=[width])
            inner.setStyle([('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#eaf6f2') if highlight else pale),
                            ('BOX', (0, 0), (-1, -1), .5, line), ('LEFTPADDING', (0, 0), (-1, -1), 15),
                            ('RIGHTPADDING', (0, 0), (-1, -1), 15), ('TOPPADDING', (0, 0), (-1, -1), 13),
                            ('BOTTOMPADDING', (0, 0), (-1, -1), 13)])
            cells.append(inner); widths.append(width)
        row = Table([cells], colWidths=widths)
        row.setStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'), ('LEFTPADDING', (0, 0), (-1, -1), 0),
                      ('RIGHTPADDING', (0, 0), (-1, -1), 0), ('TOPPADDING', (0, 0), (-1, -1), 0), ('BOTTOMPADDING', (0, 0), (-1, -1), 0)])
        story.extend([row, Spacer(0, 10)])

    def table(data, ratios, numeric=()):
        converted = []
        for i, row in enumerate(data):
            converted.append([p(v, (head_right if j in numeric else table_head) if i == 0 else (right if j in numeric else body)) for j, v in enumerate(row)])
        tbl = LongTable(converted, colWidths=[doc.width * r for r in ratios], repeatRows=1, splitByRow=1, splitInRow=1)
        tbl.setStyle([('BACKGROUND', (0, 0), (-1, 0), ink), ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                      ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, pale]),
                      ('LINEBELOW', (0, 0), (-1, 0), .8, teal), ('LINEBELOW', (0, 1), (-1, -1), .3, line),
                      ('LEFTPADDING', (0, 0), (-1, -1), 10), ('RIGHTPADDING', (0, 0), (-1, -1), 10),
                      ('TOPPADDING', (0, 0), (-1, -1), 8), ('BOTTOMPADDING', (0, 0), (-1, -1), 8)])
        story.append(tbl)

    totals = insight['totals']
    if f['mode'] == 'recipient':
        cards([('Total BDT delivered', totals['total_bdt'], 'BDT from remittances + local BDT', True),
               ('Total KRW sent / paid', totals['total_krw'], 'Recorded KRW amounts; not a conversion', False),
               ('Confirmed payments', totals['count'], str(totals['review']) + ' payment(s) excluded for review', False)])
        details = [('Remitted KRW', totals['abroad_krw']), ('Remittance BDT', totals['abroad_bdt']),
                   ('Local KRW', totals['local_krw']), ('Local BDT', totals['local_bdt'])]
        table([[x[0] for x in details], [fmt(x[1]) for x in details]], [.25] * 4, numeric=range(4))
        if f['rate']:
            section('Estimated equivalents', 'Your chosen rate: 1 KRW = ' + f['rate'] + ' BDT. These estimates do not change recorded amounts.')
            cards([('Estimated total BDT equivalent', totals['equivalent_bdt'], 'Includes direct local KRW at your rate', True),
                   ('Estimated total KRW equivalent', totals['equivalent_krw'], 'Equivalent recipient value, not actual KRW cost', False)])
        note = 'KRW and BDT are separate views of the same remittances; never add them together. Each linked transfer counts once.'
    else:
        cur = f['currency']
        if f['mode'] == 'overview':
            cards([('Income', totals['income'], cur + ' | recorded income', True),
                   ('Net spending', totals['spending'], cur + ' | after purchase refunds', False),
                   ('Income minus spending', totals['balance'], cur + ' | before transfers and adjustments', False)])
            table([['Spending before refunds', 'Refunds'], [fmt(totals['gross']), fmt(totals['refunds'])]], [.5, .5], numeric=(0, 1))
        else:
            cards([('Net spending', totals['spending'], cur + ' | selected category including children', True),
                   ('Spending before refunds', totals['gross'], cur + ' | expenses and fee transactions', False),
                   ('Refunds', totals['refunds'], cur + ' | card settlements excluded', False)])
        note = 'Transfers and card repayments are excluded. Net spending includes purchases and fees minus purchase refunds.'
    story.extend([Spacer(0, 9), p(note, small)])
    for warning in insight['warnings']:
        warning_box = Table([[p('REVIEW NOTICE  |  ' + warning, small)]], colWidths=[doc.width])
        warning_box.setStyle([('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#fff5e5')),
                             ('LEFTPADDING', (0, 0), (-1, -1), 12), ('RIGHTPADDING', (0, 0), (-1, -1), 12),
                             ('TOPPADDING', (0, 0), (-1, -1), 9), ('BOTTOMPADDING', (0, 0), (-1, -1), 9)])
        story.extend([Spacer(0, 8), warning_box])

    def bars():
        title = 'Money delivered over time' if f['mode'] == 'recipient' else ('Income and spending over time' if f['mode'] == 'overview' else 'Spending over time')
        legend = 'Remittance BDT (teal) + local BDT (blue)' if f['mode'] == 'recipient' else ('Income (teal) / net spending (coral) - ' + f['currency'] if f['mode'] == 'overview' else 'Net spending - ' + f['currency'])
        section(title, legend, chart=True)
        if not insight['months']:
            story.append(p('No matching records.', small)); return
        months=insight['months']
        chunks=[months[:3]]+[months[start:start+6] for start in range(3,len(months),6)]
        for index,chunk in enumerate(chunks):
            if index: section(title+' (continued)',legend,chart=True)
            drawing = Drawing(doc.width, len(chunk) * 36 + 12)
            x, plot = 78, doc.width - 225
            for i, month in enumerate(chunk):
                y = drawing.height - 23 - i * 36
                drawing.add(String(0, y + 2, month['month'], fontName=font, fontSize=8.5, fillColor=muted))
                if f['mode'] == 'recipient':
                    drawing.add(Rect(x, y, plot, 11, fillColor=pale, strokeColor=None))
                    w1, w2 = plot * month['abroad_width'] / 100, plot * month['local_width'] / 100
                    drawing.add(Rect(x, y, w1, 11, fillColor=teal, strokeColor=None))
                    drawing.add(Rect(x + w1, y, w2, 11, fillColor=blue, strokeColor=None))
                    drawing.add(String(doc.width - 8, y + 2, 'BDT ' + fmt(month['total_bdt']), textAnchor='end', fontName=bold, fontSize=8.5, fillColor=ink))
                else:
                    if f['mode'] == 'overview':
                        drawing.add(Rect(x, y + 7, plot, 7, fillColor=pale, strokeColor=None))
                        drawing.add(Rect(x, y + 7, plot * month['income_width'] / 100, 7, fillColor=teal, strokeColor=None))
                        drawing.add(String(doc.width - 8, y + 7, fmt(month['income']), textAnchor='end', fontName=font, fontSize=8, fillColor=teal))
                    drawing.add(Rect(x, y - 5, plot, 7, fillColor=pale, strokeColor=None))
                    drawing.add(Rect(x, y - 5, plot * month['spending_width'] / 100, 7, fillColor=teal if month['net_spending'] < 0 else coral, strokeColor=None))
                    drawing.add(String(doc.width - 8, y - 5, fmt(month['net_spending']), textAnchor='end', fontName=font, fontSize=8, fillColor=ink))
            story.extend([drawing, Spacer(0, 5)])
    bars()

    story.append(CondPageBreak(220))
    if f['mode'] == 'recipient':
        section('Monthly payment breakdown', 'Confirmed payments only. Remittance principal and local sends stay separate.')
        table([['Month', 'KRW abroad', 'Remittance BDT', 'Local KRW', 'Local BDT', 'Total KRW', 'Total BDT']] + [
            [m['month']] + [fmt(m[k]) for k in ('abroad_krw', 'abroad_bdt', 'local_krw', 'local_bdt', 'total_krw', 'total_bdt')] for m in insight['months']], [.1, .15, .15, .14, .14, .16, .16], numeric=range(1, 7))
        section('By recipient')
        table([['Recipient', 'Total KRW', 'Remittance BDT', 'Local BDT', 'Total BDT']] + [
            [r['name']] + [fmt(r[k]) for k in ('total_krw', 'abroad_bdt', 'local_bdt', 'total_bdt')] for r in insight['people']], [.28, .18, .18, .18, .18], numeric=range(1, 5))
        section('Payments behind this total', 'One row per payment. Records marked EXCLUDED do not contribute to the totals.')
        table([['Date / recipient', 'Channel', 'Total KRW', 'Total BDT', 'Note / review']] + [
            [e['date'] + ' / ' + e['recipient'], e['channel'], fmt(e['total_krw']), fmt(e['total_bdt']),
             e['note'] + (' | EXCLUDED: ' + '; '.join(e['issues']) if e['issues'] else '')] for e in insight['events']], [.2, .18, .14, .14, .34], numeric=(2, 3))
    else:
        section('Monthly breakdown', 'All amounts are in ' + f['currency'] + '.')
        table([['Month', 'Income', 'Before refunds', 'Refunds', 'Net spent']] + [
            [m['month']] + [fmt(m[k]) for k in ('income', 'spending', 'refunds', 'net_spending')] for m in insight['months']], [.16, .21, .21, .21, .21], numeric=range(1, 5))
        if insight['categories']:
            section('Where the money went', 'Top categories by net spending - ' + f['currency'] + '. Full values are listed below.', chart=True)
            cats = insight['categories'][:8]
            drawing = Drawing(doc.width, len(cats) * 29 + 8)
            for index, category in enumerate(cats):
                y = drawing.height - 20 - index * 29
                name = category['name']
                while name and pdfmetrics.stringWidth(name, font, 8) > 160: name = name[:-1]
                if name != category['name']: name = name[:-3] + '...'
                drawing.add(String(0, y + 1, name, fontName=font, fontSize=8, fillColor=ink))
                drawing.add(Rect(175, y, doc.width - 310, 9, fillColor=pale, strokeColor=None))
                drawing.add(Rect(175, y, (doc.width - 310) * category['width'] / 100, 9, fillColor=teal if category['net'] < 0 else blue, strokeColor=None))
                drawing.add(String(doc.width - 8, y + 1, fmt(category['net']), textAnchor='end', fontName=bold, fontSize=8, fillColor=ink))
            story.append(drawing)
        section('Category breakdown')
        table([['Category', 'Before refunds', 'Refunds', 'Net spent', 'Records']] + [
            [c['name']] + [fmt(c[k]) for k in ('gross', 'refunds', 'net', 'count')] for c in insight['categories']], [.32, .18, .18, .18, .14], numeric=range(1, 5))
    section('Supporting transactions', 'Original ledger rows for verification. Both legs of a transfer are shown, not counted as extra payments.')
    table([['Date / ID', 'Currency / type', 'Account / recipient', 'Category / status', 'Signed amount', 'Note']] + [
        [r['date'] + ' / ' + r['key'], r['currency'] + ' / ' + r['type'], r['account'] + ' / ' + r['recipient'],
         r['category'] + ' / ' + r['status'], fmt(r['amount']), r['note']] for r in source_rows(insight)], [.13, .16, .18, .16, .12, .25], numeric=(4,))

    short_title = title
    while short_title and pdfmetrics.stringWidth(short_title, font, 8) > doc.width * .65: short_title = short_title[:-1]
    def decorate(canvas, document):
        canvas.saveState()
        if document.page > 1:
            canvas.setFont(bold, 8); canvas.setFillColor(ink)
            canvas.drawString(36, landscape(A4)[1] - 21, 'MY MONEY  /  ' + short_title)
            canvas.setStrokeColor(line); canvas.line(36, landscape(A4)[1] - 28, landscape(A4)[0] - 36, landscape(A4)[1] - 28)
        canvas.setStrokeColor(line); canvas.line(36, 28, landscape(A4)[0] - 36, 28)
        canvas.setFillColor(muted); canvas.setFont(font, 7)
        canvas.drawString(36, 16, 'Private financial report  |  ' + generated)
        canvas.drawRightString(landscape(A4)[0] - 36, 16, 'Page ' + str(document.page))
        canvas.restoreState()
    doc.build(story, onFirstPage=decorate, onLaterPages=decorate)
    return out.getvalue()
