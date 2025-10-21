#!/usr/bin/env python3
"""
Pytest test suite for uploader_enumerator.py

These tests create temporary directory trees and rules files to validate the
behavior required by the functional spec:
- default behaviour (no rules file -> all files included)
- exclude folder
- include extension overrides excluded folder
- include_path_by_file exact override
- exclude_extension with include_file override (pattern)
- case-insensitive matching behavior
"""

import os
import sys
import json
import stat
import tempfile
from pathlib import Path
import shutil
import subprocess

import pytest

# Ensure the module under test is importable from the repository root
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import uploader_enumerator as ue  # type: ignore


def write_file(path: Path, content: str = ""):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def create_tree(base: Path, files: dict):
    """
    files: dict mapping relative posix path -> content
    """
    for rel, content in files.items():
        p = base.joinpath(*rel.split("/"))
        write_file(p, content)


def run_enum(tmpdir: Path, rules_filename: str = ".uploaderignore", **kwargs):
    """
    Helper: call enumerate_files and return the dict result.
    """
    return ue.enumerate_files(
        register_folder=tmpdir,
        rules_filename=rules_filename,
        follow_symlinks=kwargs.get("follow_symlinks", False),
        case_sensitive=kwargs.get("case_sensitive", True),
        include_dotfiles=kwargs.get("include_dotfiles", True),
    )


def test_no_rules_includes_all(tmp_path):
    tmp = tmp_path / "proj"
    tmp.mkdir()
    files = {
        "README.md": "readme",
        "src/main.py": "print('hi')",
        "build/artifact.o": "bin",
        ".hidden": "secret",
    }
    create_tree(tmp, files)

    out = run_enum(tmp)
    # all files (relative posix) should be present and sorted
    expected = sorted([p for p in files.keys()])
    assert out["files"] == expected


def test_exclude_folder(tmp_path):
    tmp = tmp_path / "proj2"
    tmp.mkdir()
    files = {
        "build/notes.txt": "n",
        "src/main.py": "print('x')",
    }
    create_tree(tmp, files)
    # write rules file excluding build
    rules = """
[exclude_folder]
build
"""
    write_file(tmp / ".uploaderignore", rules)
    out = run_enum(tmp)
    assert "src/main.py" in out["files"]
    assert "build/notes.txt" not in out["files"]


def test_include_extension_overrides_excluded_folder(tmp_path):
    tmp = tmp_path / "proj3"
    tmp.mkdir()
    files = {
        "build/notes.md": "# notes",
        "build/ignored.log": "err",
        "src/main.py": "print()",
    }
    create_tree(tmp, files)
    rules = """
[exclude_folder]
build

[include_extension]
md
"""
    write_file(tmp / ".uploaderignore", rules)
    out = run_enum(tmp)
    # notes.md must be included despite build excluded
    assert "build/notes.md" in out["files"]
    assert "build/ignored.log" not in out["files"]


def test_include_path_by_file_exact_match(tmp_path):
    tmp = tmp_path / "proj4"
    tmp.mkdir()
    files = {
        "secret/whitelist.csv": "a,b,c",
        "secret/other.csv": "x",
    }
    create_tree(tmp, files)
    rules = """
[exclude_folder]
secret

[include_path_by_file]
secret/whitelist.csv
"""
    write_file(tmp / ".uploaderignore", rules)
    out = run_enum(tmp)
    assert "secret/whitelist.csv" in out["files"]
    assert "secret/other.csv" not in out["files"]


def test_exclude_extension_with_include_file_pattern(tmp_path):
    tmp = tmp_path / "proj5"
    tmp.mkdir()
    files = {
        "app.log": "l1",
        "app.log.important": "l2",
    }
    create_tree(tmp, files)
    rules = """
[exclude_extension]
log

[include_file]
*.log.important
"""
    write_file(tmp / ".uploaderignore", rules)
    out = run_enum(tmp)
    assert "app.log" not in out["files"]
    assert "app.log.important" in out["files"]


def test_case_insensitive_matching(tmp_path):
    tmp = tmp_path / "proj6"
    tmp.mkdir()
    files = {
        "Docs/README.MD": "md",
        "src/main.py": "x",
    }
    create_tree(tmp, files)
    rules = """
[exclude_folder]
docs

[include_extension]
md
"""
    write_file(tmp / ".uploaderignore", rules)
    # run with case-insensitive mode
    out = run_enum(tmp, case_sensitive=False)
    # README.MD should be included because include_extension md matches case-insensitively
    assert "Docs/README.MD" in out["files"]


def test_rules_file_not_uploaded_by_default(tmp_path):
    tmp = tmp_path / "proj7"
    tmp.mkdir()
    files = {
        ".uploaderignore": "[exclude_file]\nsecret.txt\n",
        "secret.txt": "secret",
        "readme.txt": "ok",
    }
    create_tree(tmp, files)
    out = run_enum(tmp)
    assert ".uploaderignore" not in out["files"]


def test_symlink_file_and_follow_symlink_dir(tmp_path):
    tmp = tmp_path / "proj8"
    tmp.mkdir()
    (tmp / "real_dir").mkdir()
    write_file(tmp / "real_dir/inside.txt", "hi")
    # symlink to file
    (tmp / "link_to_file").symlink_to(tmp / "real_dir" / "inside.txt")
    # symlink to dir
    (tmp / "link_to_dir").symlink_to(tmp / "real_dir", target_is_directory=True)

    # No rules
    out_no_follow = run_enum(tmp, follow_symlinks=False)
    # link_to_file should appear (symlinked files are included), link_to_dir contents should not be traversed
    assert "link_to_file" in out_no_follow["files"]
    assert "link_to_dir/inside.txt" not in out_no_follow["files"]

    out_follow = run_enum(tmp, follow_symlinks=True)
    # with follow, the file inside symlinked dir should appear
    assert "link_to_dir/inside.txt" in out_follow["files"]


# If running tests directly, ensure they can import the module
if __name__ == "__main__":
    # Run pytest on this file for a quick smoke test
    sys.exit(pytest.main([str(Path(__file__) )]))
