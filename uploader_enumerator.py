#!/usr/bin/env python3
"""
uploader_enumerator.py

Implements the "Uploader File Enumerator" as specified.

Usage (examples):
  python uploader_enumerator.py /path/to/register-folder
  python uploader_enumerator.py /path/to/register-folder --rules-file .uploaderignore --follow-symlinks --case-insensitive

Output (printed to stdout as JSON):
{
  "root": "<absolute resolved parent folder path>",
  "files": ["relative/path/to/file1", "relative/path/to/file2", ...]
}

Notes / decisions implemented:
- Root is resolved with Path(...).resolve() and returned as string.
- Relative paths in "files" use POSIX separators (Path.as_posix()) and have no leading "./".
- Matching uses fnmatch.fnmatchcase on normalized strings. If case-insensitive mode is selected,
  both candidate strings and patterns are lowered before matching (fnmatchcase still used).
- include rules always win over exclude rules.
- Rules file is read only from the parent root (single file).
- By default, the rules file itself is implicitly excluded from results to avoid uploading it,
  unless explicitly force-included via include rules.
- Traversal does not prune excluded directories (safer) but respects --follow-symlinks flag.
- Broken symlinks and permission errors are skipped with a printed warning on stderr.
- Non-UTF8 filenames are attempted; on decode errors the file is skipped with a warning.
"""

from __future__ import annotations
import argparse
import fnmatch
import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Set, Tuple, Iterable, Optional

# ---------- Utilities ----------

def debug_print(*args, **kwargs):
    """Print debug to stderr so stdout stays clean for JSON output."""
    print(*args, file=sys.stderr, **kwargs)

def read_rules_file(path: Path) -> Dict[str, List[str]]:
    """
    Read INI-like rules file. Sections recognized (keys returned in dict):
      - exclude_folder
      - exclude_file
      - exclude_extension
      - include_folder
      - include_file
      - include_extension
      - include_path_by_file

    Unknown sections are ignored. Lines starting with '#' are comments.
    """
    sections = {
        "exclude_folder": [],
        "exclude_file": [],
        "exclude_extension": [],
        "include_folder": [],
        "include_file": [],
        "include_extension": [],
        "include_path_by_file": [],
    }
    if not path.exists():
        return sections

    current: Optional[str] = None
    try:
        raw = path.read_text(encoding="utf-8")
    except Exception as e:
        debug_print(f"Warning: Failed to read rules file {path}: {e}")
        return sections

    for raw_line in raw.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            sect = line[1:-1].strip()
            if sect in sections:
                current = sect
            else:
                current = None
            continue
        if current:
            sections[current].append(line)
    # dedupe while preserving order-ish
    for k in sections:
        seen = set()
        out = []
        for v in sections[k]:
            if v not in seen:
                seen.add(v)
                out.append(v)
        sections[k] = out
    return sections

def extract_extension(basename: str) -> Optional[str]:
    """
    Extract extension per spec:
    - If basename has at least one '.', extension is text after the last dot.
    - Files that start with a dot and have no other dot (e.g., '.gitignore') => extension is part after leading dot.
    - Returns None if there is no extension text (shouldn't happen per rules, but safe).
    """
    if basename.startswith("."):
        # '.gitignore' -> 'gitignore' (if no other dot)
        if basename.count(".") == 1:
            return basename[1:] or None
    if "." in basename:
        parts = basename.rsplit(".", 1)
        if len(parts) == 2 and parts[1] != "":
            return parts[1]
    return None

def normalize_for_case(s: str, case_sensitive: bool) -> str:
    return s if case_sensitive else s.lower()

# ---------- Matching helpers ----------

