# safe_delete_checker_core.py
#
# Core engine for Option A v1:
# "Is everything in Folder A present in Folder B with the same relative path and size?"
#
# - Read-only: never modifies files
# - PASS/FAIL based on: missing, type mismatch, size mismatch, errors
# - Timestamp diffs are informational only
#
# Tkinter-friendly:
# - supports progress callback
# - supports cancel via threading.Event

from __future__ import annotations

import os
import sys
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Set, Tuple

ProgressFn = Callable[[str, int, int, str], None]  # stage, current, total, message


@dataclass(frozen=True)
class FileMeta:
    size: int
    mtime: float  # seconds (stat().st_mtime)


@dataclass
class CompareResults:
    root_a: str
    root_b: str

    total_a_files: int
    total_b_files: int

    ok: List[str]
    missing_in_b: List[str]
    size_mismatch: List[Tuple[str, int, int]]
    type_mismatch: List[str]
    mtime_diff: List[Tuple[str, float, float]]

    extras_in_b: List[str]

    errors_a: List[Tuple[str, str]]
    errors_b: List[Tuple[str, str]]

    elapsed_seconds: float   # 👈 ADD THIS
    pass_ok: bool



def _is_windows() -> bool:
    return sys.platform.startswith("win")


def _normalize_rel(rel: str) -> str:
    # Always display forward slashes
    rel = rel.replace("\\", "/")
    # Normalize Windows keys to lower-case to avoid case weirdness
    if _is_windows():
        rel = rel.lower()
    return rel


def _safe_relpath(path: str, root: str) -> str:
    # relpath can raise on weird inputs; keep it safe
    try:
        rel = os.path.relpath(path, root)
    except Exception:
        # fallback: if relpath fails, just use basename
        rel = os.path.basename(path)
    return _normalize_rel(rel)


def _default_progress(_: str, __: int, ___: int, ____: str) -> None:
    return


def scan_tree_files(
    root: str,
    *,
    stage: str,
    cancel_event: Optional[threading.Event] = None,
    on_progress: Optional[ProgressFn] = None,
    follow_symlinks: bool = False,
) -> Tuple[Dict[str, FileMeta], Set[str], List[Tuple[str, str]]]:
    """
    Recursively scan `root` and return:
      - files_map: dict[rel_key] -> FileMeta(size, mtime)
      - dirs_set: set[rel_key] for directories (helps detect file-vs-dir mismatches)
      - errors: list of (path_or_rel, error_str)

    Notes:
      - follow_symlinks default OFF to avoid loops/surprises
      - progress callback gets stage + file_count scanned; total is unknown (0)
    """
    on_progress = on_progress or _default_progress
    cancel_event = cancel_event or threading.Event()

    files: Dict[str, FileMeta] = {}
    dirs: Set[str] = set()
    errors: List[Tuple[str, str]] = []

    root = os.path.abspath(root)

    # include root dir itself as "" (optional), not needed for v1
    # dirs.add("")  # not used

    file_count = 0

    def walk_dir(abs_dir: str) -> None:
        nonlocal file_count

        if cancel_event.is_set():
            return

        try:
            with os.scandir(abs_dir) as it:
                for entry in it:
                    if cancel_event.is_set():
                        return

                    try:
                        # Avoid stat() unless needed; but we need it for file meta.
                        # Using entry.is_file/is_dir is fast and may use cached stat.
                        if entry.is_dir(follow_symlinks=follow_symlinks):
                            rel = _safe_relpath(entry.path, root)
                            dirs.add(rel)
                            walk_dir(entry.path)
                        elif entry.is_file(follow_symlinks=follow_symlinks):
                            st = entry.stat(follow_symlinks=follow_symlinks)
                            rel = _safe_relpath(entry.path, root)
                            files[rel] = FileMeta(size=int(st.st_size), mtime=float(st.st_mtime))
                            file_count += 1
                            if (file_count % 250) == 0:
                                on_progress(stage, file_count, 0, f"Scanned {file_count:,} files…")
                        else:
                            # Skip special types (symlink to nowhere, sockets, etc.)
                            continue
                    except Exception as e:
                        # record per-entry errors; continue scanning
                        rel = _safe_relpath(entry.path, root)
                        errors.append((rel, str(e)))
        except Exception as e:
            # record directory-level error
            rel = _safe_relpath(abs_dir, root)
            errors.append((rel, str(e)))

    on_progress(stage, 0, 0, f"Scanning: {root}")
    walk_dir(root)
    on_progress(stage, file_count, 0, f"Scan complete: {file_count:,} files")

    return files, dirs, errors


