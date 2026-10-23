"""核对 ingest 的 pending 构成（与 classify 的差异诊断）"""
import importlib.util
from collections import Counter

spec = importlib.util.spec_from_file_location('ingest', 'app/ingest_facts.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

path = '/Users/bruce/Documents/knowhow/佳宠国际/产品资料图鉴信息册20260617（汇率6.6）.xlsx'
rows, skipped = m.extract(path)
print('extract 总行数:', len(rows), '跳过的sheet:', skipped)

pending = []
for row in rows:
    if m.is_empty_row(row):
        continue
    if m.validate(row):
        pending.append((row.get('_sheet'), row.get('sku_code'), row.get('name_zh')))

print('pending 行数:', len(pending))
print('按sheet分布:', dict(Counter(p[0] for p in pending)))
print('样例:')
for sheet, sku, zh in pending[:10]:
    print(f'  {sheet}: sku={sku!r} name_zh={str(zh)[:20]!r}')
