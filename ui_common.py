"""Shared Tk widgets and optional audio-device support."""
from __future__ import annotations
import importlib
import os
from pathlib import Path
import sys
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from modem import Config, ExtraTone, analyze_extra_tones

UI_FONT = "PingFang SC" if sys.platform == "darwin" else "Microsoft YaHei UI"


def audio_backend():
    try:
        return importlib.import_module("sounddevice")
    except (ImportError, OSError) as exc:
        # Support an optional, separately supplied Windows/Python 3.12 backend.
        vendor = Path(__file__).with_name("vendor")
        if sys.platform == "win32" and sys.version_info[:2] == (3, 12) and vendor.is_dir():
            sys.path.insert(0, str(vendor))
            try:
                return importlib.import_module("sounddevice")
            except (ImportError, OSError):
                pass
        installer = "安装依赖.command" if sys.platform == "darwin" else "安装依赖.cmd"
        raise RuntimeError(f"音频依赖不可用，请运行 {installer}。") from exc


def setup_window(root, title, subtitle=""):
    root.title(title)
    width = min(960, root.winfo_screenwidth()-80)
    height = min(740, root.winfo_screenheight()-100)
    root.geometry(f"{width}x{height}")
    root.minsize(min(840, width), min(640, height))
    root.configure(bg="white")
    for option, value in (("*Font", (UI_FONT, 10)), ("*Background", "white"),
                          ("*Foreground", "black"), ("*selectBackground", "black"),
                          ("*selectForeground", "white"), ("*highlightColor", "black"),
                          ("*highlightBackground", "white")):
        root.option_add(option, value)
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", background="white", foreground="black", bordercolor="black",
                    lightcolor="black", darkcolor="black", troughcolor="white", focuscolor="black")
    style.configure("TFrame", background="white")
    style.configure("TLabel", background="white", foreground="black")
    style.configure("TLabelframe", background="white", bordercolor="black")
    style.configure("TLabelframe.Label", background="white", foreground="black")
    style.configure("TButton", padding=(10, 6), font=(UI_FONT, 10), background="white",
                    foreground="black", relief="solid", borderwidth=1)
    style.map("TButton", background=[("active", "black"), ("!active", "white")],
              foreground=[("active", "white"), ("!active", "black")],
              bordercolor=[("focus", "black"), ("!focus", "black")])
    style.configure("Accent.TButton", background="black", foreground="white")
    style.map("Accent.TButton", background=[("active", "white"), ("!active", "black")],
              foreground=[("active", "black"), ("!active", "white")])
    for name in ("TEntry", "TCombobox"):
        style.configure(name, fieldbackground="white", foreground="black", selectbackground="black",
                        selectforeground="white", arrowcolor="black", bordercolor="black")
        style.map(name, fieldbackground=[("readonly", "white"), ("!readonly", "white")],
                  foreground=[("disabled", "black"), ("!disabled", "black")],
                  bordercolor=[("focus", "black"), ("!focus", "black")])
    style.configure("TCheckbutton", background="white", foreground="black", indicatorbackground="white")
    style.map("TCheckbutton", background=[("active", "white")], foreground=[("disabled", "black")],
              indicatorbackground=[("selected", "black"), ("!selected", "white")],
              indicatorforeground=[("selected", "white"), ("!selected", "black")])
    style.configure("Treeview", background="white", fieldbackground="white", foreground="black", rowheight=27)
    style.map("Treeview", background=[("selected", "black")], foreground=[("selected", "white")])
    style.configure("Treeview.Heading", background="white", foreground="black", relief="solid")
    style.map("Treeview.Heading", background=[("active", "white")], foreground=[("active", "black")])
    style.configure("TScrollbar", background="black", arrowcolor="white", troughcolor="white")
    style.map("TScrollbar", background=[("active", "black"), ("!active", "black")])
    outer = ttk.Frame(root, padding=18)
    outer.pack(fill="both", expand=True)
    ttk.Label(outer, text=title, font=(UI_FONT, 20, "bold")).pack(anchor="w", pady=(0, 12))
    return outer


