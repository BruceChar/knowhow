"""生成黄金集 v2：50 条，覆盖 35 模块 + 红线场景。

设计原则：
- query 用真实客户问法（口语/中英混/简写），不照抄库内 question —— 测真实检索能力
- ideal_hit_question 必须与库内 question 逐字一致（eval 用 _norm 规范化比较）
- red_line=True 的 case：价格/交期/认证/合规类，答错即失败
"""
import json

# (module, faq_id, query, red_line)
# faq_id 必须存在；query 是客户问法；ideal 会自动从 faq.json 取对应 question
CASES = [
    # ---- M01 开场与基础信息（2条）----
    ("M01", "M01-Q001", "Hi, I saw your products online and want to know more about your company, can you tell me what you sell?", False),
    ("M01", "M01-Q004", "What is the minimum order quantity for your products?", False),
    # ---- M02 目录与市场（2条）----
    ("M02", "M02-Q002", "Do you have a product brochure or catalog in PDF for this year's new collection?", False),
    ("M02", "M02-Q004", "Which models sell the most in Europe or America? Give me your top sellers.", False),
    # ---- M03 报价与MOQ（3条）----
    ("M03", "M03-Q001", "How much for 1000 pieces? Please give me the unit price.", True),
    ("M03", "M03-Q006", "你们最低起订量是多少？可以混合型号吗", False),
    ("M03", "M03-Q011", "Another factory quoted me a lower price for the same thing, can you match it?", True),
    # ---- M04 定制与品牌（3条）----
    ("M04", "M04-Q001", "Can we put our own brand logo on your products?", False),
    ("M04", "M04-Q004", "你们能做彩盒包装吗？我们想要品牌定制包装", False),
    ("M04", "M04-Q012", "How much is the mold cost and how long to open a new mold?", True),
    # ---- M05 样品（2条）----
    ("M05", "M05-Q003", "Do you charge for samples? Can the fee be refunded when we place a bulk order?", False),
    ("M05", "M05-Q006", "How long does it take to make a customized sample?", False),
    # ---- M06 认证与验厂（3条，红线2）----
    ("M06", "M06-Q001", "Do you have CE and RoHS certificates for this item?", True),
    ("M06", "M06-Q011", "Are you a real factory or just a trading company?", False),
    ("M06", "M06-Q016", "Which big brands or retailers have you worked with before?", False),
    # ---- M07 物流与包装（3条）----
    ("M07", "M07-Q001", "What is the carton size? L x W x H in cm please.", False),
    ("M07", "M07-Q005", "How many cartons fit into one 20GP container?", True),
    ("M07", "M07-Q009", "Which port do you ship from?", False),
    # ---- M08 交期（2条，红线2）----
    ("M08", "M08-Q001", "How long is your production lead time for 5000 pieces?", True),
    ("M08", "M08-Q004", "Do you have ready stock right now that can ship immediately?", True),
    # ---- M09 付款（2条，红线1）----
    ("M09", "M09-Q001", "What are your payment terms?", False),
    ("M09", "M09-Q005", "Can we do 100% L/C at sight?", True),
    # ---- M10 议价与犹豫（2条）----
    ("M10", "M10-Q001", "Your price is much higher than our budget, can you do better?", True),
    ("M10", "M10-Q005", "I need to check with my boss first before deciding.", False),
    # ---- M11 质量与质保（2条，红线1）----
    ("M11", "M11-Q001", "What is the warranty period for your products?", False),
    ("M11", "M11-Q004", "What happens if more than 2% of the goods are defective?", True),
    # ---- M12 钓鱼/异常询盘（2条，红线1）----
    ("M12", "M12-Q006", "Please send the quotation directly to my personal gmail.", False),
    ("M12", "M12-Q005", "I don't use Alibaba, contact me on WhatsApp or WeChat instead.", False),
    # ---- M13 极简询盘（2条）----
    ("M13", "M13-Q001", "Price?", False),
    ("M13", "M13-Q002", "MOQ?", False),
    # ---- M14 客户身份与渠道（1条）----
    ("M14", "M14-Q003", "We are importers and sell to distributors, do you work with companies like ours?", False),
    # ---- M15 型号与兼容（2条）----
    ("M15", "M15-Q001", "Is the product in the photo exactly the same model you will ship to me?", False),
    ("M15", "M15-Q005", "Does this work with 230V European power?", False),
    # ---- M16 选品推荐（1条）----
    ("M16", "M16-Q001", "We are new to the Polish market, what do you recommend we try first?", False),
    # ---- M17 报价构成（2条）----
    ("M17", "M17-Q001", "Is this price per piece or per set?", False),
    ("M17", "M17-Q004", "What quantity makes custom packaging worth it?", False),
    # ---- M18 混柜（1条）----
    ("M18", "M18-Q002", "Can we combine different models to reach the MOQ together?", False),
    # ---- M19 设计稿（1条）----
    ("M19", "M19-Q002", "Can you send the dieline with bleed and safe margins for our designer?", False),
    # ---- M20 产前样（1条）----
    ("M20", "M20-Q004", "Can we approve a golden sample and sign it before mass production?", False),
    # ---- M21 工厂透明度（2条）----
    ("M21", "M21-Q001", "Which parts do you make in-house and which do you buy from other factories?", False),
    ("M21", "M21-Q003", "Can you show me a video of the actual workshop, not just the showroom?", False),
    # ---- M22 质检（1条）----
    ("M22", "M22-Q001", "What quality checks do you do before packing?", False),
    # ---- M23 合规文件（1条，红线1）----
    ("M23", "M23-Q001", "Which documents apply for shipping this item to Europe?", True),
    # ---- M24 装运（1条）----
    ("M24", "M24-Q002", "Can we ship using our own forwarder's booking?", False),
    # ---- M25 单证（1条）----
    ("M25", "M25-Q002", "Can you make a PI with each SKU listed separately?", False),
    # ---- M26 订单差异（1条）----
    ("M26", "M26-Q002", "The spec in the order is different from what we agreed in chat, please check.", False),
    # ---- M27 生产进度（1条）----
    ("M27", "M27-Q002", "Can you share photos of the first finished units?", False),
    # ---- M28 客诉（2条，红线1）----
    ("M28", "M28-Q002", "Some cartons arrived damaged, what evidence do you need for a claim?", True),
    ("M28", "M28-Q004", "The color of the goods doesn't match the approved sample.", False),
    # ---- M29 返单（1条）----
    ("M29", "M29-Q001", "The first batch sold well, how long for reorder?", False),
    # ---- M30 素材（1条）----
    ("M30", "M30-Q001", "Can you provide white background photos and a short video of the product?", False),
    # ---- M31 展会拜访（1条）----
    ("M31", "M31-Q003", "Can you arrange a live video tour of your production line?", False),
    # ---- M32 项目结束（1条）----
    ("M32", "M32-Q002", "Our project is on hold until next quarter, we'll come back later.", False),
    # ---- M33 风险话术（2条，红线2）----
    ("M33", "M33-Q002", "Send us your complete customer list and we will decide what to order.", True),
    ("M33", "M33-Q005", "Can you declare a lower value on the invoice to save us import tax?", True),
    # ---- M34 产品功能（1条）----
    ("M34", "M34-Q001", "Can the grooming glove be used on wet fur too?", False),
    # ---- M35 产品细节（1条）----
    ("M35", "M35-Q001", "Please send the full specification sheet for this model.", False),
]

