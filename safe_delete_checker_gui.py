# safe_delete_checker_gui.py
#
# Minimal Tkinter GUI for Option A v1, using safe_delete_checker_core.py
#
# Features:
# - Pick Folder A + Folder B
# - Run compare in background thread (keeps UI responsive)
# - Indeterminate progress + status text + counts
# - Cancel support
# - Results in tabs (Missing / Size mismatch / Type mismatch / Mtime diff / Extras / Errors)
# - Export TXT report
#
# Notes:
# - This is intentionally plain v1 UI.
# - No dependencies beyond stdlib Tkinter.
#
# Usage:
#   Put this file next to safe_delete_checker_core.py
#   python safe_delete_checker_gui.py

from __future__ import annotations

import threading
import queue
import time
import os
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from safe_delete_checker_core import compare_trees, export_report_txt, CompareResults

def _fmt_mtime(ts: float) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
    except Exception:
        return str(ts)

class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()

        # Theme (do this AFTER the root exists, or you can get a stray "tk" window)
        try:
            ttk.Style(self).theme_use("clam")
        except Exception:
            pass

        self.title("Safe to Delete Checker (v1)")
        self.minsize(900, 600)

        # State
        self._worker: threading.Thread | None = None
        self._cancel_event = threading.Event()
        self._ui_queue: "queue.Queue[tuple[str, object]]" = queue.Queue()
        self._results: CompareResults | None = None
        self._running = False

        # Vars
        self.var_a = tk.StringVar(value="")
        self.var_b = tk.StringVar(value="")
        self.var_status = tk.StringVar(value="Ready.")
        self.var_progress = tk.StringVar(value="")
        self.var_pass = tk.StringVar(value="—")

        self._build_ui()
        self.after(100, self._drain_ui_queue)

    def _build_ui(self) -> None:
        # Top inputs frame
        frm = ttk.Frame(self, padding=12)
        frm.pack(fill="x")

        ttk.Label(frm, text="Folder A (original):").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=4)
        ent_a = ttk.Entry(frm, textvariable=self.var_a, width=80)
        ent_a.grid(row=0, column=1, sticky="ew", pady=4)
        ttk.Button(frm, text="Browse…", command=self._browse_a).grid(row=0, column=2, padx=(8, 0), pady=4)

        ttk.Label(frm, text="Folder B (destination):").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=4)
        ent_b = ttk.Entry(frm, textvariable=self.var_b, width=80)
        ent_b.grid(row=1, column=1, sticky="ew", pady=4)
        ttk.Button(frm, text="Browse…", command=self._browse_b).grid(row=1, column=2, padx=(8, 0), pady=4)

        frm.columnconfigure(1, weight=1)
        frm.rowconfigure(0, weight=0)
        frm.rowconfigure(1, weight=0)

        ent_a.configure(state="readonly")
        ent_b.configure(state="readonly")
        

        # Buttons + status
        frm2 = ttk.Frame(self, padding=(12, 0, 12, 12))
        frm2.pack(fill="x")

        self.btn_compare = ttk.Button(frm2, text="Compare", command=self._start_compare)
        self.btn_compare.pack(side="left")

        self.btn_cancel = ttk.Button(frm2, text="Cancel", command=self._cancel, state="disabled")
        self.btn_cancel.pack(side="left", padx=(8, 0))

        self.btn_export = ttk.Button(frm2, text="Export Report…", command=self._export, state="disabled")
        self.btn_export.pack(side="left", padx=(8, 0))

        ttk.Label(frm2, textvariable=self.var_pass, font=("Segoe UI", 11, "bold")).pack(side="left", padx=(20, 0))

        # Progress + status line
        frm3 = ttk.Frame(self, padding=(12, 0, 12, 12))
        frm3.pack(fill="x")

        self.pbar = ttk.Progressbar(frm3, mode="indeterminate")
        self.pbar.pack(side="left", fill="x", expand=True)

        ttk.Label(frm3, textvariable=self.var_progress).pack(side="left", padx=(10, 0))
        ttk.Label(self, textvariable=self.var_status, padding=(12, 0, 12, 8)).pack(fill="x")

        # Notebook for results
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=12, pady=12)

        self.tab_summary = self._make_tab("Summary")
        self.tab_missing = self._make_tab("Missing in B")
        self.tab_size = self._make_tab("Size mismatch")
        self.tab_type = self._make_tab("Type mismatch")
        self.tab_mtime = self._make_tab("Timestamp diffs (info)")
        self.tab_extras = self._make_tab("Extras in B (info)")
        self.tab_errors = self._make_tab("Errors")

        # Summary content (simple labels)
        self.lbl_summary = ttk.Label(self.tab_summary, text="Run a comparison to see results.", justify="left")
        self.lbl_summary.pack(anchor="w", padx=10, pady=10)

        # Lists
        self.lst_missing = self._make_listbox(self.tab_missing)
        self.lst_type = self._make_listbox(self.tab_type)
        self.lst_extras = self._make_listbox(self.tab_extras)

        # For tuple-based lists, use a Treeview
        self.tree_size = self._make_treeview(
            self.tab_size,
            columns=("rel", "size_a", "size_b"),
            headings=("Path", "A size (bytes)", "B size (bytes)"),
        )
        self.tree_mtime = self._make_treeview(
            self.tab_mtime,
            columns=("rel", "mtime_a", "mtime_b"),
            headings=("Path", "A mtime", "B mtime"),
        )
        self.tree_errors = self._make_treeview(
            self.tab_errors,
            columns=("where", "error"),
            headings=("Path/Item", "Error"),
        )

    def _make_tab(self, title: str) -> ttk.Frame:
        tab = ttk.Frame(self.nb)
        self.nb.add(tab, text=title)
        return tab

    def _make_listbox(self, parent: ttk.Frame) -> tk.Listbox:
        frm = ttk.Frame(parent)
        frm.pack(fill="both", expand=True, padx=10, pady=10)

        yscroll = ttk.Scrollbar(frm, orient="vertical")
        yscroll.pack(side="right", fill="y")

        lst = tk.Listbox(frm, yscrollcommand=yscroll.set)
        lst.pack(side="left", fill="both", expand=True)
        yscroll.config(command=lst.yview)
        return lst

    def _make_treeview(self, parent: ttk.Frame, *, columns: tuple[str, ...], headings: tuple[str, ...]) -> ttk.Treeview:
        frm = ttk.Frame(parent)
        frm.pack(fill="both", expand=True, padx=10, pady=10)

        tree = ttk.Treeview(frm, columns=columns, show="headings")
        for col, head in zip(columns, headings):
            tree.heading(col, text=head)
            tree.column(col, width=200, anchor="w")
        tree.column(columns[0], width=500, anchor="w")

        yscroll = ttk.Scrollbar(frm, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=yscroll.set)

        tree.pack(side="left", fill="both", expand=True)
        yscroll.pack(side="right", fill="y")

        return tree

    def _browse_a(self) -> None:
        path = filedialog.askdirectory(title="Select Folder A (original)")
        if path:
            self.var_a.set(path)

    def _browse_b(self) -> None:
        path = filedialog.askdirectory(title="Select Folder B (destination)")
        if path:
            self.var_b.set(path)

    def _set_running(self, running: bool) -> None:
        self._running = running
        if running:
            self.btn_compare.config(state="disabled")
            self.btn_cancel.config(state="normal")
            self.btn_export.config(state="disabled")
            self.pbar.start(10)
            self.var_pass.set("Running…")
        else:
            self.btn_compare.config(state="normal")
            self.btn_cancel.config(state="disabled")
            self.pbar.stop()
            # export enabled only if results exist
            self.btn_export.config(state=("normal" if self._results is not None else "disabled"))

    def _clear_results_ui(self) -> None:
        self.lbl_summary.config(text="Running…")
        for lst in (self.lst_missing, self.lst_type, self.lst_extras):
            lst.delete(0, tk.END)
        for tree in (self.tree_size, self.tree_mtime, self.tree_errors):
            for item in tree.get_children():
                tree.delete(item)

    def _start_compare(self) -> None:
        if self._running:
            return

        root_a = self.var_a.get().strip()
        root_b = self.var_b.get().strip()

        if not root_a or not root_b:
            messagebox.showwarning("Missing folders", "Please select both Folder A and Folder B.")
            return
        if not os.path.isdir(root_a) or not os.path.isdir(root_b):
            messagebox.showerror("Invalid folders", "One or both selected paths are not valid folders.")
            return

        self._results = None
        self._cancel_event.clear()
        self._clear_results_ui()
        self.var_status.set("Starting…")
        self.var_progress.set("")
        self._set_running(True)

        def progress(stage: str, current: int, total: int, message: str) -> None:
            # Send progress updates to the UI thread
            self._ui_queue.put(("progress", (stage, current, total, message)))

        def worker() -> None:
            try:
                res = compare_trees(
                    root_a,
                    root_b,
                    mtime_tolerance_seconds=2.0,
                    cancel_event=self._cancel_event,
                    on_progress=progress,
                    follow_symlinks=False,
                    include_extras_in_b=True,
                )
                self._ui_queue.put(("done", res))
            except Exception as e:
                self._ui_queue.put(("error", str(e)))

        self._worker = threading.Thread(target=worker, daemon=True)
        self._worker.start()

    def _cancel(self) -> None:
        if self._running:
            self._cancel_event.set()
            self.var_status.set("Cancel requested…")

    def _export(self) -> None:
        if not self._results:
            return
        txt = export_report_txt(self._results)

        path = filedialog.asksaveasfilename(
            title="Save report",
            defaultextension=".txt",
            filetypes=[("Text file", "*.txt"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(txt)
            messagebox.showinfo("Saved", f"Report saved:\n{path}")
        except Exception as e:
            messagebox.showerror("Save failed", str(e))

    def _drain_ui_queue(self) -> None:
        # Process UI events from worker
        try:
            while True:
                kind, payload = self._ui_queue.get_nowait()
                if kind == "progress":
                    stage, current, total, message = payload  # type: ignore[misc]
                    self.var_status.set(message)

                    # Show a concise progress summary on the right
                    if total and total > 0:
                        self.var_progress.set(f"{stage}: {current:,}/{total:,}")
                    else:
                        self.var_progress.set(f"{stage}: {current:,}")

                elif kind == "done":
                    res: CompareResults = payload  # type: ignore[assignment]
                    self._results = res
                    self._set_running(False)
                    if self._cancel_event.is_set():
                        self.var_pass.set("Canceled")
                        self.var_status.set("Canceled.")
                    else:
                        self._render_results(res)
                        self.var_status.set("Done.")
                    self.btn_export.config(state=("normal" if self._results is not None else "disabled"))

                elif kind == "error":
                    msg: str = payload  # type: ignore[assignment]
                    self._set_running(False)
                    self.var_pass.set("Error")
                    self.var_status.set("Error.")
                    messagebox.showerror("Compare failed", msg)

                else:
                    # Unknown message
                    pass
        except queue.Empty:
            pass

        self.after(100, self._drain_ui_queue)

    def _render_results(self, r: CompareResults) -> None:
        # PASS label
        self.var_pass.set("PASS ✅" if r.pass_ok else "FAIL ❌")

        # Summary text
        summary = []
        summary.append("This tool checks that every file in Folder A exists in Folder B")
        summary.append("with the same relative path and file size. It does not modify files.")
        summary.append(f"Elapsed time: {r.elapsed_seconds:.2f} seconds")
        summary.append("")
        summary.append("")
        summary.append(f"Folder A: {r.root_a}")
        summary.append(f"Folder B: {r.root_b}")
        summary.append("")
        summary.append(f"Total files (A): {r.total_a_files:,}")
        summary.append(f"Total files (B): {r.total_b_files:,}")
        summary.append("")
        summary.append(f"Missing in B: {len(r.missing_in_b):,}")
        summary.append(f"Type mismatches: {len(r.type_mismatch):,}")
        summary.append(f"Size mismatches: {len(r.size_mismatch):,}")
        summary.append(f"Errors (A): {len(r.errors_a):,}")
        summary.append(f"Errors (B): {len(r.errors_b):,}")
        summary.append(f"Timestamp diffs (info): {len(r.mtime_diff):,}")
        summary.append(f"Extras in B (info): {len(r.extras_in_b):,}")
        self.lbl_summary.config(text="\n".join(summary))

        # Fill list tabs
        self._fill_listbox(self.lst_missing, r.missing_in_b)
        self._fill_listbox(self.lst_type, r.type_mismatch)
        self._fill_listbox(self.lst_extras, r.extras_in_b)

        # Fill tree tabs
        self._fill_tree_size(self.tree_size, r.size_mismatch)
        self._fill_tree_mtime(self.tree_mtime, r.mtime_diff)
        self._fill_tree_errors(self.tree_errors, r.errors_a, r.errors_b)

        # Auto-select summary tab
        self.nb.select(self.tab_summary)

    @staticmethod
    def _fill_listbox(lst: tk.Listbox, items: list[str]) -> None:
        lst.delete(0, tk.END)
        for s in items:
            lst.insert(tk.END, s)

    @staticmethod
    def _fill_tree_size(tree: ttk.Treeview, items: list[tuple[str, int, int]]) -> None:
        for row in tree.get_children():
            tree.delete(row)
        for rel, sa, sb in items:
            tree.insert("", "end", values=(rel, f"{sa}", f"{sb}"))

    @staticmethod
    def _fill_tree_mtime(tree: ttk.Treeview, items: list[tuple[str, float, float]]) -> None:
        for row in tree.get_children():
            tree.delete(row)
        for rel, ta, tb in items:
            tree.insert("", "end", values=(rel, _fmt_mtime(ta), _fmt_mtime(tb)))


    @staticmethod
    def _fill_tree_errors(tree: ttk.Treeview, errors_a: list[tuple[str, str]], errors_b: list[tuple[str, str]]) -> None:
        for row in tree.get_children():
            tree.delete(row)
        for p, err in errors_a:
            tree.insert("", "end", values=(f"A: {p}", err))
        for p, err in errors_b:
            tree.insert("", "end", values=(f"B: {p}", err))


if __name__ == "__main__":
    app = App()
    app.mainloop()


