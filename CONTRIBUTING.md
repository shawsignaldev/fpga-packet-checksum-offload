# Contributing

Changes should preserve the separation between protocol interpretation, arithmetic, the independent cycle model, and RTL. Keep public claims tied to checked-in tests or reproducible reports.

## Development setup

```bash
python -m venv .venv
python -m pip install -e ".[dev]"
python -m ruff check .
python -m ruff format --check .
python -m pytest -q
```

Hardware and formal checks require Verilator, Icarus Verilog, Yosys, and SymbiYosys. With OSS CAD Suite on the path, enforce the required-tool gate with:

```bash
REQUIRE_HDL_TOOLS=1 python -m pytest tests/test_rtl_contract.py -q
python tools/generate_rtl_vectors.py --check
```

## Change discipline

1. Add a failing test for each behavior change.
2. Keep protocol limits and ready/valid timing semantics explicit.
3. Regenerate reports and vectors through their documented commands.
4. Run the complete software, RTL, formal, package, and content checks that apply.
5. Describe evidence and limits without timing closure or deployment claims that the repository cannot reproduce.

Commits must not contain credentials, workstation paths, build products, or private input captures. Security-sensitive reports should follow [SECURITY.md](SECURITY.md).
