#! python3
# coding: utf-8
"""FFTA US->JP localization -- a small window around build.py.

    python gui.py

This is also the entry point of the Windows release (packaging/windows/),
which lets people build without installing Python or typing commands.  It
uses only tkinter, which ships with Python, and does exactly what build.py
does: the same input hashes, the same chain, the same output hash.

No ROM is included with this project, and this tool never downloads one.

This file is part of the FFTA US->JP localization project.
Licensed under the GNU General Public License v3 (see LICENSE).
"""
from __future__ import annotations

import os
import queue
import sys
import tempfile
import threading
from pathlib import Path

import build

TITLE = f"FFTA 日本語化ビルダー ({build.RELEASE})"

# What a picked file is, in the words shown next to it.
LABELS = {
    build.PRISTINE_US: "US版（対応する版です）",
    build.PRISTINE_JP: "JP版（対応する版です）",
    build.EXPECTED_OUTPUT: "このプロジェクトで日本語化済みのROMです。元のUS版を選んでください",
}
OLDER_OUTPUT = "このプロジェクトの以前の版で日本語化済みのROMです。元のUS版を選んでください"
UNKNOWN = ("対応していないファイルです。版の違い・ヘッダ付き・改変済みのどれかです"
           "（docs/TROUBLESHOOTING.md）")


def describe(path: Path) -> tuple[str | None, str]:
    """(role, text): role is "us", "jp" or None."""
    try:
        got = build.sha256_file(path)
    except OSError as e:
        return None, f"読めません: {e}"
    if got == build.PRISTINE_US:
        return "us", LABELS[got]
    if got == build.PRISTINE_JP:
        return "jp", LABELS[got]
    if got in LABELS:
        return None, LABELS[got]
    if got in build.KNOWN:
        return None, OLDER_OUTPUT
    return None, UNKNOWN


def work_dir() -> Path:
    # A frozen release may sit in a folder the user cannot write to; the
    # scratch space (which holds copies of the ROMs) goes to the temp folder
    # and is removed after a successful build.
    if build.FROZEN:
        return Path(tempfile.gettempdir()) / "ffta_us_jp_build"
    return build.REPO / ".build"


