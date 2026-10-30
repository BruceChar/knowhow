"""混合检索纯函数单元测试：jieba 分词 / BM25 打分 / 加权 RRF / 治理过滤 SQL。

不依赖数据库与模型，只测确定性纯函数。
"""
import pytest

from src.core.search import (
    _base_where,
    _bm25_idf,
    _bm25_score,
    _rrf,
    _rrf_weighted,
    _tokens,
)


class TestTokens:
    def test_chinese_segmentation(self):
        toks = _tokens("智能猫砂盆价格")
        assert "智能" in toks
        # jieba 实际切分：['智能','猫','砂盆','价格']
        assert "猫" in toks and "砂盆" in toks
        assert "价格" in toks

    def test_english_lowercase_and_space_stripped(self):
        toks = _tokens("  What is the MOQ?  ")
        assert "what" in toks and "moq" in toks
        assert all(t == t.lower() for t in toks)
        assert not any(t == "" for t in toks)

    def test_model_number_split(self):
        toks = _tokens("PS-2000")
        assert "ps" in toks and "2000" in toks

    def test_empty_input(self):
        assert _tokens("") == []
        assert _tokens(None) == []


class TestBm25Idf:
    def test_rare_term_gets_higher_idf(self):
        idf_rare = _bm25_idf(1000, 2)
        idf_common = _bm25_idf(1000, 500)
        assert idf_rare > idf_common

    def test_term_in_all_docs_gets_low_idf(self):
        assert _bm25_idf(100, 100) < _bm25_idf(100, 10)

    def test_idf_strictly_positive(self):
        assert _bm25_idf(10, 9) > 0


class TestBm25Score:
    def _idf(self):
        return {"moq": 2.0, "price": 1.5, "cat": 1.0}

    def test_hit_scores_positive_unhit_zero(self):
        hit = _bm25_score(["moq"], ["moq", "moq", "price"], self._idf(), 5.0)
        miss = _bm25_score(["cat"], ["moq", "price"], self._idf(), 5.0)
        assert hit > 0
        assert miss == 0.0

    def test_more_query_matches_score_more(self):
        idf = self._idf()
        one = _bm25_score(["moq"], ["moq", "price", "cat"], idf, 3.0)
        two = _bm25_score(["moq", "price"], ["moq", "price", "cat"], idf, 3.0)
        assert two > one

    def test_length_normalization(self):
        """长文档稀释词频（b=0.75 长度归一）：同词频下短文档分更高。"""
        idf = self._idf()
        short = _bm25_score(["moq"], ["moq"] * 2, idf, 10.0)
        long = _bm25_score(["moq"], ["moq"] * 2 + ["pad"] * 8, idf, 10.0)
        assert short > long

    def test_empty_doc_scores_zero(self):
        assert _bm25_score(["moq"], [], self._idf(), 1.0) == 0.0

    def test_unknown_token_skipped(self):
        s = _bm25_score(["nope"], ["nope", "nope"], self._idf(), 2.0)
        assert s == 0.0  # nope 不在 idf 中


class TestRrf:
    def test_lower_rank_scores_higher(self):
        assert _rrf([1]) > _rrf([2]) > _rrf([10])

    def test_multi_source_accumulates(self):
        assert _rrf([1, 3]) > _rrf([1])

    def test_weighted_vector_dominates_bm25(self):
        """同 rank 下 vector 权重贡献高于 bm25。"""
        vec_only = _rrf_weighted([("vector", 1)])
        bm_only = _rrf_weighted([("bm25", 1)])
        assert vec_only > bm_only

    def test_weighted_double_hit_beats_single(self):
        both = _rrf_weighted([("vector", 5), ("bm25", 3)])
        single = _rrf_weighted([("vector", 1)])
        assert both > single

    def test_weighted_bm25_does_not_override_vector_rank(self):
        """弱 BM25 单路（rank1）分值低于向量路边缘命中（rank5）——
        混合对 BM25 的抑制由『BM25 候选精取 k 条』在集成层保证。"""
        assert _rrf_weighted([("vector", 5)]) > _rrf_weighted([("bm25", 1)])


class TestBaseWhere:
    def test_default_permissions_public_internal(self):
        where, params = _base_where(None, None)
        assert "permission = ANY(%s::text[])" in where
        assert params[0] == ["public", "internal"]
        assert len(params) == 1

    def test_custom_permissions(self):
        where, params = _base_where(["internal", "confidential"], None)
        assert params[0] == ["internal", "confidential"]

    def test_customer_ready_appended_after_permissions(self):
        where, params = _base_where(None, True)
        assert "customer_ready = %s" in where
        # SQL 参数顺序：permissions 在前、customer_ready 在后
        assert params[0] == ["public", "internal"]
        assert params[1] is True

    def test_validity_window_in_where(self):
        where, _ = _base_where(None, None)
        assert "valid_from" in where and "valid_until" in where
