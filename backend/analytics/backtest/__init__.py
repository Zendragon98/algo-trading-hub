"""Offline strategy backtesting against stored 1m klines."""

from .runner import BacktestResult, run_backtest

__all__ = ["BacktestResult", "run_backtest"]
