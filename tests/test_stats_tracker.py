from solbot.stats_tracker import StatsTracker


def test_stats_tracker_records_funnel():
    stats = StatsTracker()
    stats.bump("tokens_seen", 3)
    stats.record_filter_skip("mcap 12 SOL")
    stats.record_filter_skip("mcap 12 SOL")
    stats.bump("qualified")
    assert stats.tokens_seen == 3
    assert stats.skip_filter == 2
    assert stats.qualified == 1
    assert stats.top_filter_reasons(1)[0] == ("mcap 12 SOL", 2)