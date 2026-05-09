"""Venue adapters. Everything that talks to an exchange lives here.

Each concrete venue (only `binance/` for now) implements
`gateway_interface.GatewayInterface` so the engine stays venue-agnostic.
"""
