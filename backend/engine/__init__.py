"""Trading engine. Strategy-agnostic, venue-agnostic core.

The engine consumes ticks from `market_data`, hands features to a
`strategies/StrategyBase`, validates resulting signals through `risk/`,
and dispatches approved orders to `execution/` which in turn drives
`orders/OrderManager` against the configured gateway.
"""
