
# Functional spec — “Uploader File Enumerator”

## Purpose

Given a **parent folder** path, recursively scan its contents and return:

1. the **parent folder name** used to register, and
2. a list (array) of **relative file paths** that should be uploaded to a remote server.

Filtering is controlled by a per-project rules file (an “ignore” file). **Include rules always take precedence over exclude rules.**

---

## CLI interface

* Executable name (suggested): `uploader_enumerator.py`
* Required argument:

  * `register-folder` (positional): absolute or relative path to the parent folder.
* Optional arguments (suggested):

  * `--rules-file <name>`: file name of the rules file to read from the parent folder root. Default: `.uploaderignore`
  * `--follow-symlinks` (flag): if present, follow directory symlinks; otherwise ignore them.
  * `--case-sensitive` / `--case-insensitive`: how to compare file/folder names and extensions. Default: **case-sensitive** (platform-independent default).
  * `--dotfiles`: include dotfiles by default unless they’re excluded. Default behavior: treat dotfiles like any other files (no special handling).

**Output (return value / JSON print):**

```json
{
  "root": "<normalized parent folder path as provided/resolved>",
  "files": ["relative/path/to/file1", "relative/path/to/file2", ...]
}
```

* `files` are **POSIX-style relative paths** from the parent folder (use `/` as the separator), with no leading `./`.

---

## Rules file

* File name (default): `.uploaderignore`
* Location: **only at the parent folder root** (single file).
  (You can later extend to support hierarchical rules if needed; for now, spec uses a single top-level file for simplicity and determinism.)

### File format

INI-like sections. Lines starting with `#` are comments; blank lines are ignored.

Required sections (all optional to include, but recognized if present):

1. `[exclude_folder]` — list of **folder names or glob patterns** to exclude
2. `[exclude_file]` — list of **file names or glob patterns** to exclude
3. `[exclude_extension]` — list of **extensions** (without dot), e.g. `tmp`, `log`
4. `[include_folder]` — list of **folder names or glob patterns** to force-include
5. `[include_file]` — list of **file names or glob patterns** to force-include
6. `[include_extension]` — list of **extensions** (without dot) to force-include
7. `[include_path_by_file]` — list of **relative file paths** (from root) to force-include

* **Glob patterns** use standard `fnmatch` semantics (`*`, `?`, `[]` sets).
* Folder rules match **directory names** (not entire paths) unless a slash `/` appears—if a slash appears, treat it as a relative path pattern from root.
* File rules match **file basenames** unless a slash is present—then treat as a relative path pattern.
* Extensions are matched by a file’s **final suffix after the last dot**, ignoring leading dots. Example: `.env` has extension `env`; `archive.tar.gz` has extension `gz`.

### Example `.uploaderignore`

```ini
# Folders to exclude (names or relative path globs)
[exclude_folder]
build
dist
.venv
**/__pycache__

# Files to exclude (names or path globs)
[exclude_file]
Thumbs.db
.DS_Store
*.tmp
*.bak

# Extensions to exclude (no dot)
[exclude_extension]
log
tmp

# Force-includes (override any exclude)
[include_folder]
docs
examples

[include_file]
Makefile
README.md
*important*.txt

[include_extension]
md
csv

# Force-include by exact relative path (from root)
[include_path_by_file]
config/prod.env
scripts/deploy.sh
```

---

## Traversal & matching rules

1. **Traversal**

   * Recursively walk the parent folder - perform this iteratively.
   * By default, **do not** follow symlinked directories (unless `--follow-symlinks` is used). Always include symlinked files as files (unless excluded).
   * Generate relative paths using POSIX separators (`/`).

2. **Decision order & precedence**

   * Compute three booleans for each candidate file (not directories): `is_included_by_rule`, `is_excluded_by_rule`, `default_state`.

     * `default_state` is “include” (files are included unless excluded).
   * **Include precedence overrides exclude precedence.**

     * If any **include rule matches**, the file is **included**, even if it matches excludes.
     * Else, if any **exclude rule matches**, the file is **excluded**.
     * Else, include by default.

3. **What can cause an include match?**

   * `[include_path_by_file]`: exact relative path match (string equality or path-glob if you choose; spec defaults to **exact** path match**).
   * `[include_file]`: basename or path-glob matches.
   * `[include_folder]`: any ancestor dir name matches (by name or path-glob).
   * `[include_extension]`: file’s extension matches an entry.

4. **What can cause an exclude match?**

   * `[exclude_file]`: basename or path-glob matches.
   * `[exclude_folder]`: any ancestor dir name matches (by name or path-glob).
   * `[exclude_extension]`: file’s extension matches an entry.

5. **Folder rules: name vs path**

   * If the rule contains `/`, treat it as a **relative path glob** from root (e.g., `assets/private/**`).
   * Otherwise, treat it as a **directory-name** glob compared against each directory in the file’s ancestor path (e.g., `__pycache__` matches any `__pycache__` anywhere).

