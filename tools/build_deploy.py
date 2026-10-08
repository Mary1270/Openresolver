import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "contracts")
OUT = os.path.join(ROOT, "deploy")
FILES = ["resolver_bank.py", "question_registry.py", "resolver_engine.py",
         "prediction_pool.py", "conditional_escrow.py"]
HEADER = ["# v0.2.16",
          '# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }']
DEMO = {
    "question_registry.py": {"MIN_CHALLENGE_WINDOW": 300, "MIN_RESPONSE_WINDOW": 300, "STALL_TIMEOUT": 7200},
    "conditional_escrow.py": {"INVALID_GRACE": 300},
    "resolver_engine.py": {"FREEZE_GRACE": 600},
}


class Strip(ast.NodeTransformer):
    def _body(self, node):
        self.generic_visit(node)
        b = node.body
        if b and isinstance(b[0], ast.Expr) and isinstance(b[0].value, ast.Constant) \
                and isinstance(b[0].value.value, str):
            node.body = b[1:] or [ast.Pass()]
        return node

    visit_FunctionDef = _body
    visit_AsyncFunctionDef = _body
    visit_ClassDef = _body

    def visit_Module(self, node):
        self.generic_visit(node)
        b = node.body
        if b and isinstance(b[0], ast.Expr) and isinstance(b[0].value, ast.Constant) \
                and isinstance(b[0].value.value, str):
            node.body = b[1:]
        return node


def strip(source, overrides):
    tree = Strip().visit(ast.parse(source))
    seen = set()
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name) and node.targets[0].id in overrides:
            node.value = ast.Constant(value=overrides[node.targets[0].id])
            seen.add(node.targets[0].id)
    missing = set(overrides) - seen
    if missing:
        raise SystemExit("constants not found: %s" % sorted(missing))
    ast.fix_missing_locations(tree)
    return "\n".join(HEADER) + "\n" + ast.unparse(tree) + "\n"


def main():
    for variant in ("production", "demo"):
        d = os.path.join(OUT, variant)
        os.makedirs(d, exist_ok=True)
        for name in FILES:
            with open(os.path.join(SRC, name), encoding="utf-8") as f:
                src = f.read()
            ov = DEMO.get(name, {}) if variant == "demo" else {}
            out = strip(src, ov)
            compile(out, name, "exec")
            with open(os.path.join(d, name), "w", encoding="utf-8") as f:
                f.write(out)
            print("%s/%s %d bytes" % (variant, name, len(out)))


if __name__ == "__main__":
    sys.exit(main())
