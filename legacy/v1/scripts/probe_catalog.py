"""探查佳宠产品图鉴的完整结构：每个 sheet 的表头行、型号列、SKU 数"""
import pandas as pd

path = '/Users/bruce/Documents/knowhow/佳宠国际/产品资料图鉴信息册20260617（汇率6.6）.xlsx'
xl = pd.ExcelFile(path)
total = 0
for name in xl.sheet_names:
    # 读前 10 行（header=None），扫描哪一行是表头（含"型号"）
    head = pd.read_excel(path, sheet_name=name, header=None, nrows=10, dtype=str)
    header_row = None
    for i in range(len(head)):
        row = [str(x) for x in head.iloc[i].tolist()]
        if any('型号' in x for x in row):
            header_row = i
            break
    if header_row is None:
        print(f'{name!r}: 前10行未找到型号表头')
        continue
    # 用该行作表头重新读
    df = pd.read_excel(path, sheet_name=name, header=header_row, dtype=str)
    cols = [str(c) for c in df.columns]
    mx_cols = [c for c in cols if c.startswith('型号')]
    if not mx_cols:
        print(f'{name!r}: 表头行{header_row}但无型号列, 列={cols[:6]}')
        continue
    s = df[mx_cols[0]].dropna().astype(str).str.strip()
    s = s[s.str.lower() != 'nan']
    s = s[s != '']
    print(f'{name!r}: 表头行={header_row} 型号列={mx_cols[0]!r} SKU数={len(s)} 样例={list(s.head(2))}')
    total += len(s)
print('SKU 总数:', total)