6. **Case sensitivity**

   * By default, matches are **case-sensitive**.
     If `--case-insensitive` is set, convert both candidate strings and patterns to lowercase before matching.

7. **Hidden files & dotfolders**

   * Treated like any others; they may be included or excluded via rules.

8. **Ignored directories during traversal**

   * For performance, you may **prune** excluded directories early *if and only if* there is no include rule that could match files inside them (e.g., `include_extension=md`).
   * To keep behavior simple and correct, recommended approach: **do not prune** based on excludes alone; instead, enumerate everything and then filter with precedence (or prune only when safe).

9. **Malformed rules handling**

   * Unknown sections are ignored.
   * Trim whitespace. Empty lines are ignored.
   * Duplicate entries are allowed; treat as a set.
   * If the rules file is missing, proceed as if **all lists are empty** (include everything).

---

## Matching details (normative)

* Use Python’s `fnmatch.fnmatchcase()` for glob sections that support patterns.
* Extract extension with:

  * If basename has at least one `.`, extension is text **after the last dot**, excluding the dot.
  * Files that start with a dot and have no other dot (e.g., `.gitignore`) have extension equal to the part after the leading dot (`gitignore`).
* Relative paths use `/` (even on Windows). Normalize with `Path(...).as_posix()`.

---

## Output contract

* Return a **tuple-like** or a small object with:

  * `root`: the **exact normalized path** of the parent folder used for traversal (absolute or resolved realpath—pick one and document it; recommended: absolute resolved path).
  * `files`: **sorted** list of **unique** relative file paths to upload. Sorting order: lexical ASCII on the POSIX path string.

Example output:

```json
{
  "root": "/home/alex/project",
  "files": [
    "README.md",
    "config/prod.env",
    "docs/intro.md",
    "scripts/deploy.sh",
    "src/main.py"
  ]
}
```

---

## Edge cases to cover

* Files inside an excluded folder but matching an include extension → **included** (include wins).
* A file explicitly listed in `[include_path_by_file]` → **always included**, regardless of folder/file/extension excludes.
* A directory named like a file pattern (e.g., `build.tmp/`) → folder rules apply by folder logic; file rules don’t apply to directories.
* Broken symlinks: treat as **files** if they are symlinked files and lstat shows a link; if reading fails, you may skip with a warning (implementation detail).
* Non-UTF8 filenames: attempt to process; if not possible, skip with a warning (implementation detail).
* Permission errors while walking: skip subtrees you can’t enter; keep going (implementation detail).
* Rules file itself should **not** be auto-excluded unless excluded by rules; you may choose to implicitly exclude it to avoid accidental upload (recommended: implicitly exclude the rules file name itself—document this if you do).

---

## Suggested high-level algorithm (implementation outline in Python)

1. Parse CLI args.
2. Resolve `root = Path(register_folder).resolve()`.
3. Load rules from `root / rules_file_name` (if present), building sets/lists for each section.
4. Create helper functions:

   * `matches_folder_rule(path_parts, rule)` → bool
   * `matches_file_rule(basename, rel_path, rule)` → bool
   * `has_extension(basename, ext)` → bool
5. Walk filesystem from `root` (e.g., `os.walk`).
6. For each **file** found:

   * Compute `rel = file_path.relative_to(root).as_posix()`
   * Compute booleans:

     * `include_hit` if any include rule matches (path-by-file exact, file glob, folder rule on any ancestor, or extension)
     * `exclude_hit` if any exclude rule matches (file glob, folder on any ancestor, or extension)
   * Decision: if `include_hit` → keep; else if `exclude_hit` → drop; else keep.
7. Sort the kept list lexically, dedupe.
8. Emit JSON (or return values) with `root` and `files`.

---

## Examples

### Example A: include extension wins over excluded folder

Rules:

```ini
[exclude_folder]
build

[include_extension]
md
```

Tree:

```
build/
  notes.md
src/
  main.py
```

Result: `["build/notes.md", "src/main.py"]` — `notes.md` is included because `md` is force-included.

### Example B: include by exact path

Rules:

```ini
[exclude_folder]
secret

[include_path_by_file]
secret/whitelist.csv
```

Result includes `secret/whitelist.csv` despite `secret` being excluded.

### Example C: exclude extension unless force-included by file

Rules:

```ini
[exclude_extension]
log

[include_file]
*.log.important
```

`app.log` → excluded.
`app.log.important` → included by file pattern.

---

## Testing checklist

* No rules file → returns all files.
* Mixed includes/excludes across folders, files, extensions.
* Case-insensitive mode behaves as documented.
* Symlink handling respects `--follow-symlinks`.
* Sorting and path normalization.
* Rules file present but empty sections.
* Malformed section names are ignored without crash.

---
Use modular programming with fnctions and variable names need to be self explanatory

Also create a test case file where you will test all functionality in test_cases folder you can create any file/folder to perform the test and later on if someone execute the testcase it will use samme folder to validate the funtionality