from epictrace.retrieval.fuse import rrf_fuse
from epictrace.retrieval.types import RetrievedChunk


def _c(rid):
    return RetrievedChunk(text=f"t{rid}", ingest_record_id=rid, project_id=7,
                          char_start=0, char_end=1, source_type="folder_scan")


def test_rrf_rewards_items_ranked_high_in_both_lists():
    dense = [_c(1), _c(2), _c(3)]
    sparse = [_c(2), _c(1), _c(4)]
    fused = rrf_fuse([dense, sparse], k=10)
    # 1 与 2 在两路都靠前 → 应排在仅单路出现的 3/4 之前
    top2 = {c.ingest_record_id for c in fused[:2]}
    assert top2 == {1, 2}


def test_rrf_dedups_by_chunk_key():
    fused = rrf_fuse([[_c(1)], [_c(1)]], k=10)
    assert len(fused) == 1


def test_rrf_top_item_uses_canonical_rank_base():
    # 单路单项:榜首得分应为规范 RRF 的 1/(60+1)(rank 从 1 起算)。
    fused = rrf_fuse([[_c(1)]], k=10)
    assert fused[0].score == 1.0 / (60 + 1)
