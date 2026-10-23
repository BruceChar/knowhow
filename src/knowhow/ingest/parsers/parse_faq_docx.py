"""话术库 docx → 结构化 JSON（faq 知识卡原料）。

docx 结构（已验证）：
  模块头: "M01 ｜ 第一模块：...（17 条）"
  表头:   source_id / 客户原话（逐字）/ 适用情境 / 分支 / 英文回复 / 需获取的信息（1-3）/ 内部说明
  条目:   M01-Q001 → 后续 5 行分别是 客户原话/情境/英文回复/需获取信息/内部说明

输出: [{id, module, question, answer, context, need_info, note}]
用法: uv run python app/parse_faq_docx.py "输入.docx" -o data/raw/faq.json
"""
import argparse, json, re, zipfile

def extract_paragraphs(path: str) -> list[str]:
    with zipfile.ZipFile(path) as z:
        xml = z.read('word/document.xml').decode('utf-8')
    paras = re.findall(r'<w:p[ >].*?</w:p>', xml, re.S)
    texts = []
    for pa in paras:
        t = ''.join(re.findall(r'<w:t[^>]*>([^<]*)</w:t>', pa))
        if t.strip():
            texts.append(t.strip())
    return texts

def parse(texts: list[str]) -> list[dict]:
    entries, cur = [], None
    # 行类型判定
    for t in texts:
        if re.match(r'^M\d{2}\s*｜', t):          # 模块头
            continue
        m = re.match(r'^(M\d{2}-Q\d{3})\s*$', t)  # 条目编号
        if m:
            if cur:
                entries.append(cur)
            cur = {"id": m.group(1), "module": m.group(1)[:3],
                   "lines": []}
            continue
        if cur is not None:
            cur["lines"].append(t)
    if cur:
        entries.append(cur)

    # 条目组装：编号 + 客户原话 + 情境 + 英文回复 + 需获取信息 + 内部说明
    out = []
    for e in entries:
        ls = e["lines"]
        if len(ls) < 3:
            continue
        # 去掉可能残留的表头（保护性）
        while ls and ls[0] in ("source_id", "客户原话（逐字）", "适用情境 / 分支",
                               "英文回复（客户文案）", "需获取的信息（1–3）", "内部说明"):
            ls = ls[1:]
        if not ls:
            continue
        question = ls[0]
        context = ls[1] if len(ls) > 1 else ""
        answer = ls[2] if len(ls) > 2 else ""
        need = ls[3] if len(ls) > 3 else ""
        note = ls[4] if len(ls) > 4 else ""
        out.append({
            "id": e["id"], "module": e["module"],
            "question": question, "context": context,
            "answer": answer, "need_info": need, "note": note,
        })
    return out

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("-o", "--out", default="data/raw/faq.json")
    a = ap.parse_args()
    texts = extract_paragraphs(a.path)
    entries = parse(texts)
    print(json.dumps({
        "total_entries": len(entries),
        "modules": sorted({e["module"] for e in entries}),
        "sample": entries[:3],
    }, ensure_ascii=False, indent=2))