def compare_trees(
        root_a: str,
    root_b: str,
    *,
    mtime_tolerance_seconds: float = 2.0,
    cancel_event: Optional[threading.Event] = None,
    on_progress: Optional[ProgressFn] = None,
    follow_symlinks: bool = False,
    include_extras_in_b: bool = True,
) -> CompareResults:
    start_ts = time.perf_counter()
    """
    Compare Folder A against Folder B using v1 rules:
      FAIL buckets: missing_in_b, type_mismatch, size_mismatch, errors_a, errors_b
      INFO bucket: mtime_diff
      OK: path exists and size matches (mtime ignored for pass/fail)

    Returns CompareResults (lists are rel keys using forward slashes).
    """
    on_progress = on_progress or _default_progress
    cancel_event = cancel_event or threading.Event()

    root_a = os.path.abspath(root_a)
    root_b = os.path.abspath(root_b)

    # Scan both trees
    a_files, a_dirs, errors_a = scan_tree_files(
        root_a, stage="scan_a", cancel_event=cancel_event, on_progress=on_progress, follow_symlinks=follow_symlinks
    )
    
    elapsed_seconds = time.perf_counter() - start_ts
       
    if cancel_event.is_set():
        # return partial but consistent result
        return CompareResults(
            root_a, root_b, len(a_files), 0,
            ok=[], missing_in_b=[], size_mismatch=[], type_mismatch=[], mtime_diff=[],
            extras_in_b=[],
            errors_a=errors_a, errors_b=[], elapsed_seconds=elapsed_seconds,
            pass_ok=False
        )

    b_files, b_dirs, errors_b = scan_tree_files(
        root_b, stage="scan_b", cancel_event=cancel_event, on_progress=on_progress, follow_symlinks=follow_symlinks
    )
    if cancel_event.is_set():
        return CompareResults(
            root_a, root_b, len(a_files), len(b_files),
            ok=[], missing_in_b=[], size_mismatch=[], type_mismatch=[], mtime_diff=[],
            extras_in_b=[],
            errors_a=errors_a, errors_b=errors_b,elapsed_seconds=elapsed_seconds,
            pass_ok=False
        )

    # Compare A -> B
    ok: List[str] = []
    missing_in_b: List[str] = []
    size_mismatch: List[Tuple[str, int, int]] = []
    type_mismatch: List[str] = []
    mtime_diff: List[Tuple[str, float, float]] = []
    extras_in_b: List[str] = []

    keys_a = list(a_files.keys())
    total = len(keys_a)
    on_progress("compare", 0, total, f"Comparing {total:,} files…")

    for i, rel in enumerate(keys_a, start=1):
        if cancel_event.is_set():
            break

        a_meta = a_files[rel]

        # Determine what exists in B at that relative path
        b_meta = b_files.get(rel)

        if b_meta is None:
            # Is there a directory with that name in B?
            if rel in b_dirs:
                type_mismatch.append(rel)
            else:
                missing_in_b.append(rel)
        else:
            # Both are files
            if a_meta.size != b_meta.size:
                size_mismatch.append((rel, a_meta.size, b_meta.size))
            else:
                ok.append(rel)
                # informational mtime difference
                if abs(a_meta.mtime - b_meta.mtime) > float(mtime_tolerance_seconds):
                    mtime_diff.append((rel, a_meta.mtime, b_meta.mtime))

        if (i % 500) == 0 or i == total:
            on_progress("compare", i, total, f"Compared {i:,}/{total:,} files…")

    if include_extras_in_b and not cancel_event.is_set():
        # extras in B are those file keys not present in A's file map
        a_set = set(a_files.keys())
        extras_in_b = sorted([k for k in b_files.keys() if k not in a_set])

    # PASS/FAIL per locked v1 rules
    pass_ok = (
        (not cancel_event.is_set())
        and len(missing_in_b) == 0
        and len(type_mismatch) == 0
        and len(size_mismatch) == 0
        and len(errors_a) == 0
        and len(errors_b) == 0
    )

    on_progress("compare", total, total, "Compare complete.")
    
    elapsed_seconds = time.perf_counter() - start_ts

    return CompareResults(
        root_a=root_a,
        root_b=root_b,
        total_a_files=len(a_files),
        total_b_files=len(b_files),
        ok=ok,
        missing_in_b=missing_in_b,
        size_mismatch=size_mismatch,
        type_mismatch=type_mismatch,
        mtime_diff=mtime_diff,
        extras_in_b=extras_in_b,
        errors_a=errors_a,
        errors_b=errors_b,
        elapsed_seconds=elapsed_seconds,  
        pass_ok=pass_ok,
    )



