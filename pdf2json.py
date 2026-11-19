"""PDF 10年表 → 平台 JSON。用法: python3 pdf2json.py 營業.pdf 非營業.pdf > data.json"""
import json, re, subprocess, sys
from itertools import combinations

COLS = ['業務總收入', '業務總支出', '本期餘絀', '賸餘繳庫數', '購建固定資產', '國庫增撥基金',
        '政府補助收入', '資產', '現金', '負債', '長債餘額', '累積賸餘']
# PDF 欄位順序 → 平台欄位 (None = 不用)
BIZ = ['業務總收入', '業務總支出', '本期餘絀', '賸餘繳庫數', '購建固定資產', '_增撥合計', '國庫增撥基金',
       '政府補助收入', '資產', '現金', '負債', '長債餘額', '累積賸餘', None, None]  # 權益→累積賸餘; 略 ROA/ROE
NON = ['業務總收入', '業務總支出', '本期餘絀', '賸餘繳庫數', '購建固定資產', '_增撥合計', '國庫增撥基金',
       '政府補助收入', '資產', '現金', '負債', '長債餘額', '累積賸餘']
TITLE = re.compile(r'^\s*(\S.*?)近\s*(?:10\s*)?年度?收支概況彙.表')
ROW = re.compile(r'^\s*(1\d\d)\s{2,}(\S.*)$')
TOK = re.compile(r'-?[\d,]+(?:\.\d+)?%?')
SEP = re.compile(r'[－-]')


def num(s):
    return None if s.endswith('%') else int(s.replace(',', ''))


def row_tokens(lines):
    toks = []
    for ln in lines:
        m = ROW.match(ln)
        if m:
            ts = [(t.group(), t.end()) for t in TOK.finditer(ln, m.start(2))]
            if ts:
                toks.append((m.group(1), ts))
    return toks


def ref_ends(toks):
    return [e for _, e in max(toks, key=lambda r: len(r[1]))[1]]


def fill_ends(ends, template):
    """整欄空白時：把現有欄位對到範本(0~1 相對位置)上誤差最小的位置，再補出缺欄位置。"""
    best = None
    n = len(template)
    for mid in combinations(range(3, n - 1), len(ends) - 4):  # 收入/支出/餘絀與末欄必有值，錨定比例；ponytail: 暴力 C(13,k)
        slots = (0, 1, 2, *mid, n - 1)
        xs = [template[s] for s in slots]
        mx, my = sum(xs) / len(xs), sum(ends) / len(ends)
        var = sum((x - mx) ** 2 for x in xs) or 1e-9
        a = sum((x - mx) * (y - my) for x, y in zip(xs, ends)) / var
        b = my - a * mx
        cost = sum((a * x + b - y) ** 2 for x, y in zip(xs, ends))
        if a > 0 and (best is None or cost < best[0]):
            best = (cost, a, b, slots)
    _, a, b, slots = best
    full = [a * t + b for t in template]
    for s, e in zip(slots, ends):
        full[s] = e
    return full


def parse_rows(toks, ncol, template):
    if not toks:
        return []
    ends = ref_ends(toks)
    if len(ends) != ncol:
        ends = fill_ends(ends, template)
    out = []
    for year, ts in toks:
        vals = [None] * ncol
        for t, e in ts:  # ponytail: 數字右對齊，依右緣找最近欄；跨頁欄寬漂移就會錯，靠下方勾稽抓
            vals[min(range(ncol), key=lambda i: abs(ends[i] - e))] = t
        out.append((year, vals))
    return out


def fund_meta(title, kind, section):
    t = re.sub(r'\s+', '', title).replace('基金基金', '基金')
    t = re.sub(r'[（(]含[^)）]*[)）]', '', t)
    if '總表' in t or '內部往來' in t:
        return None
    parts = SEP.split(t, 1)
    if len(parts) == 2:
        parent, name = (re.sub(r'\((母|分)基金\)', '', p) for p in parts)
        if name == parent:
            name += '(分基金)'
        level = '分基金'
    else:
        parent, name = t, t
        level = '分基金' if t.startswith('國立臺灣大學附設醫院') and t != '國立臺灣大學附設醫院作業基金' else '彙編基金'
    return {'基金別': section, '基金名稱': name, '基金層級': level, '母基金': parent,
            '主管機關別': '', '業別': '', '基金編號': ''}


def section_of(kind, title):
    if kind == 'biz':
        return '營業基金'
    if '債務基金' in title:
        return '債務基金'
    if '營舍及設施改建' in title:
        return '資本計畫基金'
    return None  # 依順序決定 作業/特別收入


def extract(path, kind):
    text = subprocess.run(['pdftotext', '-layout', path, '-'], capture_output=True, text=True, check=True).stdout
    lines = text.splitlines()
    cmap = BIZ if kind == 'biz' else NON
    heads = {i: m.group(1) for i, ln in enumerate(lines) if (m := TITLE.match(ln)) and '...' not in ln and '---' not in ln}
    for j, ln in enumerate(lines):  # 標題漏寫「近10年」者：取「單位」行上方那行
        if '單位' in ln and '千元' in ln and not any(j - 3 <= i < j for i in heads):
            i = max(k for k in range(j) if lines[k].strip())
            heads[i] = lines[i].strip()
    heads = sorted(heads.items())
    segs = []
    for k, (i, title) in enumerate(heads):
        end = heads[k + 1][0] if k + 1 < len(heads) else len(lines)
        segs.append((title, row_tokens(lines[i + 1:end])))
    fulls = [ref_ends(t) for _, t in segs if t and len(ref_ends(t)) == len(cmap)]
    template = [sum((f[c] - f[0]) / (f[-1] - f[0]) for f in fulls) / len(fulls) for c in range(len(cmap))]
    records, seq_section = [], '作業基金'
    for title, toks in segs:
        if kind == 'non' and '債務基金' in title:
            seq_section = '特別收入基金'
        meta = fund_meta(title, kind, section_of(kind, title) or seq_section)
        if not meta:
            continue
        for year, vals in parse_rows(toks, len(cmap), template):
            rec = {**meta, '年度': year}
            for col, v in zip(cmap, vals):
                if col:
                    rec[col] = num(v) if v else None
            records.append(rec)
    return records


def check(records):
    bad = [r for r in records if None not in (r['業務總收入'], r['業務總支出'], r['本期餘絀'])
           and abs(r['業務總收入'] - r['業務總支出'] - r['本期餘絀']) > 2]
    bad += [r for r in records if None not in (r['_增撥合計'], r['國庫增撥基金'], r['政府補助收入'])
            and abs(r['_增撥合計'] - r['國庫增撥基金'] - r['政府補助收入']) > 2]
    for r in bad:
        print(f"勾稽不符: {r['基金名稱']} {r['年度']}", file=sys.stderr)
    names = {}
    for r in records:
        names.setdefault((r['基金名稱'], r['年度']), 0)
        names[(r['基金名稱'], r['年度'])] += 1
    for k, n in names.items():
        if n > 1:
            print(f'重複: {k}', file=sys.stderr)


if __name__ == '__main__':
    recs = extract(sys.argv[1], 'biz') + extract(sys.argv[2], 'non')
    check(recs)
    for r in recs:
        del r['_增撥合計']
    print(f'{len(recs)} 筆, {len({r["基金名稱"] for r in recs})} 基金', file=sys.stderr)
    json.dump(recs, sys.stdout, ensure_ascii=False, indent=1)
