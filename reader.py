"""Run without arguments for the reader window; --input FILE for offline CLI."""
from __future__ import annotations
import argparse
from dataclasses import replace
import json
from pathlib import Path
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinter.scrolledtext import ScrolledText
import numpy as np
from modem import Config, Decoder, decode_audio, read_wav, estimate_scale
from ui_common import setup_window, SettingsPanel, DevicePanel, audio_backend, UI_FONT, style_text_scrollbar


class ReaderApp:
    def __init__(self, root):
        self.root, self.events = root, queue.Queue()
        self.stop_event = threading.Event()
        self.busy, self.result = False, None
        outer = setup_window(root, "音频读取器")
        self.settings = SettingsPanel(outer, receiver=True)
        self.settings.pack(fill="x")
        self.devices = DevicePanel(outer, "input")
        self.devices.pack(fill="x", pady=(12, 6))
        self.auto_settings = tk.BooleanVar(value=True)
        ttk.Checkbutton(outer, text="读取 WAV 参数",
                        variable=self.auto_settings).pack(anchor="w")
        bar = ttk.Frame(outer)
        bar.pack(fill="x", pady=(10, 8))
        self.mic_button = ttk.Button(bar, text="麦克风接收", style="Accent.TButton", command=self.start_mic)
        self.mic_button.pack(side="left")
        self.file_button = ttk.Button(bar, text="导入 WAV", command=self.start_file)
        self.file_button.pack(side="left", padx=8)
        ttk.Button(bar, text="停止", command=self.stop).pack(side="left")
        ttk.Button(bar, text="保存文字", command=self.save_result).pack(side="right")
        self.status = tk.StringVar(value="就绪")
        ttk.Label(outer, textvariable=self.status, wraplength=900).pack(anchor="w", pady=(2, 5))
        self.display = ScrolledText(outer, height=5, wrap="word", font=(UI_FONT, 34, "bold"),
                                   bg="white", fg="black", insertbackground="black", selectbackground="black",
                                   selectforeground="white", relief="solid", borderwidth=1, padx=14, pady=10)
        self.display.tag_configure("uncertain", foreground="#d52035")
        style_text_scrollbar(self.display)
        self.display.configure(state="disabled")
        self.display.pack(fill="both", expand=True)
        self.diagnostics = tk.StringVar(value="A：等待    B：等待")
        ttk.Label(outer, textvariable=self.diagnostics).pack(anchor="w", pady=(8, 0))
        self.after_id = root.after(80, self.drain)
        root.protocol("WM_DELETE_WINDOW", self.close)

    def begin(self):
        self.result = None
        self.busy = True
        self.stop_event.clear()
        self.mic_button["state"] = self.file_button["state"] = "disabled"
        self.display.configure(state="normal")
        self.display.delete("1.0", "end")
        self.display.configure(state="disabled")
        self.diagnostics.set("A：同步中    B：同步中")

    def start_file(self):
        if self.busy:
            return
        try:
            config = self.settings.get()
            path = filedialog.askopenfilename(filetypes=[("PCM WAV 音频", "*.wav")])
            if not path:
                return
            auto = self.auto_settings.get()
            self.begin()
            self.status.set("正在读取音频……")
            threading.Thread(target=self.file_worker, args=(path, config, auto), daemon=True).start()
        except Exception as exc:
            messagebox.showerror("参数错误", str(exc))

    def file_worker(self, path, config, auto):
        try:
            audio, rate, embedded = read_wav(path)
            if auto and embedded:
                config = embedded
                self.events.put(("settings", config))
            last_status = None
            def progress(status):
                nonlocal last_status
                if status != last_status:
                    self.events.put(("status", status))
                    last_status = status
            result = decode_audio(audio, rate, config, progress, self.stop_event,
                                  on_partial=lambda value: self.events.put(("partial", value)))
            self.events.put(("result", result))
        except InterruptedError:
            self.events.put(("status", "已停止解析。"))
        except Exception as exc:
            self.events.put(("error", str(exc)))
        finally:
            self.events.put(("done", None))

    def start_mic(self):
        if self.busy:
            return
        try:
            config = self.settings.get()
            backend = audio_backend()
            device = self.devices.get()
            self.begin()
            self.status.set("正在打开麦克风……")
            threading.Thread(target=self.mic_worker, args=(backend, config, device), daemon=True).start()
        except Exception as exc:
            messagebox.showerror("麦克风不可用", str(exc))

    def mic_worker(self, backend, config, device):
        chunks, discontinuity = queue.Queue(maxsize=64), threading.Event()
        decoder, latest_result, last_preview = None, None, None
        def callback(indata, frames, time_info, status):
            if status:
                discontinuity.set()
            try:
                chunks.put_nowait(indata.copy())
            except queue.Full:
                discontinuity.set()
        try:
            rate = config.sample_rate
            try:
                backend.check_input_settings(device=device, channels=1, samplerate=rate, dtype="float32")
            except Exception:
                rate = round(backend.query_devices(device, "input")["default_samplerate"])
                backend.check_input_settings(device=device, channels=1, samplerate=rate, dtype="float32")
            if max(config.frequencies) >= rate/2-100:
                raise ValueError("麦克风采样率不足以覆盖通信频率，请降低频率。")
            calibration = np.empty((0, 1), dtype=np.float32)
            since_check, last_poll = 0, time.monotonic()
            started = time.monotonic()
            with backend.InputStream(device=device, samplerate=rate, channels=1, dtype="float32",
                                     blocksize=round(rate*.08), callback=callback):
                self.events.put(("status", "等待信号"))
                while not self.stop_event.is_set():
                    if discontinuity.is_set():
                        raise RuntimeError("录音丢帧，请重新接收。")
                    if time.monotonic()-started > 1200:
                        raise RuntimeError("已等待 20 分钟，接收自动停止。")
                    try:
                        chunk = chunks.get(timeout=.15)
                    except queue.Empty:
                        continue
                    if decoder is None:
                        calibration = np.concatenate([calibration, chunk])[-round(rate*.9):]
                        since_check += len(chunk)
                        if len(calibration) < rate*.6 or since_check < rate*.2:
                            continue
                        since_check = 0
                        scale = estimate_scale(calibration, rate, config.masker_hz)
                        if scale is None:
                            self.events.put(("status", "等待主音"))
                            continue
                        decoder = Decoder(config, rate, scale)
                        decoder.feed(calibration)
                    else:
                        decoder.feed(chunk)
                    if time.monotonic()-last_poll < .08:
                        continue
                    last_poll = time.monotonic()
                    latest_result = decoder.poll(partial=True)
                    if latest_result is None:
                        self.events.put(("status", "同步中"))
                    elif latest_result.finalized:
                        self.events.put(("result", latest_result))
                        break
                    elif latest_result.text:
                        signature = (latest_result.text, tuple(latest_result.red))
                        if signature != last_preview:
                            self.events.put(("partial", latest_result))
                            last_preview = signature
            if self.stop_event.is_set():
                partial = decoder.poll(final=True) if decoder is not None else None
                if partial:
                    self.events.put(("result", partial))
                else:
                    self.events.put(("status", "已停止"))
        except Exception as exc:
            self.events.put(("error", str(exc)))
        finally:
            self.events.put(("done", None))

    def show_result(self, result):
        self.result = result
        top, bottom = self.display.yview()
        self.display.configure(state="normal")
        self.display.delete("1.0", "end")
        for char, uncertain in zip(result.text, result.red):
            self.display.insert("end", char, ("uncertain",) if uncertain else ())
        self.display.configure(state="disabled")
        if bottom >= .98:
            self.display.see("end")
        else:
            self.display.yview_moveto(top)
        if not result.finalized:
            self.status.set(f"接收中 · {len(result.text)} 字")
        else:
            self.status.set("完成 · 已校验" if result.verified else f"完成 · {sum(result.red)} 字待核对")
        channels = []
        for name, channel in zip(("A", "B"), (result.channel_a, result.channel_b)):
            state = ("等待" if channel is None else "已校验" if channel.crc_ok else
                     "接收中" if not result.finalized else "待核对")
            channels.append(f"{name}：{state}")
        self.diagnostics.set("    ".join(channels))

    def finalize_preview(self):
        if self.result is not None and not self.result.finalized:
            self.show_result(replace(self.result, red=[True]*len(self.result.text), complete=False,
                                     finalized=True, warnings=self.result.warnings+["接收未完成。没有整帧校验结果。" ]))
            self.status.set("已停止 · 未完成")

    def save_result(self):
        if not self.result:
            messagebox.showinfo("尚无结果", "请先完成一次接收。")
            return
        path = filedialog.asksaveasfilename(defaultextension=".txt", filetypes=[("文本", "*.txt")])
        if path:
            try:
                Path(path).write_text(self.result.text, encoding="utf-8")
                report = Path(path).with_suffix(".validation.json")
                report.write_text(json.dumps(self.result.summary(), ensure_ascii=False, indent=2), encoding="utf-8")
                self.status.set("已保存")
            except Exception as exc:
                messagebox.showerror("保存失败", str(exc))

    def drain(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "status":
                    self.status.set(value)
                elif kind == "settings":
                    self.settings.set(value)
                elif kind == "result":
                    self.show_result(value)
                elif kind == "partial":
                    self.show_result(value)
                    # Let Tk paint each character, including for fast file decoding.
                    self.after_id = self.root.after(16, self.drain)
                    return
                elif kind == "error":
                    self.finalize_preview()
                    self.status.set("接收失败")
                    messagebox.showerror("接收失败", value)
                elif kind == "done":
                    self.finalize_preview()
                    self.busy = False
                    self.mic_button["state"] = self.file_button["state"] = "normal"
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
    parser = argparse.ArgumentParser(description="双频段摩斯读取器；无参数启动图形界面。")
    parser.add_argument("--input", type=Path, help="输入 PCM WAV")
    parser.add_argument("--config", type=Path, help="接收参数 JSON")
    parser.add_argument("--json", type=Path, help="输出逐字符校验 JSON")
    parser.add_argument("--ignore-settings", action="store_true", help="忽略 WAV 内附参数")
    args = parser.parse_args()
    config = Config.load(args.config) if args.config else Config()
    if args.input is None:
        root = tk.Tk()
        app = ReaderApp(root)
        app.settings.set(config)
        root.mainloop()
        return
    audio, rate, embedded = read_wav(args.input)
    if embedded and not args.ignore_settings:
        config = embedded
    result = decode_audio(audio, rate, config)
    print(json.dumps(result.summary(), ensure_ascii=False, indent=2))
    if args.json:
        args.json.write_text(json.dumps(result.summary(), ensure_ascii=False, indent=2), encoding="utf-8")
    raise SystemExit(0 if result.verified else 2)


if __name__ == "__main__":
    main()
