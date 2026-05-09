"""FastAPI surface that the React console talks to.

Holds no business logic. Reads engine state, publishes engine events, and
forwards control commands. Lives in the same process and event loop as
the engine so there is zero IPC overhead.
"""
