"""Import parser regression tests."""
from knowhow.services.imports import _parse_csv, _parse_text


def test_csv_question_answer_mapping():
    rows, mapping = _parse_csv(
        "问题,答案,标题\nMOQ是多少？,取决于型号和定制要求。,MOQ 规则\n"
    )
    assert mapping["question"] == "问题"
    assert mapping["answer"] == "答案"
    assert rows == [{
        "question": "MOQ是多少？",
        "answer": "取决于型号和定制要求。",
        "title": "MOQ 规则",
    }]


def test_csv_chat_export_pairing():
    rows, mapping = _parse_csv(
        "sender,message\ncustomer,Can you customize the logo?\n"
        "sales,Yes, please send the artwork and quantity.\n"
    )
    assert mapping["speaker"] == "sender"
    assert rows[0]["question"] == "Can you customize the logo?"
    assert rows[0]["answer"].startswith("Yes")


def test_wechat_style_text_pairing():
    rows, mapping = _parse_text(
        "客户：你们有现货吗？\n业务员：请提供型号，我确认库存。\n"
    )
    assert mapping["format"] == "chat_lines"
    assert rows[0]["question"] == "你们有现货吗？"
    assert rows[0]["answer"] == "请提供型号，我确认库存。"


def test_qa_block_import():
    rows, _ = _parse_text(
        "Q: What is the MOQ?\nA: It depends on customization.\n"
    )
    assert rows[0]["question"] == "What is the MOQ?"
    assert "customization" in rows[0]["answer"]