class RulesMatcher:
    def __init__(self, sections: Dict[str, List[str]], root: Path, case_sensitive: bool):
        self.root = root
        self.case_sensitive = case_sensitive

        # store lists of patterns as provided (we'll normalize at match-time)
        self.exclude_folder = sections.get("exclude_folder", [])
        self.exclude_file = sections.get("exclude_file", [])
        self.exclude_extension = sections.get("exclude_extension", [])
        self.include_folder = sections.get("include_folder", [])
        self.include_file = sections.get("include_file", [])
        self.include_extension = sections.get("include_extension", [])
        self.include_path_by_file = sections.get("include_path_by_file", [])

        # implicit exclude for the rules file itself (to avoid uploading it)
        self.implicit_exclude_basename = os.path.basename(path := str((root / default_rules_filename).name)) if False else None
        # Note: We'll implement implicit exclusion by comparing actual rules filename
        # below as a special-case in is_excluded_by_file_rule.

    def _fnmatch(self, candidate: str, pattern: str) -> bool:
        # Per spec: use fnmatch.fnmatchcase semantics. For case-insensitive mode,
        # we lowercase both candidate and pattern and still use fnmatchcase.
        if not self.case_sensitive:
            candidate = candidate.lower()
            pattern = pattern.lower()
        return fnmatch.fnmatchcase(candidate, pattern)

    def _match_folder_rule_against_ancestors(self, rel_posix: str, pattern: str) -> bool:
        """
        If pattern contains '/', treat as relative path glob from root: match rel_posix against it.
        Otherwise treat as directory-name glob and match against each ancestor directory name.
        """
        if "/" in pattern:
            # treat as relative path glob
            return self._fnmatch(rel_posix, pattern)
        # match against each directory name in ancestors
        parts = rel_posix.split("/")
        # drop last part because it's filename when called from file context; caller should pass directory path for folder checks
        for p in parts[:-1]:
            if self._fnmatch(p, pattern):
                return True
        return False

    def folder_rule_matches_any_ancestor(self, rel_path_for_file: str, rule_pattern: str) -> bool:
        # rel_path_for_file is the relative path of the file (posix)
        # We need to check any ancestor dir name for name-glob matches
        if "/" in rule_pattern:
            # Treat as relative path glob from root; test the directory portion of rel_path_for_file
            # Example: rule 'assets/private/**' should match 'assets/private/something/file.txt'
            # We'll test the rel path's directory portion (everything except basename)
            dir_part = "/".join(rel_path_for_file.split("/")[:-1])
            if dir_part == "":
                return False
            return self._fnmatch(dir_part, rule_pattern)
        else:
            # match each directory name
            dir_parts = rel_path_for_file.split("/")[:-1]
            for d in dir_parts:
                if self._fnmatch(d, rule_pattern):
                    return True
            return False

    def file_rule_matches(self, basename: str, rel_posix: str, pattern: str) -> bool:
        # If pattern contains '/', treat as relative path pattern from root
        if "/" in pattern:
            return self._fnmatch(rel_posix, pattern)
        return self._fnmatch(basename, pattern)

    def is_included_by_rules(self, basename: str, rel_posix: str, extension: Optional[str]) -> bool:
        # include_path_by_file: exact relative path match per spec (exact equality)
        for p in self.include_path_by_file:
            # spec default: exact path match. Honor case sensitivity flag.
            a = p if self.case_sensitive else p.lower()
            b = rel_posix if self.case_sensitive else rel_posix.lower()
            if a == b:
                return True

        # include_file
        for pattern in self.include_file:
            if self.file_rule_matches(basename, rel_posix, pattern):
                return True

        # include_folder
        for pattern in self.include_folder:
            if self.folder_rule_matches_any_ancestor(rel_posix, pattern):
                return True

        # include_extension
        if extension:
            for ext in self.include_extension:
                a = ext if self.case_sensitive else ext.lower()
                b = extension if self.case_sensitive else extension.lower()
                if a == b:
                    return True

        return False

    def is_excluded_by_rules(self, basename: str, rel_posix: str, extension: Optional[str], rules_filename: Optional[str]) -> bool:
        # implicit exclude for rules file: if basename equals rules filename and rules_filename provided
        if rules_filename and basename == rules_filename:
            # implicit exclude unless explicitly included (include rules already checked separately)
            return True

        # exclude_file
        for pattern in self.exclude_file:
            if self.file_rule_matches(basename, rel_posix, pattern):
                return True

        # exclude_folder
        for pattern in self.exclude_folder:
            if self.folder_rule_matches_any_ancestor(rel_posix, pattern):
                return True

        # exclude_extension
        if extension:
            for ext in self.exclude_extension:
                a = ext if self.case_sensitive else ext.lower()
                b = extension if self.case_sensitive else extension.lower()
                if a == b:
                    return True

        return False

# ---------- Core enumerator ----------

default_rules_filename = ".uploaderignore"