def style_text_scrollbar(widget):
    widget.vbar.configure(background="black", activebackground="black", troughcolor="white",
                          highlightbackground="white", highlightcolor="black", borderwidth=0,
                          elementborderwidth=0, relief="flat", activerelief="flat")


class SettingsPanel(ttk.LabelFrame):
    fields = [
        ("masker_hz", "主音频率 / Hz"), ("masker_dbfs", "主音数字峰值 / dBFS"),
        ("offset_a", "A 路向上频偏 / Hz"), ("signal_relative_db", "通信声相对主音 / dB"),
        ("offset_b", "B 路向上频偏 / Hz"), ("unit_ms", "摩斯点时长 / 毫秒"),
        ("leader_seconds", "连续 1 前导 / 秒"), ("sample_rate", "采样率 / Hz")]

    def __init__(self, parent, receiver=False):
        super().__init__(parent, text="参数", padding=(12, 8))
        self.variables = {}
        self.receiver = receiver
        self.extra_tones = []
        self.extra_dialog = None
        self.last_warning_signature = None
        defaults = Config()
        for index, (name, title) in enumerate(self.fields):
            row, column = divmod(index, 2)
            ttk.Label(self, text=title).grid(row=row, column=column*2, sticky="w", padx=(0, 10), pady=3)
            variable = tk.StringVar(value=str(getattr(defaults, name)))
            self.variables[name] = variable
            entry = ttk.Entry(self, textvariable=variable, width=15)
            entry.grid(row=row, column=column*2+1, sticky="ew", padx=(0, 22), pady=3)
        self.columnconfigure(1, weight=1)
        self.columnconfigure(3, weight=1)
        buttons = ttk.Frame(self)
        buttons.grid(row=4, column=0, columnspan=4, sticky="ew", pady=(7, 0))
        ttk.Button(buttons, text="导出参数", command=self.save).pack(side="left")
        ttk.Button(buttons, text="导入参数", command=self.load).pack(side="left", padx=8)
        if not receiver:
            extras = ttk.Frame(self)
            extras.grid(row=5, column=0, columnspan=4, sticky="ew", pady=(8, 0))
            ttk.Button(extras, text="额外叠加频率…", command=self.open_extra_tones).pack(side="left")
            self.extra_summary = tk.StringVar(value="0 项")
            ttk.Label(extras, textvariable=self.extra_summary).pack(side="left", padx=10)

    def get(self):
        try:
            values = {name: float(var.get()) for name, var in self.variables.items()}
            if not values["sample_rate"].is_integer():
                raise ValueError("采样率必须为整数。")
            values["sample_rate"] = int(values["sample_rate"])
            return Config(**values, extra_tones=list(self.extra_tones)).validate()
        except ValueError as exc:
            raise ValueError("请检查参数："+str(exc)) from exc

    def set(self, config):
        for name, variable in self.variables.items():
            variable.set(str(getattr(config, name)))
        self.extra_tones = list(config.extra_tones)
        self.update_extra_summary()
        if self.extra_dialog is not None and self.extra_dialog.winfo_exists():
            self.extra_dialog.refresh()

    def update_extra_summary(self):
        if not self.receiver:
            count = len(self.extra_tones)
            self.extra_summary.set(f"{count} 项")

    def warn_extra_tones(self, force=False, analysis=None):
        config = self.get()
        analysis = analysis or analyze_extra_tones(config)
        signature = repr(config)
        parent = (self.extra_dialog if self.extra_dialog is not None and self.extra_dialog.winfo_exists()
                  else self.winfo_toplevel())
        if analysis.warnings:
            if force or signature != self.last_warning_signature:
                lines = analysis.warnings[:4]
                text = "\n\n".join(lines)
                if len(analysis.warnings) > len(lines):
                    text += f"\n\n另有 {len(analysis.warnings)-len(lines)} 项预警，可在列表中查看。"
                messagebox.showwarning("额外频率可能影响通信", text, parent=parent)
                self.last_warning_signature = signature
        else:
            self.last_warning_signature = None
            if force:
                messagebox.showinfo("频率检查", "未发现频率冲突。",
                                    parent=parent)
        return analysis

    def open_extra_tones(self):
        if self.extra_dialog is not None and self.extra_dialog.winfo_exists():
            self.extra_dialog.lift()
        else:
            self.extra_dialog = ExtraTonesDialog(self)

    def save(self):
        try:
            config = self.get()
            path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("接收参数", "*.json")])
            if path:
                config.save(path)
        except Exception as exc:
            messagebox.showerror("参数错误", str(exc))

    def load(self):
        path = filedialog.askopenfilename(filetypes=[("接收参数", "*.json")])
        if path:
            try:
                self.set(Config.load(path))
                if not self.receiver:
                    self.warn_extra_tones()
            except Exception as exc:
                messagebox.showerror("导入失败", str(exc))


