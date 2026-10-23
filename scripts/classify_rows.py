"""精确分类全部行：empty / no_sku / no_zh / ok"""
import pandas as pd

path = '/Users/bruce/Documents/knowhow/佳宠国际/产品资料图鉴信息册20260617（汇率6.6）.xlsx'
xl = pd.ExcelFile(path)
cats = {'empty': 0, 'no_sku': 0, 'no_zh': 0, 'ok': 0}
no_sku_sheets = {}
for name in xl.sheet_names:
    if name == '资料整理说明':
        continue
    df = pd.read_excel(path, sheet_name=name, header=5, dtype=str)
    if '型号' not in df.columns:
        continue
    for _, r in df.iterrows():
        vals = [str(v).strip() for v in r.tolist()
                if str(v).strip() not in ('', 'nan')]
        sku = str(r.get('型号') or '').strip()
        zh = str(r.get('中文名') or '').strip()
        if not vals:
            cats['empty'] += 1
        elif sku.lower() == 'nan' or not sku:
            cats['no_sku'] += 1
            no_sku_sheets[name] = no_sku_sheets.get(name, 0) + 1
        elif zh.lower() == 'nan' or not zh:
            cats['no_zh'] += 1
        else:
            cats['ok'] += 1
print('分类:', cats)
print('缺型号按sheet:', no_sku_sheets)