def export_report_txt(results: CompareResults) -> str:
    """
    Create a human-readable TXT report (string). Caller writes to disk.
    """
    lines: List[str] = []
    lines.append("SAFE TO DELETE CHECKER — REPORT (v1)")
    lines.append("")
    lines.append(f"Folder A: {results.root_a}")
    lines.append(f"Folder B: {results.root_b}")
    lines.append("")
    lines.append(f"Total files (A): {results.total_a_files:,}")
    lines.append(f"Total files (B): {results.total_b_files:,}")
    lines.append("")
    lines.append(f"PASS: {'YES' if results.pass_ok else 'NO'}")
    lines.append("")
    lines.append(f"Missing in B: {len(results.missing_in_b):,}")
    lines.append(f"Type mismatches: {len(results.type_mismatch):,}")
    lines.append(f"Size mismatches: {len(results.size_mismatch):,}")
    lines.append(f"Errors (A): {len(results.errors_a):,}")
    lines.append(f"Errors (B): {len(results.errors_b):,}")
    lines.append(f"Timestamp differences (info): {len(results.mtime_diff):,}")
    lines.append(f"Extras in B (info): {len(results.extras_in_b):,}")
    lines.append("")
    lines.append(f"Elapsed time: {results.elapsed_seconds:.2f} seconds")
    lines.append("")

    def fmt(ts: float) -> str:
        try:
            return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
        except Exception:
            return str(ts)

    def dump_list(title: str, items: List[str], limit: int = 5000) -> None:
        lines.append(title)
        if not items:
            lines.append("  (none)")
        else:
            show = items[:limit]
            for rel in show:
                lines.append(f"  {rel}")
            if len(items) > limit:
                lines.append(f"  ... ({len(items)-limit:,} more)")
        lines.append("")

    dump_list("MISSING IN B:", results.missing_in_b)
    dump_list("TYPE MISMATCH:", results.type_mismatch)
    lines.append("SIZE MISMATCH:")
    if not results.size_mismatch:
        lines.append("  (none)")
    else:
        for rel, sa, sb in results.size_mismatch[:5000]:
            lines.append(f"  {rel}  (A={sa} bytes, B={sb} bytes)")
        if len(results.size_mismatch) > 5000:
            lines.append(f"  ... ({len(results.size_mismatch)-5000:,} more)")
    lines.append("")

    lines.append("TIMESTAMP DIFFERENCES (info):")
    if not results.mtime_diff:
        lines.append("  (none)")
    else:
        for rel, ta, tb in results.mtime_diff[:5000]:
            lines.append(f"  {rel}  (A mtime={fmt(ta)}, B mtime={fmt(tb)})")
        if len(results.mtime_diff) > 5000:
            lines.append(f"  ... ({len(results.mtime_diff)-5000:,} more)")
    lines.append("")

    dump_list("EXTRAS IN B (info):", results.extras_in_b)

    lines.append("ERRORS (A):")
    if not results.errors_a:
        lines.append("  (none)")
    else:
        for p, err in results.errors_a[:2000]:
            lines.append(f"  {p}  :: {err}")
        if len(results.errors_a) > 2000:
            lines.append(f"  ... ({len(results.errors_a)-2000:,} more)")
    lines.append("")

    lines.append("ERRORS (B):")
    if not results.errors_b:
        lines.append("  (none)")
    else:
        for p, err in results.errors_b[:2000]:
            lines.append(f"  {p}  :: {err}")
        if len(results.errors_b) > 2000:
            lines.append(f"  ... ({len(results.errors_b)-2000:,} more)")
    lines.append("")

    lines.append("NOTES:")
    lines.append("- PASS/FAIL is based on: missing, type mismatch, size mismatch, and read errors.")
    lines.append("- Timestamp differences are informational only; copy tools may not preserve mtimes.")
    lines.append("- This tool does not modify files.")
    lines.append("")

    return "\n".join(lines)
