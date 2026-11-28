---
name: locate-code
description: Deterministic IDF + AST multi-symbol source & test locator that filters docs_src/ noise, ranks source files by multi-term co-occurrence, and returns exact read_file start_line..end_line spans plus a matching test example.
---

# Locate Code Skill

Run `scripts/locate.py` via:
`run_skill_script(skill_name="locate-code", file_path="scripts/locate.py", args=["<scope_or_module>", "<Symbol1>", "<Symbol2>", "<Symbol3>"])`

Pass 3–8 exact symbols, method/attribute names, or module scopes from the issue. It returns:
- `top_source_files`: Top 4 ranked source `.py` files with exact `start_line` and `end_line` windows (`<= 115` lines) ready for `read_file(filepath=..., start_line=..., end_line=...)`, plus matching line previews.
- `matching_test`: The single most relevant existing test function in `tests/` with its `filepath`, `start_line`, `end_line`, and code snippet showing real imports/usage.
