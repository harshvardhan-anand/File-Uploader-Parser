# uploader_enumerator.py — Quick Guide

**What it does (one line)**  
Walks a folder and prints a JSON object listing files (relative POSIX paths) to upload, honoring simple include/exclude rules from a single rules file (default `.uploaderignore`).

---

## Quick CLI
```bash
python uploader_enumerator.py /path/to/register-folder
python uploader_enumerator.py /path/to/register-folder --rules-file .uploaderignore --case-insensitive --follow-symlinks
```

## Output (stdout)
```json
{
  "root": "/absolute/resolved/path/to/register-folder",
  "files": [
    "relative/path/to/file1",
    "relative/path/to/file2",
    ...
  ]
}
```
- `root` is `Path(...).resolve()` as a string.  
- `files` use POSIX separators and have no leading `./`. Results are lexicographically sorted.

## Important flags
- `--rules-file` : filename looked for at the root (default `.uploaderignore`).
- `--follow-symlinks` : follow directory symlinks during traversal.
- `--case-sensitive` / `--case-insensitive` : choose matching mode (default is case-sensitive).
- `--dotfiles` / `--no-dotfiles` : include or exclude files that start with `.` (default includes them).

## Rules semantics (concise)
- Rules file supports sections:
  - `exclude_folder`, `exclude_file`, `exclude_extension`
  - `include_folder`, `include_file`, `include_extension`, `include_path_by_file`
- Matching uses `fnmatch.fnmatchcase`. In case-insensitive mode both pattern and candidate are lowercased before matching.
- **Include rules win** over exclude rules. If a path matches an include rule it will be kept even if it also matches an exclude rule.
- `include_path_by_file` requires exact relative-path equality (honors case option).
- The rules file itself is implicitly excluded from results unless explicitly included.

## Traversal & behavior notes
- Traversal does **not** prune excluded directories; files are filtered per-file.
- Broken symlinks and permission errors are skipped; warnings are printed to `stderr` (JSON stays clean on `stdout`).
- Non-UTF8 filenames are attempted; decode errors cause the file to be skipped with a warning.
- Default behavior is to include everything unless excluded; include rules can force-keep.

## When to use this script
- Prepare a predictable list of files for an uploader that must respect simple include/exclude rules stored at the folder root.
- CI steps where you need a canonical, reproducible list of upload candidates (POSIX paths, sorted).

## Minimal example
Run:
```bash
python uploader_enumerator.py ./my-project --rules-file .uploaderignore --case-insensitive
```
Result: JSON printed to stdout ready for consumption by whatever upload step you wire up.

---

## Implementation notes (for maintainers)
- Rules parsed by `read_rules_file()` return named sections; unknown sections ignored.
- `RulesMatcher` centralizes matching logic; it distinguishes path-globs (contain `/`) from name-globs.
- `extract_extension()` implements the extension rules (handles leading-dot filenames).
- Default rules filename: `.uploaderignore`. The script implicitly excludes that file from results unless overridden by include rules.

---

**Checklist**
- [x] Analyze requirements
- [x] Read `uploader_enumerator.py`
- [x] Explain its functionality in markdown concisely
- [x] Write `quick-guide.md` with the result
