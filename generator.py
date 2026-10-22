"""Run without arguments for the generator window; --help for CLI usage."""
from __future__ import annotations
import argparse
from dataclasses import replace
import queue
import sys
import threading
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from tkinter.scrolledtext import ScrolledText
from modem import Config, make_protocol, synthesize, write_wav, parse_extra_tone
from ui_common import setup_window, SettingsPanel, DevicePanel, audio_backend, UI_FONT, style_text_scrollbar


class GeneratorApp:
    def __init__(self, root):
        self.root = root
        self.events = queue.Queue()
        self.stop_event = threading.Event()
        self.busy = False
        self.backend = None
        outer = setup_window(root, "音频生成器")
        self.settings = SettingsPanel(outer)
        self.settings.pack(fill="x")
        ttk.Label(outer, text="发送文字").pack(anchor="w", pady=(12, 6))
        self.message = ScrolledText(outer, height=4, font=(UI_FONT, 16), wrap="word", bg="white", fg="black",
                                   insertbackground="black", selectbackground="black", selectforeground="white",
                                   relief="solid", borderwidth=1, padx=10, pady=8)
        style_text_scrollbar(self.message)
        self.message.insert("1.0", "你好，HELLO!")
        self.message.pack(fill="both", expand=True)
        self.devices = DevicePanel(outer, "output")
        self.devices.pack(fill="x", pady=(12, 8))
        self.include_settings = tk.BooleanVar(value=False)
        ttk.Checkbutton(outer, text="WAV 附带参数",
                        variable=self.include_settings).pack(anchor="w")
        bar = ttk.Frame(outer)
        bar.pack(fill="x", pady=(12, 8))
        self.play_button = ttk.Button(bar, text="播放", style="Accent.TButton", command=lambda: self.start("play"))
        self.play_button.pack(side="left")
        self.save_button = ttk.Button(bar, text="保存 WAV", command=lambda: self.start("save"))
        self.save_button.pack(side="left", padx=10)
        ttk.Button(bar, text="停止", command=self.stop).pack(side="left")
        ttk.Button(bar, text="估算时长", command=self.estimate).pack(side="right")
        self.status = tk.StringVar(value="就绪")
        ttk.Label(outer, textvariable=self.status, wraplength=900).pack(anchor="w", pady=(5, 5))
        self.after_id = root.after(80, self.drain)
        root.protocol("WM_DELETE_WINDOW", self.close)

    def capture(self):
        config = self.settings.get()
        text = self.message.get("1.0", "end-1c")
        _, info = make_protocol(text, config)
        return config, text, info

    def estimate(self):
        try:
            _, _, info = self.capture()
            self.status.set(f"{info['bytes']} 字节 · {info['duration_seconds']:.1f} 秒")
        except Exception as exc:
            messagebox.showerror("无法生成", str(exc))

    def start(self, action):
        if self.busy:
            return
        try:
            config, text, info = self.capture()
            self.settings.warn_extra_tones()
            path = None
            if action == "save":
                path = filedialog.asksaveasfilename(defaultextension=".wav", initialfile="message.wav",
                                                   filetypes=[("PCM WAV 音频", "*.wav")])
                if not path:
                    return
            else:
                self.backend = audio_backend()
            self.busy = True
            self.stop_event.clear()
            self.play_button["state"] = self.save_button["state"] = "disabled"
            self.status.set(f"正在生成 {info['duration_seconds']:.1f} 秒音频……")
            device, include = self.devices.get(), self.include_settings.get()
            threading.Thread(target=self.worker, args=(action, config, text, path, device, include), daemon=True).start()
        except Exception as exc:
            messagebox.showerror("无法生成", str(exc))

    def worker(self, action, config, text, path, device, include):
        try:
            audio, info = synthesize(text, config)
            mix_note = ""
            if info['mix_attenuation_db'] > .01:
                mix_note = f" · 整体降低 {info['mix_attenuation_db']:.1f} dB"
            if self.stop_event.is_set():
                return
            if action == "save":
                write_wav(path, audio, config, include)
                self.events.put(("status", f"已保存 {Path(path).name} · {info['duration_seconds']:.1f} 秒{mix_note}"))
            else:
                # A callback stream permits stopping without blocking the interface.
                cursor = 0
                finished = threading.Event()
                backend = self.backend
                def callback(outdata, frames, time_info, status):
                    nonlocal cursor
                    if self.stop_event.is_set():
                        outdata.fill(0)
                        raise backend.CallbackStop
                    count = min(frames, len(audio)-cursor)
                    outdata.fill(0)
                    outdata[:count, 0] = audio[cursor:cursor+count]
                    cursor += count
                    if cursor >= len(audio):
                        raise backend.CallbackStop
                with backend.OutputStream(samplerate=config.sample_rate, channels=1, dtype="float32",
                                          device=device, callback=callback, finished_callback=finished.set):
                    self.events.put(("status", "播放中"))
                    while not finished.wait(.15):
                        self.events.put(("status", f"正在播放 {cursor/config.sample_rate:.1f} / {info['duration_seconds']:.1f} 秒{mix_note}"))
                self.events.put(("status", ("已停止" if self.stop_event.is_set() else "发送完成")+mix_note))
        except Exception as exc:
            self.events.put(("error", str(exc)))
        finally:
            self.events.put(("done", None))

    def drain(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "error":
                    self.status.set("操作失败。")
                    messagebox.showerror("操作失败", value)
                elif kind == "status":
                    self.status.set(value)
                elif kind == "done":
                    self.busy = False
                    self.play_button["state"] = self.save_button["state"] = "normal"
        except queue.Empty:
            pass
        self.after_id = self.root.after(80, self.drain)

    def stop(self):
        self.stop_event.set()
        if self.busy:
            self.status.set("正在停止……")

    def close(self):
        self.stop_event.set()
        self.root.after_cancel(self.after_id)
        self.root.destroy()


def main():
    parser = argparse.ArgumentParser(description="双频段摩斯音频生成器；无参数启动图形界面。")
    parser.add_argument("--message", help="要发送的文字")
    parser.add_argument("--output", type=Path, help="输出 PCM WAV 文件")
    parser.add_argument("--config", type=Path, help="接收参数 JSON")
    parser.add_argument("--play", action="store_true", help="现场播放")
    parser.add_argument("--include-settings", action="store_true", help="WAV 附带公开接收参数")
    parser.add_argument("--tone", action="append", type=parse_extra_tone, default=[], metavar="HZ[:DB]",
                        help="额外纯音，强度相对主音；可重复，例如 --tone 700:-18 --tone 3000:-12")
    args = parser.parse_args()
    config = Config.load(args.config) if args.config else Config()
    if args.tone:
        config = replace(config, extra_tones=config.extra_tones+args.tone).validate()
    if args.message is None and not args.output and not args.play:
        root = tk.Tk()
        app = GeneratorApp(root)
        app.settings.set(config)
        root.mainloop()
        return
    if args.message is None or not (args.output or args.play):
        parser.error("命令行模式需要 --message，以及 --output 或 --play。")
    audio, info = synthesize(args.message, config)
    for warning in info["interference_warnings"]:
        print("预警："+warning, file=sys.stderr)
    if info["mix_attenuation_db"] > .01:
        print(f"防削波整体降低 {info['mix_attenuation_db']:.1f} dB", file=sys.stderr)
    print(f"{info['bytes']} bytes; {info['duration_seconds']:.2f} seconds")
    if args.output:
        write_wav(args.output, audio, config, args.include_settings)
        print(args.output)
    if args.play:
        backend = audio_backend()
        backend.play(audio, config.sample_rate)
        backend.wait()


if __name__ == "__main__":
    main()
