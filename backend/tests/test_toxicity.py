"""ToxicityScorer composite."""

from common.config import Settings
from engine.market_data.book_depletion import DepletionStats
from engine.market_data.markout_tracker import MarkoutStats
from engine.market_data.mid_tracker import MidStats
from engine.market_data.toxicity import ToxicityScorer
from engine.market_data.trade_tape import TapeStats


def test_toxic_when_scores_high() -> None:
    scorer = ToxicityScorer(Settings(mm_toxicity_threshold=0.5))
    out = scorer.score(
        tape=TapeStats(
            bid_hit_qty=1.0,
            ask_hit_qty=9.0,
            bid_hit_count=1,
            ask_hit_count=9,
            vpin=0.9,
            large_trade_share=0.8,
            trades_per_sec=40.0,
        ),
        depletion=DepletionStats(
            bid_depletion_score=0.1,
            ask_depletion_score=0.9,
        ),
        markout=MarkoutStats(adverse_ewma_bps=12.0),
        mid=MidStats(jump_active=True),
    )
    assert out.toxicity_score >= 0.5
    assert out.is_toxic is True
