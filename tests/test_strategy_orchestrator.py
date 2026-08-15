import pytest
from solbot.strategy_orchestrator import StrategyOrchestrator, StrategyProfile


def test_strategy_initialization():
    orch = StrategyOrchestrator(initial_strategy="alpha_sniper", auto_switch=True)
    assert orch.active_strategy_name == "alpha_sniper"
    assert orch.current.name == "alpha_sniper"
    assert orch.current.ai_min_score == 75
    assert orch.current.max_positions == 3
    assert orch.auto_switch_enabled is True


def test_manual_strategy_switching():
    orch = StrategyOrchestrator()
    
    # Switch via alias '2' (runner_momentum)
    ok, msg = orch.switch_strategy("2")
    assert ok is True
    assert orch.active_strategy_name == "runner_momentum"
    assert orch.current.name == "runner_momentum"

    # Switch via name 'whale' (kol_whale_copy)
    ok, msg = orch.switch_strategy("whale")
    assert ok is True
    assert orch.active_strategy_name == "kol_whale_copy"

    # Switch via name 'safe' (conservative_rebalancer)
    ok, msg = orch.switch_strategy("safe")
    assert ok is True
    assert orch.active_strategy_name == "conservative_rebalancer"
    assert orch.current.stop_loss_pct == 0.08


def test_auto_failover_on_3_consecutive_losses():
    orch = StrategyOrchestrator(initial_strategy="alpha_sniper", auto_switch=True)
    assert orch.active_strategy_name == "alpha_sniper"

    # Loss 1
    alert = orch.record_trade_result(-0.005, -25.0)
    assert alert is None
    assert orch.current.consecutive_losses == 1

    # Loss 2
    alert = orch.record_trade_result(-0.004, -20.0)
    assert alert is None
    assert orch.current.consecutive_losses == 2

    # Loss 3 -> Should trigger auto-failover to runner_momentum!
    alert = orch.record_trade_result(-0.006, -30.0)
    assert alert is not None
    assert "STRATEGY AUTO-FAILOVER TRIGGERED" in alert
    assert orch.active_strategy_name == "runner_momentum"


def test_auto_failover_disabled_toggle():
    orch = StrategyOrchestrator(initial_strategy="alpha_sniper", auto_switch=False)
    
    # 3 consecutive losses with auto-switch disabled
    orch.record_trade_result(-0.005, -25.0)
    orch.record_trade_result(-0.005, -25.0)
    alert = orch.record_trade_result(-0.005, -25.0)
    
    assert alert is None
    assert orch.active_strategy_name == "alpha_sniper"


def test_dashboard_text_generation():
    orch = StrategyOrchestrator()
    dashboard = orch.get_dashboard_text()
    assert "MULTI-STRATEGY CONTROL" in dashboard
    assert "Safe Alpha Sniper" in dashboard
    assert "Missed Runner Momentum" in dashboard
    assert "Whale & KOL Copy" in dashboard
    assert "Capital Preservation" in dashboard
