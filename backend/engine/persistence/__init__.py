"""Per-run on-disk archive of EventBus traffic.

Splits the bus stream into one JSONL file per event type so a session
can be replayed and analysed offline (pandas, DuckDB, parquet, etc.)
without having to re-run live trading.
"""
