"""GemArc accounts package (Google sign-in, personal access tokens, Postgres storage).

This module deliberately imports nothing: when accounts are disabled the deployed app must not load any
account code, database driver or crypto library (design invariant I1).
"""