class ExtraTonesDialog(tk.Toplevel):
    def __init__(self, owner):
        super().__init__(owner.winfo_toplevel())
        self.owner = owner
        self.title("额外叠加频率")
        self.geometry("880x590")
        self.minsize(760, 500)
        self.configure(bg="white")
        self.transient(owner.winfo_toplevel())
        outer = ttk.Frame(self, padding=16)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="额外频率", font=(UI_FONT, 18, "bold")).pack(anchor="w", pady=(0, 12))
        form = ttk.Frame(outer)
        form.pack(fill="x")
        self.frequency = tk.StringVar(value="700")
        self.level = tk.StringVar(value="-12")
        ttk.Label(form, text="频率 / Hz").pack(side="left")
        ttk.Entry(form, textvariable=self.frequency, width=12).pack(side="left", padx=(6, 14))
        ttk.Label(form, text="相对主音 / dB").pack(side="left")
        ttk.Entry(form, textvariable=self.level, width=9).pack(side="left", padx=(6, 12))
        ttk.Button(form, text="添加", style="Accent.TButton", command=self.add).pack(side="left")
        ttk.Button(form, text="更新选中", command=self.update_selected).pack(side="left", padx=6)
        table = ttk.Frame(outer)
        table.pack(fill="both", expand=True, pady=(12, 8))
        self.tree = ttk.Treeview(table, columns=("frequency", "level", "risk"), show="headings", height=8)
        self.tree.heading("frequency", text="频率 / Hz")
        self.tree.heading("level", text="相对主音 / dB")
        self.tree.heading("risk", text="检查结果")
        self.tree.column("frequency", width=150, anchor="center")
        self.tree.column("level", width=150, anchor="center")
        self.tree.column("risk", width=440)
        self.tree.tag_configure("risk", font=(UI_FONT, 10, "bold"))
        scroll = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self.select)
        self.details = tk.Text(outer, height=4, wrap="word", bg="white", fg="black", relief="solid", borderwidth=1,
                               selectbackground="black", selectforeground="white",
                               font=(UI_FONT, 10), padx=8, pady=6, state="disabled")
        self.details.pack(fill="x")
        self.summary = tk.StringVar()
        ttk.Label(outer, textvariable=self.summary, wraplength=820).pack(anchor="w", pady=(7, 4))
        bar = ttk.Frame(outer)
        bar.pack(fill="x", pady=(5, 0))
        ttk.Button(bar, text="删除选中", command=self.remove_selected).pack(side="left")
        ttk.Button(bar, text="清空列表", command=self.clear).pack(side="left", padx=6)
        ttk.Button(bar, text="检查频率冲突", command=self.check).pack(side="left")
        ttk.Button(bar, text="完成", command=self.destroy).pack(side="right")
        self.analysis = None
        self.refresh()

    def refresh(self):
        try:
            self.analysis = analyze_extra_tones(self.owner.get())
        except ValueError:
            self.analysis = None
        self.tree.delete(*self.tree.get_children())
        for i, tone in enumerate(self.owner.extra_tones):
            issues = self.analysis.per_tone[i] if self.analysis else []
            status = issues[0] if issues else "正常"
            self.tree.insert("", "end", iid=str(i), values=(f"{tone.frequency_hz:g}", f"{tone.relative_db:g}", status),
                             tags=("risk",) if issues else ())
        count = len(self.owner.extra_tones)
        warnings = len(self.analysis.warnings) if self.analysis else 0
        self.summary.set(f"{count} 项 · {warnings} 处提示")
        self.details.configure(state="normal")
        self.details.delete("1.0", "end")
        self.details.configure(state="disabled")
        self.owner.update_extra_summary()

    def select(self, event=None):
        selected = self.tree.selection()
        if not selected:
            return
        index = int(selected[0])
        if index >= len(self.owner.extra_tones):
            return
        tone = self.owner.extra_tones[index]
        self.frequency.set(f"{tone.frequency_hz:g}")
        self.level.set(f"{tone.relative_db:g}")
        issues = self.analysis.per_tone[index] if self.analysis else []
        self.details.configure(state="normal")
        self.details.delete("1.0", "end")
        self.details.insert("1.0", "\n".join(issues) if issues else "正常")
        self.details.configure(state="disabled")

    def commit(self, tones):
        old = self.owner.extra_tones
        self.owner.extra_tones = tones
        try:
            analysis = analyze_extra_tones(self.owner.get())
        except ValueError as exc:
            self.owner.extra_tones = old
            messagebox.showerror("无法添加频率", str(exc), parent=self)
            return False
        self.refresh()
        self.owner.warn_extra_tones(analysis=analysis)
        return True

    def read_entry(self):
        try:
            return ExtraTone(float(self.frequency.get()), float(self.level.get()))
        except ValueError as exc:
            raise ValueError("频率与强度应填写数字。") from exc

    def add(self):
        try:
            tone = self.read_entry()
            self.commit(self.owner.extra_tones+[tone])
        except ValueError as exc:
            messagebox.showerror("输入错误", str(exc), parent=self)

    def update_selected(self):
        selected = self.tree.selection()
        if len(selected) != 1:
            messagebox.showinfo("请选择一项", "先选择一项频率，再修改数值并点击更新。", parent=self)
            return
        try:
            tones = list(self.owner.extra_tones)
            tones[int(selected[0])] = self.read_entry()
            self.commit(tones)
        except ValueError as exc:
            messagebox.showerror("输入错误", str(exc), parent=self)

    def remove_selected(self):
        selected = set(self.tree.selection())
        if selected:
            self.commit([tone for i, tone in enumerate(self.owner.extra_tones) if str(i) not in selected])

    def clear(self):
        self.commit([])

    def check(self):
        try:
            self.refresh()
            self.owner.warn_extra_tones(force=True, analysis=self.analysis)
        except ValueError as exc:
            messagebox.showerror("参数错误", str(exc), parent=self)


class DevicePanel(ttk.Frame):
    def __init__(self, parent, kind):
        super().__init__(parent)
        self.kind = kind
        self.ids = [None]
        ttk.Label(self, text="输入设备" if kind == "input" else "播放设备").pack(side="left", padx=(0, 8))
        self.box = ttk.Combobox(self, state="readonly", width=48, values=["系统默认设备"])
        self.box.current(0)
        self.box.pack(side="left", fill="x", expand=True)
        ttk.Button(self, text="刷新设备", command=self.refresh).pack(side="left", padx=(8, 0))

    def refresh(self):
        try:
            backend = audio_backend()
            names, self.ids = ["系统默认设备"], [None]
            for index, device in enumerate(backend.query_devices()):
                if device["max_"+self.kind+"_channels"]:
                    names.append(f"{index} · {device['name']}")
                    self.ids.append(index)
            self.box["values"] = names
            self.box.current(0)
        except Exception as exc:
            messagebox.showerror("设备不可用", str(exc))

    def get(self):
        return self.ids[max(0, self.box.current())]
