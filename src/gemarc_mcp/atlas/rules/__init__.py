"""GemArc Atlas rules: the registry (``registry``), the context (``context``) and one module per rule family.

Importing this package registers every rule and check of wave 1. The modules of the wave 2 and wave 3 rules are added here.
"""
from . import registry  # noqa: F401  (first: the families register into it)
from . import context  # noqa: F401
from . import st, rf, ev, sm, cs, rq  # noqa: F401
