"""Off-engine analytics. Calibrates thresholds + writes reports.

Pure batch code. Never imported by the live engine; the engine reads only
the parquet/JSON artefacts produced here.
"""
