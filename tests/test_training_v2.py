"""Training scoring regression tests."""
from knowhow.services.training import score_response


def test_same_answer_scores_full_marks():
    score, matched, missing = score_response(
        "确认型号、数量、目的地后报价",
        "确认型号、数量、目的地后报价",
    )
    assert score == 100
    assert matched
    assert missing == []


def test_missing_business_information_reduces_score():
    score, _matched, missing = score_response(
        "确认型号、数量、目的地后报价",
        "请提供型号",
    )
    assert 0 < score < 100
    assert "目的地" in missing

