"""Makes `import genlayer` resolve to the offline stub and loads contract modules.

Set OR_CONTRACT_DIR to run the whole suite against another directory, for
example the AST-stripped deploy versions in ./deploy.
"""
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "genlayer_stub"))

import genlayer as G  # noqa: E402

CONTRACT_DIR = os.environ.get("OR_CONTRACT_DIR", os.path.join(ROOT, "contracts"))
_cache = {}


def load(name):
    if name in _cache:
        return _cache[name]
    path = os.path.join(CONTRACT_DIR, name + ".py")
    spec = importlib.util.spec_from_file_location("or_" + name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _cache[name] = mod
    return mod


W = G.W
Rollback = G.Rollback
ConsensusFailure = G.ConsensusFailure