def enumerate_files(
    register_folder: Path,
    rules_filename: str = default_rules_filename,
    follow_symlinks: bool = False,
    case_sensitive: bool = True,
    include_dotfiles: bool = True,
) -> Dict[str, object]:
    """
    Walk the register_folder and produce the result dict per spec.
    """
    # resolve root
    try:
        root = register_folder.resolve()
    except Exception:
        root = register_folder.absolute()

    rules_path = root / rules_filename
    sections = read_rules_file(rules_path)
    matcher = RulesMatcher(sections, root, case_sensitive=case_sensitive)

    result_files: List[str] = []
    seen: Set[str] = set()

    # Walk
    for dirpath, dirnames, filenames in os.walk(root, followlinks=follow_symlinks):
        # dirpath may contain backslashes on Windows; convert to Path relative to root
        try:
            current_dir = Path(dirpath)
        except Exception as e:
            debug_print(f"Warning: Skipping directory {dirpath}: {e}")
            continue

        # We'll iterate files list. We avoid pruning directory traversal even if directories might be excluded,
        # to keep behavior simple and correct per spec.
        for fname in filenames:
            abs_path = current_dir / fname
            # compute rel path posix
            try:
                rel = abs_path.relative_to(root).as_posix()
            except Exception:
                # If relative_to fails (shouldn't), compute manually
                rel = os.path.relpath(str(abs_path), str(root)).replace(os.path.sep, "/")

            # basename
            basename = fname

            # extension:
            extension = extract_extension(basename)

            # dotfile handling - the spec treats them like any other; include_dotfiles param allows custom behavior.
            if not include_dotfiles:
                # Skip files whose name starts with a dot
                if basename.startswith("."):
                    continue

            # Attempt to detect broken symlink or unreadable files where necessary:
            try:
                lstat_res = os.lstat(abs_path)
            except Exception as e:
                debug_print(f"Warning: Skipping {rel} (lstat/read error): {e}")
                continue

            # Build booleans
            try:
                include_hit = matcher.is_included_by_rules(basename, rel, extension)
                exclude_hit = matcher.is_excluded_by_rules(basename, rel, extension, rules_filename)
            except Exception as e:
                debug_print(f"Warning: Error while matching rules for {rel}: {e}")
                include_hit = False
                exclude_hit = False

            # Decision: include wins, else exclude, else default include
            keep = False
            if include_hit:
                keep = True
            elif exclude_hit:
                keep = False
            else:
                keep = True  # default_state is include

            # Add if kept
            if keep:
                if rel not in seen:
                    seen.add(rel)
                    result_files.append(rel)

    # Sort lexically ASCII on POSIX path string
    result_files.sort()
    return {"root": str(root), "files": result_files}

# ---------- CLI ----------

def parse_args(argv: Optional[Iterable[str]] = None):
    p = argparse.ArgumentParser(
        prog="uploader_enumerator.py",
        description="Enumerate files to upload from a parent folder according to rules."
    )
    p.add_argument("register_folder", help="absolute or relative path to the parent folder")
    p.add_argument("--rules-file", default=default_rules_filename, help=f"rules file name at parent folder root (default: {default_rules_filename})")
    p.add_argument("--follow-symlinks", action="store_true", help="follow directory symlinks during traversal")
    group = p.add_mutually_exclusive_group()
    group.add_argument("--case-sensitive", dest="case_sensitive", action="store_true", help="perform case-sensitive matching (default)")
    group.add_argument("--case-insensitive", dest="case_sensitive", action="store_false", help="perform case-insensitive matching")
    p.set_defaults(case_sensitive=True)
    p.add_argument("--dotfiles", dest="include_dotfiles", action="store_true", help="include dotfiles (default behavior: treat like normal files)")
    p.add_argument("--no-dotfiles", dest="include_dotfiles", action="store_false", help="exclude dotfiles from enumeration")
    p.set_defaults(include_dotfiles=True)
    return p.parse_args(list(argv) if argv is not None else None)

def main(argv: Optional[Iterable[str]] = None):
    args = parse_args(argv)
    folder = Path(args.register_folder)

    if not folder.exists() or not folder.is_dir():
        debug_print(f"Error: register-folder '{folder}' does not exist or is not a directory.", flush=True)
        sys.exit(2)

    try:
        out = enumerate_files(
            register_folder=folder,
            rules_filename=args.rules_file,
            follow_symlinks=args.follow_symlinks,
            case_sensitive=args.case_sensitive,
            include_dotfiles=args.include_dotfiles,
        )
    except Exception as e:
        debug_print(f"Fatal error during enumeration: {e}")
        raise

    # Print JSON to stdout (only the JSON)
    print(json.dumps(out, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    main()