class App:
    def __init__(self, root, dropped: list[Path]) -> None:
        import tkinter as tk
        from tkinter import ttk
        from tkinter.scrolledtext import ScrolledText

        self.tk = tk
        self.root = root
        self.events: queue.Queue = queue.Queue()
        self.paths: dict[str, Path | None] = {"us": None, "jp": None}
        self.busy = False

        root.title(TITLE)
        root.minsize(640, 460)
        frame = ttk.Frame(root, padding=12)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)

        intro = ("あなたが持っている US版 と JP版 のROMから、日本語化したUS版ROMを作ります。\n"
                 "ROMはこのツールに含まれていません。ダウンロードもしません。")
        ttk.Label(frame, text=intro, justify="left").grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 10))

        self.status: dict[str, tk.StringVar] = {}
        self.shown: dict[str, tk.StringVar] = {}
        for row, (role, name) in enumerate((("us", "US版 ROM"), ("jp", "JP版 ROM")), start=1):
            ttk.Label(frame, text=name).grid(row=row * 2 - 1, column=0, sticky="w")
            self.shown[role] = tk.StringVar(value="（まだ選んでいません）")
            ttk.Label(frame, textvariable=self.shown[role]).grid(
                row=row * 2 - 1, column=1, sticky="we", padx=8)
            ttk.Button(frame, text="選ぶ…", command=lambda r=role: self.pick(r)).grid(
                row=row * 2 - 1, column=2)
            self.status[role] = tk.StringVar(value="")
            ttk.Label(frame, textvariable=self.status[role], foreground="#555",
                      wraplength=520, justify="left").grid(
                row=row * 2, column=1, columnspan=2, sticky="w", padx=8, pady=(0, 6))

        ttk.Label(frame, text="保存先").grid(row=5, column=0, sticky="w")
        self.output = tk.StringVar(value="")
        self.output.trace_add("write", lambda *_: self.refresh())
        ttk.Entry(frame, textvariable=self.output).grid(row=5, column=1, sticky="we", padx=8)
        ttk.Button(frame, text="変える…", command=self.pick_output).grid(row=5, column=2)

        self.go = ttk.Button(frame, text="日本語化ROMを作る", command=self.start,
                             state="disabled")
        self.go.grid(row=6, column=0, columnspan=3, pady=10)

        self.log = ScrolledText(frame, height=12, state="disabled")
        self.log.grid(row=7, column=0, columnspan=3, sticky="nsew")
        frame.rowconfigure(7, weight=1)

        for path in dropped:
            self.take(path, None)
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(100, self.drain)

    def close(self) -> None:
        from tkinter import messagebox
        if self.busy and not messagebox.askyesno(
                TITLE, "まだ作っています。閉じると途中でやめます。閉じますか？"):
            return
        self.root.destroy()

    # --- picking files ------------------------------------------------------

    def pick(self, role: str) -> None:
        from tkinter import filedialog
        name = filedialog.askopenfilename(
            title=f"{role.upper()}版のROMを選んでください",
            filetypes=[("GBA ROM", "*.gba"), ("すべてのファイル", "*.*")])
        if name:
            self.take(Path(name), role)

    def take(self, path: Path, asked: str | None) -> None:
        if not path.is_file():
            return
        role, text = describe(path)
        # A file is placed by what it is, not by the button that picked it:
        # choosing the JP ROM with the US button still lands in the JP row.
        slot = role or asked or "us"
        self.paths[slot] = path if role == slot else None
        self.shown[slot].set(str(path))
        self.status[slot].set(("OK：" if role == slot else "NG：") + text)
        if slot == "us" and role == "us" and not self.output.get():
            self.output.set(str(path.with_name("FFTA_US_JP.gba")))
        self.refresh()

    def pick_output(self) -> None:
        from tkinter import filedialog
        name = filedialog.asksaveasfilename(
            title="保存先", defaultextension=".gba", initialfile="FFTA_US_JP.gba",
            filetypes=[("GBA ROM", "*.gba")])
        if name:
            self.output.set(name)
            self.refresh()

    def refresh(self) -> None:
        ready = (not self.busy and self.paths["us"] and self.paths["jp"]
                 and self.output.get().strip())
        self.go.configure(state="normal" if ready else "disabled")

    # --- building -----------------------------------------------------------

    def write(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def start(self) -> None:
        from tkinter import messagebox
        output = Path(self.output.get().strip())
        if output.resolve() in {p.resolve() for p in self.paths.values() if p}:
            messagebox.showerror(TITLE, "保存先が元のROMと同じです。別の名前にしてください。")
            return
        if output.exists() and not messagebox.askyesno(
                TITLE, f"{output} は既にあります。上書きしますか？"):
            return
        self.busy = True
        self.refresh()
        self.write("作っています。数分かかります。このウィンドウを閉じないでください。\n")
        args = (self.paths["us"], self.paths["jp"], output)
        threading.Thread(target=self.run, args=args, daemon=True).start()

    def run(self, us: Path, jp: Path, output: Path) -> None:
        try:
            got = build.build(us, jp, output, work_dir(),
                              say=lambda t: self.events.put(("log", t)))
            self.events.put(("done", (output, got)))
        except SystemExit as e:  # BuildError
            self.events.put(("fail", str(e.code).strip()))
        except Exception as e:  # noqa: BLE001 -- shown to the user, not swallowed
            self.events.put(("fail", f"{type(e).__name__}: {e}"))

    def drain(self) -> None:
        from tkinter import messagebox
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "log":
                    self.write(value)
                elif kind == "done":
                    output, got = value
                    self.busy = False
                    self.refresh()
                    self.write(f"\n完成しました: {output}\nSHA-256 {got}")
                    if messagebox.askyesno(
                            TITLE, f"完成しました。\n{output}\n\n保存したフォルダを開きますか？"):
                        open_folder(output.parent)
                elif kind == "fail":
                    self.busy = False
                    self.refresh()
                    self.write("\n" + value)
                    messagebox.showerror(
                        TITLE, "作れませんでした。保存先には何も書いていません。\n\n"
                        "ウィンドウの下の欄に理由があります。報告するときは、その欄の文だけを"
                        "貼ってください（ROMや作業フォルダは添付しないでください）。")
        except queue.Empty:
            pass
        self.root.after(100, self.drain)


def open_folder(path: Path) -> None:
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606 -- the user's own output folder


def smoke_test(out: Path) -> int:
    """Release check without a ROM: the bundled chain and its imports load."""
    import importlib
    import json
    names = ["PIL.Image", "PIL.ImageDraw", "PIL.ImageFont", "argparse", "collections",
             "csv", "functools", "hashlib", "json", "pdb", "pprint", "re", "shutil",
             "struct", "types", "zlib"]
    report = {"release": build.RELEASE, "frozen": build.FROZEN,
              "chain": (build.CHAIN / build.TERMINAL).is_file(), "missing": []}
    for name in names:
        try:
            importlib.import_module(name)
        except ImportError:
            report["missing"].append(name)
    out.write_text(json.dumps(report), encoding="utf-8")
    return 0 if report["chain"] and not report["missing"] else 1


def main() -> int:
    argv = sys.argv[1:]
    if len(argv) == 2 and argv[0] == build.RUN_CHAIN_FLAG:
        return build.run_chain_entry(argv[1])
    if len(argv) == 2 and argv[0] == "--smoke-test":
        return smoke_test(Path(argv[1]))

    if sys.platform == "win32":
        try:  # crisp text on high-DPI screens
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
    import tkinter as tk
    root = tk.Tk()
    # Files dropped onto the executable arrive as arguments.
    App(root, [Path(a) for a in argv[:2]])
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
