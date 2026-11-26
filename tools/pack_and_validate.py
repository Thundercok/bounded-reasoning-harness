"""Validate an agent directory against HARNESS_README.md rules and package dist/submission.zip.

usage: pack_and_validate.py variants/v18d_v14_submit_gate
"""
from __future__ import annotations

import ast
import os
import re
import sys
import zipfile
from pathlib import Path

import yaml

ALLOWED_MODELS = {
    "gemma-4-31b-it-qat-w4a16-ct",
    "gemma-4-12b-it-qat-w4a16-ct",
    "gemma-4-e4b-it-qat-w4a16-ct",
    "gemma-4-e2b-it-qat-w4a16-ct",
}
ALLOWED_EXTENSIONS = {".yaml", ".yml", ".md", ".txt", ".py", ".json", ".safetensors"}
MAX_TOTAL_BYTES = 3 * 1024 * 1024 * 1024  # 3 GiB


class _IncludeLoader(yaml.SafeLoader):
    def __init__(self, stream, base_dir: Path, root_dir: Path, depth: int = 0):
        super().__init__(stream)
        self._base_dir = base_dir
        self._root_dir = root_dir
        self._depth = depth


def _include_constructor(loader: _IncludeLoader, node: yaml.ScalarNode):
    if loader._depth >= 10:
        raise ValueError("Max !include depth (10) exceeded")
    rel = loader.construct_scalar(node)
    if rel.startswith("/") or "\x00" in rel:
        raise ValueError(f"Illegal !include path: {rel}")
    target = (loader._base_dir / rel).resolve()
    try:
        target.relative_to(loader._root_dir.resolve())
    except ValueError as exc:
        raise ValueError(f"!include escapes submission root: {rel}") from exc
    if not target.is_file():
        raise FileNotFoundError(f"!include target missing: {target}")
    if target.suffix.lower() in (".md", ".txt"):
        return target.read_text(encoding="utf-8")
    if target.suffix.lower() in (".yaml", ".yml"):
        return load_yaml_with_includes(target, loader._root_dir, depth=loader._depth + 1)
    raise ValueError(f"Unsupported !include file type: {target.name}")


_IncludeLoader.add_constructor("!include", _include_constructor)


def load_yaml_with_includes(path: Path, root_dir: Path, depth: int = 0):
    with open(path, "r", encoding="utf-8") as f:
        loader = _IncludeLoader(f, base_dir=path.parent, root_dir=root_dir, depth=depth)
        try:
            return loader.get_single_data()
        finally:
            loader.dispose()


def _collect_models(data, models: set[str]):
    if isinstance(data, dict):
        if "model" in data and isinstance(data["model"], str):
            models.add(data["model"].split("/")[-1])
        for v in data.values():
            _collect_models(v, models)
    elif isinstance(data, list):
        for item in data:
            _collect_models(item, models)


def validate_submission(sub_dir: Path) -> dict:
    sub_dir = sub_dir.resolve()
    root_configs = [
        sub_dir / name
        for name in ("agent.yaml", "agent.yml", "root_agent.yaml", "root_agent.yml")
        if (sub_dir / name).is_file()
    ]
    assert len(root_configs) == 1, f"Expected exactly 1 root config, found: {root_configs}"

    total_bytes = 0
    files = []
    models: set[str] = set()
    yaml_count = 0

    for dirpath, dirnames, filenames in os.walk(sub_dir, followlinks=False):
        parent = Path(dirpath)
        for d in dirnames:
            assert not (parent / d).is_symlink(), f"Symlink dir not allowed: {parent / d}"
        for name in filenames:
            p = parent / name
            assert not p.is_symlink(), f"Symlink file not allowed: {p}"
            rel = p.relative_to(sub_dir)
            ext = p.suffix.lower()
            assert ext in ALLOWED_EXTENSIONS, f"Disallowed extension: {rel}"
            total_bytes += p.stat().st_size
            files.append(str(rel))

            if ext in (".yaml", ".yml"):
                yaml_count += 1
                parsed = load_yaml_with_includes(p, sub_dir)
                _collect_models(parsed, models)
            elif ext == ".py":
                ast.parse(p.read_text(encoding="utf-8"), filename=str(rel))

    assert total_bytes <= MAX_TOTAL_BYTES, f"Unpacked size exceeds 3 GiB: {total_bytes}"
    assert len(models) == 1, f"Single-model rule violated; found models: {models}"
    model = next(iter(models))
    assert model in ALLOWED_MODELS, f"Disallowed competition model: {model}"

    # Verify every skill directory referenced in agent configs has SKILL.md with valid kebab-case frontmatter name
    skills_root = sub_dir / "skills"
    if skills_root.is_dir():
        for skill_dir in sorted(p for p in skills_root.iterdir() if p.is_dir()):
            skill_md = skill_dir / "SKILL.md"
            assert skill_md.is_file(), f"Missing SKILL.md in {skill_dir}"
            text = skill_md.read_text(encoding="utf-8")
            m = re.search(r"^---\s*\n(.*?)^---\s*$", text, re.MULTILINE | re.DOTALL)
            assert m, f"Missing YAML frontmatter block in {skill_md}"
            fm = yaml.safe_load(m.group(1)) or {}
            sname = fm.get("name", "")
            assert isinstance(sname, str) and re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", sname), (
                f"Skill frontmatter name must be lowercase kebab-case in {skill_md}, got: {sname!r}"
            )
            assert sname == skill_dir.name, (
                f"Skill frontmatter name {sname!r} must match directory name {skill_dir.name!r}"
            )

    return {
        "root_config": root_configs[0].name,
        "model": model,
        "file_count": len(files),
        "yaml_count": yaml_count,
        "total_bytes": total_bytes,
        "files": sorted(files),
    }


def pack_submission(sub_dir: Path, out_zip: Path) -> Path:
    out_zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for dirpath, _dirnames, filenames in os.walk(sub_dir):
            for name in sorted(filenames):
                full = Path(dirpath) / name
                rel = full.relative_to(sub_dir)
                zf.write(full, arcname=str(rel))
    return out_zip


def main() -> int:
    import sys
    repo_root = Path(__file__).resolve().parents[1]
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    sub_dir = Path(sys.argv[1]).resolve()
    out_zip = repo_root / "dist" / "submission.zip"
    report = validate_submission(sub_dir)
    pack_submission(sub_dir, out_zip)
    print(f"VALID: model={report['model']} files={report['file_count']} bytes={report['total_bytes']}")
    for f in report["files"]:
        print(f"  - {f}")
    print(f"PACKED: {out_zip} ({out_zip.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
