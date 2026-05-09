"""Cross-cutting building blocks used by every other backend package.

Nothing in `common/` may import from `engine/`, `gateways/`, `api/` or
`analytics/`. Keep the dependency direction one-way so this stays the
foundation layer.
"""