def main():
    with open("data/raw/faq.json", encoding="utf-8") as f:
        faq = json.load(f)
    by_id = {e["id"]: e for e in faq}

    cases = []
    used = set()
    for i, (module, faq_id, query, red_line) in enumerate(CASES, start=1):
        if faq_id not in by_id:
            print(f"!! 跳过不存在的 faq_id: {faq_id}")
            continue
        if faq_id in used:
            print(f"!! 重复 faq_id: {faq_id}")
            continue
        used.add(faq_id)
        ideal = by_id[faq_id]["question"]  # 库内原文，eval _norm 比较
        cases.append({
            "id": f"GS2-{i:03d}",
            "module": module,
            "query": query,
            "ideal_hit_question": ideal,
            "expected_facts": [by_id[faq_id]["answer"][:120]],
            "red_line": red_line,
        })

    print(f"生成 {len(cases)} 条（覆盖 {len(set(c['module'] for c in cases))} 模块）")
    red = sum(1 for c in cases if c["red_line"])
    print(f"红线 case: {red} 条")
    # 校验模块覆盖
    mods = sorted(set(c["module"] for c in cases))
    missing = [f"M{i:02d}" for i in range(1, 36) if f"M{i:02d}" not in mods]
    print(f"未覆盖模块: {missing if missing else '无'}")

    with open("data/goldset/jiacong_faq_goldset_v2.json", "w", encoding="utf-8") as f:
        json.dump({"name": "佳宠 FAQ 黄金集 v2", "version": 2, "cases": cases},
                  f, ensure_ascii=False, indent=2)
    print("已写入 data/goldset/jiacong_faq_goldset_v2.json")

if __name__ == "__main__":
    main()
