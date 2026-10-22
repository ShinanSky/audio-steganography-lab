"""Two-channel, mask-assisted OOK / hexadecimal Morse modem.

The DSP decoder reads the waveform, never the message from a WAV metadata chunk.
Only numpy is needed for offline generation and decoding.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict, field
import json
import math
from pathlib import Path
import struct
import wave
import zlib
import numpy as np

HEX_MORSE = dict(zip("0123456789ABCDEF", [
    "-----", ".----", "..---", "...--", "....-", ".....", "-....", "--...",
    "---..", "----.", ".-", "-...", "-.-.", "-..", ".", "..-."]))
MAGIC = 0xD391C5A7
ALTERNATING = [1, 0] * 20  # Twenty pairs, forty symbols.
RECORD_MARKER = [1, 1, 0, 1, 0, 0, 1, 0, 1, 1, 1, 0]
HEADER_FORMAT = ">2sBBHI"
HEADER_BYTES = struct.calcsize(HEADER_FORMAT) + 2
MAX_BYTES = 256
CELL = 24
RECORD_UNITS = len(RECORD_MARKER) + CELL * 2 + 8


def bits_of(data: bytes) -> list[int]:
    return np.unpackbits(np.frombuffer(data, dtype=np.uint8)).tolist()


SYNC_BITS = ALTERNATING + bits_of(MAGIC.to_bytes(4, "big"))


def pack_bits(bits) -> bytes:
    return np.packbits(np.asarray(bits, dtype=np.uint8)).tobytes()


def crc8(data: bytes) -> int:
    value = 0
    for byte in data:
        value ^= byte
        for _ in range(8):
            value = ((value << 1) ^ (0x07 if value & 128 else 0)) & 255
    return value


def crc16(data: bytes) -> int:
    value = 0xFFFF
    for byte in data:
        value ^= byte << 8
        for _ in range(8):
            value = ((value << 1) ^ (0x1021 if value & 0x8000 else 0)) & 65535
    return value


def morse_cell(char: str) -> list[int]:
    result = []
    for token in HEX_MORSE[char]:
        if result:
            result.append(0)
        result.extend([1] * (1 if token == "." else 3))
    return result + [0] * (CELL - len(result))


MORSE_CELLS = np.array([morse_cell(h) for h in "0123456789ABCDEF"])


@dataclass(frozen=True)
class ExtraTone:
    frequency_hz: float
    relative_db: float = -12.0

    def validate(self, sample_rate):
        if not all(isinstance(v, (int, float)) and math.isfinite(v)
                   for v in (self.frequency_hz, self.relative_db)):
            raise ValueError("额外频率与强度必须是有限数字。")
        if not 0 < self.frequency_hz < sample_rate/2:
            raise ValueError(f"额外频率必须大于 0 且低于采样率的一半（{sample_rate/2:g} Hz）。")
        if not -80 <= self.relative_db <= 12:
            raise ValueError("额外纯音相对主音的强度应在 −80 至 +12 dB 之间。")
        return self


def parse_extra_tone(spec):
    """CLI syntax HZ[:RELATIVE_DB], e.g. 700:-18."""
    parts = spec.split(":")
    if len(parts) not in (1, 2):
        raise ValueError("格式应为 频率[:相对主音dB]，例如 700:-18。")
    return ExtraTone(float(parts[0]), float(parts[1]) if len(parts) == 2 else -12.0)


@dataclass
class Config:
    sample_rate: int = 48000
    masker_hz: float = 1800.0
    offset_a: float = 120.0
    offset_b: float = 240.0
    masker_dbfs: float = -12.0
    signal_relative_db: float = -30.0
    unit_ms: float = 60.0
    leader_seconds: float = 1.5
    extra_tones: list[ExtraTone] = field(default_factory=list)

    def __post_init__(self):
        if isinstance(self.extra_tones, (list, tuple)):
            self.extra_tones = [ExtraTone(**tone) if isinstance(tone, dict) else tone
                                for tone in self.extra_tones]

    @property
    def unit(self):
        return round(self.sample_rate * self.unit_ms / 1000) / self.sample_rate

    @property
    def frequencies(self):
        return [self.masker_hz, self.masker_hz + self.offset_a,
                self.masker_hz + self.offset_b]

    def validate(self):
        values = [value for name, value in asdict(self).items() if name != "extra_tones"]
        if not all(isinstance(v, (float, int)) and math.isfinite(v) for v in values):
            raise ValueError("所有参数必须是有限的数字。")
        if self.sample_rate not in (16000, 22050, 24000, 32000, 44100, 48000, 96000):
            raise ValueError("采样率请选择 16000、22050、24000、32000、44100、48000 或 96000。")
        if not 40 <= self.unit_ms <= 200:
            raise ValueError("码元时长应在 40–200 毫秒之间；麦克风接收推荐 60–100。")
        if not 0.5 <= self.leader_seconds <= 10:
            raise ValueError("连续 1 的时长应在 0.5–10 秒之间。")
        if self.leader_seconds < 8 * self.unit:
            raise ValueError("连续 1 至少应持续 8 个码元。")
        if not 200 <= self.masker_hz <= 6000:
            raise ValueError("主音频率应在 200–6000 Hz。")
        if not (80 <= self.offset_a < self.offset_b and self.offset_b-self.offset_a >= 80):
            raise ValueError("两个频偏均向上，第一频偏至少 80 Hz，两路至少相隔 80 Hz。")
        if max(self.frequencies) >= self.sample_rate / 2 - 300:
            raise ValueError("通信频率过高，请提高采样率或降低频率。")
        if not -40 <= self.masker_dbfs <= -3:
            raise ValueError("主音数字峰值请选择 −40 至 −3 dBFS。")
        if not -55 <= self.signal_relative_db <= -12:
            raise ValueError("通信声相对主音请选择 −55 至 −12 dB。")
        if 10**(self.masker_dbfs/20) * (1+2*10**(self.signal_relative_db/20)) >= .95:
            raise ValueError("总振幅过高，会削波，请降低主音或通信声振幅。")
        if not isinstance(self.extra_tones, list) or not all(isinstance(t, ExtraTone) for t in self.extra_tones):
            raise ValueError("额外频率必须是 ExtraTone 列表或带 frequency_hz、relative_db 的 JSON 列表。")
        for tone in self.extra_tones:
            tone.validate(self.sample_rate)
        return self

    def save(self, path):
        Path(path).write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path):
        return cls(**json.loads(Path(path).read_text(encoding="utf-8"))).validate()


@dataclass
class ToneAnalysis:
    warnings: list[str]
    per_tone: list[list[str]]
    peak_bound: float
    guard_hz: float


def analyze_extra_tones(config: Config) -> ToneAnalysis:
    """Conservative DIGITAL checks plus conditional nonlinear-device warnings.

    This is not a guarantee for arbitrary hardware/rooms. The wire format and
    decoder tone bank are deliberately unchanged; extras are transmitter-only.
    """
    config.validate()
    per_tone = [[] for _ in config.extra_tones]
    warnings = []
    window_seconds = min(.03, config.unit*.5)
    guard = max(80., 2/window_seconds)
    frequencies = config.frequencies
    small = 10**(config.signal_relative_db/20)
    levels = [10**(tone.relative_db/20) for tone in config.extra_tones]
    peak_bound = 10**(config.masker_dbfs/20)*(1+2*small+math.fsum(levels))

    def warn(message, indices=()):
        warnings.append(message)
        for index in set(indices):
            per_tone[index].append(message)

    for i, tone in enumerate(config.extra_tones):
        f = tone.frequency_hz
        for name, carrier in zip(("A", "B"), frequencies[1:]):
            if abs(f-carrier) <= guard:
                warn(f"第 {i+1} 项 {f:g} Hz 靠近 {name} 路 {carrier:g} Hz（保护范围 ±{guard:g} Hz），"
                     "可能抬高静音电平、产生拍频或造成误码。", (i,))
        if abs(f-frequencies[0]) <= frequencies[0]*.025:
            warn(f"第 {i+1} 项 {f:g} Hz 位于主音检测区，可能改变主音电平或干扰频率/时钟校准。", (i,))
        for previous in range(i):
            if abs(f-config.extra_tones[previous].frequency_hz) < .01:
                warn(f"第 {previous+1}、{i+1} 项频率重复，振幅会相加。", (previous, i))

    if config.extra_tones:
        # Bound leakage through the ACTUAL unchanged weighted tone bank for
        # arbitrary phase, rather than checking frequency distance alone.
        count = round(config.sample_rate*window_seconds)
        times = np.arange(count)/config.sample_rate
        basis = np.column_stack([fn(2*np.pi*f*times) for f in frequencies for fn in (np.sin, np.cos)])
        weight = np.sqrt(np.hanning(count))
        projection = np.linalg.pinv(basis*weight[:, None])*weight[None, :]
        leak = np.zeros((len(config.extra_tones), 2))
        for i, tone in enumerate(config.extra_tones):
            extra_basis = np.column_stack([np.sin(2*np.pi*tone.frequency_hz*times),
                                            np.cos(2*np.pi*tone.frequency_hz*times)])
            mapped = projection @ extra_basis
            for channel in range(2):
                leak[i, channel] = levels[i]*np.linalg.norm(mapped[2+2*channel:4+2*channel], ord=2)/small
        for channel, name in enumerate(("A", "B")):
            combined = float(np.sum(leak[:, channel]))
            if combined > .15:
                contributors = tuple(int(i) for i in np.flatnonzero(leak[:, channel] > .01))
                warn(f"{name} 路的分析泄漏保守上界约为通信振幅的 {combined*100:.0f}%；"
                     "这些纯音即使未同频，也可能影响判决，建议移远或调低。", contributors)

        # Actual harmonic/intermodulation levels depend on hardware distortion.
        # Flag only tones sufficiently strong relative to the transmitted data.
        strong = [(None, frequencies[0], 1.)] + [
            (i, tone.frequency_hz, level) for i, (tone, level) in
            enumerate(zip(config.extra_tones, levels)) if level >= small*2]
        nonlinear_guard = max(20., 1/config.unit)
        for i, frequency, _ in strong[1:]:
            for harmonic in (2, 3):
                for name, carrier in zip(("A", "B"), frequencies[1:]):
                    if abs(harmonic*frequency-carrier) <= nonlinear_guard:
                        warn(f"设备失真风险：第 {i+1} 项的 {harmonic} 次谐波靠近 {name} 路；"
                             "扬声器或麦克风非线性时可能产生干扰。", (i,))
        for first in range(len(strong)):
            i, a, _ = strong[first]
            for j, b, _ in strong[first+1:]:
                products = (a+b, abs(a-b), abs(2*a-b), abs(2*b-a))
                for name, carrier in zip(("A", "B"), frequencies[1:]):
                    if any(abs(product-carrier) <= nonlinear_guard for product in products):
                        label_a = "主音" if i is None else f"第 {i+1} 项"
                        warn(f"设备失真风险：{label_a}与第 {j+1} 项的和频、差频或三阶互调"
                             f"靠近 {name} 路；非线性设备可能产生干扰。",
                             tuple(k for k in (i, j) if k is not None))
    if peak_bound >= .95:
        warn(f"叠加振幅的保守上界为 {peak_bound:.2f}，可能超过数字上限；"
             "若实际波形峰值过高，程序会整体等比例降低振幅以防削波，通信声也会随之变弱。",
             tuple(range(len(config.extra_tones))))
    return ToneAnalysis(warnings, per_tone, peak_bound, guard)


def make_protocol(text: str, config: Config):
    config.validate()
    data = text.encode("utf-8")
    if not data or len(data) > MAX_BYTES:
        raise ValueError(f"正文不能为空，且 UTF-8 编码后不能超过 {MAX_BYTES} 字节。")
    header = struct.pack(HEADER_FORMAT, b"AM", 1, 1, len(data), zlib.crc32(data))
    header += crc16(header).to_bytes(2, "big")
    leader = max(8, round(config.leader_seconds / config.unit))
    bits = [1] * leader + SYNC_BITS + bits_of(header)
    payload_start = len(bits)
    for byte in data:
        bits += RECORD_MARKER
        bits += morse_cell(f"{byte:02X}"[0]) + morse_cell(f"{byte:02X}"[1])
        bits += bits_of(bytes([crc8(bytes([byte]))]))
    seconds = .25 + len(bits) * config.unit + .25
    if seconds > 600:
        raise ValueError(f"音频预计长 {seconds:.0f} 秒，超过 10 分钟上限；请减少正文或码元时长。")
    return np.asarray(bits, dtype=np.uint8), {
        "payload_start_unit": payload_start, "leader_units": leader,
        "bytes": len(data), "duration_seconds": seconds, "units": len(bits)}


def synthesize(text: str, config: Config, channel_bits=None):
    bits, info = make_protocol(text, config)
    analysis = analyze_extra_tones(config)
    streams = [bits, bits] if channel_bits is None else channel_bits
    if any(len(b) != len(bits) for b in streams):
        raise ValueError("两路码元长度必须一致。")
    samples = round(config.unit * config.sample_rate)
    guard = round(.25 * config.sample_rate)
    total = len(bits) * samples + guard * 2
    audio = np.empty(total, dtype=np.float32)
    amplitude = 10**(config.masker_dbfs / 20)
    small = amplitude * 10**(config.signal_relative_db / 20)
    # Smooth each ON run at both ends; phase remains continuous through OFF runs.
    ramp_n = max(2, round(min(.005, config.unit*.12) * config.sample_rate))
    ramp = np.sin(np.linspace(0, np.pi/2, ramp_n))**2
    envelopes = []
    for stream in streams:
        env = np.repeat(np.asarray(stream, dtype=np.float32), samples)
        changes = np.diff(np.r_[0, stream, 0].astype(int))
        for index in np.flatnonzero(changes == 1):
            env[index*samples:index*samples+ramp_n] *= ramp
        for index in np.flatnonzero(changes == -1):
            env[index*samples-ramp_n:index*samples] *= ramp[::-1]
        envelopes.append(np.pad(env, (guard, guard)))
    for start in range(0, total, 48000):
        end = min(start+48000, total)
        times = np.arange(start, end, dtype=np.float64) / config.sample_rate
        segment = amplitude * np.sin(2*np.pi*config.masker_hz*times)
        for tone in config.extra_tones:
            segment += amplitude*10**(tone.relative_db/20)*np.sin(2*np.pi*tone.frequency_hz*times)
        for ch in range(2):
            segment += small * envelopes[ch][start:end] * np.sin(2*np.pi*config.frequencies[ch+1]*times)
        audio[start:end] = segment
    fade = min(guard, round(.03 * config.sample_rate))
    audio[:fade] *= np.linspace(0, 1, fade)
    audio[-fade:] *= np.linspace(1, 0, fade)
    peak = float(np.max(abs(audio)))
    gain = .92/peak if peak >= .95 else 1.0
    if gain < 1:
        audio *= gain
    info.update(extra_tones=len(config.extra_tones), mix_attenuation_db=-20*math.log10(gain),
                peak=float(np.max(abs(audio))), interference_warnings=analysis.warnings)
    return audio, info


def write_wav(path, audio, config: Config, include_settings=True):
    pcm = np.rint(np.clip(audio, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(config.sample_rate)
        output.writeframes(pcm.tobytes())
    if include_settings:
        # Public receiver parameters only. No message, bits, or synchronization positions.
        receiver_settings = asdict(config)
        # Extra tones are independent of the protocol; the receiver needs no list.
        receiver_settings.pop("extra_tones", None)
        settings = json.dumps(receiver_settings, separators=(",", ":")).encode("utf-8")
        with open(path, "r+b") as output:
            output.seek(0, 2)
            output.write(b"amdm" + struct.pack("<I", len(settings)) + settings)
            if len(settings) % 2:
                output.write(b"\0")
            size = output.tell()
            output.seek(4)
            output.write(struct.pack("<I", size-8))


def read_wav(path):
    embedded = None
    with open(path, "rb") as source:
        if source.read(4) != b"RIFF":
            raise ValueError("请选择未压缩的 PCM WAV 文件。")
        source.seek(12)
        while chunk := source.read(8):
            if len(chunk) != 8:
                break
            tag, size = struct.unpack("<4sI", chunk)
            if tag == b"amdm" and size <= 8192:
                try:
                    embedded = Config(**json.loads(source.read(size))).validate()
                except (ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError):
                    embedded = None
                if size % 2:
                    source.seek(1, 1)
            else:
                source.seek(size+(size%2), 1)
    try:
        with wave.open(str(path), "rb") as source:
            rate, channels, width = source.getframerate(), source.getnchannels(), source.getsampwidth()
            if source.getnframes() / rate > 1200:
                raise ValueError("文件超过 20 分钟，请先截取包含一帧信号的片段。")
            raw = source.readframes(source.getnframes())
    except wave.Error as exc:
        raise ValueError("仅支持 PCM WAV；请将浮点 WAV、MP3 等先转换为 PCM WAV。") from exc
    if width == 1:
        audio = (np.frombuffer(raw, np.uint8).astype(np.float32)-128)/128
    elif width == 2:
        audio = np.frombuffer(raw, "<i2").astype(np.float32)/32768
    elif width == 3:
        b = np.frombuffer(raw, np.uint8).reshape(-1, 3).astype(np.int32)
        v = b[:, 0] | (b[:, 1]<<8) | (b[:, 2]<<16)
        audio = ((v ^ 0x800000)-0x800000).astype(np.float32)/8388608
    elif width == 4:
        audio = np.frombuffer(raw, "<i4").astype(np.float32)/2147483648
    else:
        raise ValueError("不支持此 WAV 位深。")
    return audio.reshape(-1, channels), rate, embedded


def estimate_scale(audio, rate, frequency):
    """Estimate common clock/pitch error from the continuous reference tone."""
    x = np.asarray(audio)
    if x.ndim == 1:
        x = x[:, None]
    size = min(round(rate * .6), len(x))
    if size < rate * .15:
        return None
    x = x[:size]
    nfft = 1 << (max(size*4, 1024)-1).bit_length()
    spectra = abs(np.fft.rfft(x*np.hanning(size)[:, None], n=nfft, axis=0))**2
    power = np.max(spectra, axis=1)
    bins = np.fft.rfftfreq(nfft, 1/rate)
    allowed = np.flatnonzero(abs(bins-frequency) <= frequency*.025)
    if not len(allowed):
        return None
    k = allowed[np.argmax(power[allowed])]
    floor = np.median(power[(bins > frequency+100) & (bins < min(rate/2, frequency+700))])
    if power[k] < max(1e-12, floor*80) or np.sqrt(np.mean(x*x)) < 1e-6:
        return None
    curve = np.log(np.maximum(power[k-1:k+2], 1e-30))
    delta = .5*(curve[0]-curve[2])/(curve[0]-2*curve[1]+curve[2])
    return (k+float(np.clip(delta, -.5, .5)))*rate/nfft/frequency


class EnvelopeExtractor:
    """Streaming weighted least-squares tone bank with explicit masker rejection.

    Jointly fitting all three tones avoids FFT-bin leakage from the much stronger
    masker into the data channels. Each physical stereo channel is fitted separately.
    """
    def __init__(self, config, rate=None, scale=1.0):
        self.rate = rate or config.sample_rate
        self.hop = max(1, round(self.rate*.005))
        self.window = round(self.rate*min(.03, config.unit*.5))
        time = np.arange(self.window)/self.rate
        basis = np.column_stack([function(2*np.pi*f*scale*time)
                                 for f in config.frequencies for function in (np.sin, np.cos)])
        weight = np.hanning(self.window)
        self.basis = basis
        self.projector = np.linalg.pinv(basis * np.sqrt(weight)[:, None]) * np.sqrt(weight)[None, :]
        self.pending = None
        self.frame_index = 0
        self.clipped = 0
        self.total = 0

    def feed(self, audio):
        x = np.asarray(audio, dtype=np.float64)
        if x.ndim == 1:
            x = x[:, None]
        self.clipped += int(np.count_nonzero(abs(x) >= .995))
        self.total += x.size
        self.pending = x if self.pending is None else np.concatenate([self.pending, x])
        count = max(0, (len(self.pending)-self.window)//self.hop+1)
        if not count:
            return np.empty((0, 2))
        outputs = []
        for first in range(0, count, 256):
            starts = np.arange(first, min(first+256, count))*self.hop
            windows = self.pending[starts[:, None]+np.arange(self.window)[None, :]]
            coefficients = np.einsum("fwc,kw->fkc", windows, self.projector, optimize=True)
            amplitudes = np.sqrt(coefficients[:, 0::2, :]**2+coefficients[:, 1::2, :]**2)
            # Choose a physical input channel independently for each communication tone.
            reference = np.maximum(amplitudes[:, 0, :], 1e-7)
            ratios = amplitudes[:, 1:, :]/reference[:, None, :]
            strongest = np.argmax(amplitudes[:, 1:, :], axis=2)
            selected = np.take_along_axis(ratios, strongest[:, :, None], axis=2)[:, :, 0]
            outputs.append(np.maximum(selected, 1e-9))
        self.pending = self.pending[count*self.hop:]
        self.frame_index += count
        return np.concatenate(outputs)


@dataclass
class Lock:
    start: float
    unit: float
    low: float
    high: float
    score: float


@dataclass
class ChannelResult:
    payload: bytes
    valid_bytes: list[bool]
    confidence: list[float]
    crc_ok: bool
    header_ok: bool
    complete: bool
    sync_score: float = 0.0

    @property
    def text(self):
        return self.payload.decode("utf-8", errors="replace")


@dataclass
class DecodeResult:
    text: str
    red: list[bool]
    channel_a: ChannelResult | None
    channel_b: ChannelResult | None
    warnings: list[str] = field(default_factory=list)
    scale: float = 1.0
    complete: bool = True
    finalized: bool = True

    @property
    def verified(self):
        return bool(self.finalized and self.complete and self.channel_a and self.channel_b
                    and self.channel_a.crc_ok and self.channel_b.crc_ok
                    and not any(self.red))

    def summary(self):
        return {"text": self.text, "red_characters": self.red, "verified": self.verified,
                "complete": self.complete, "finalized": self.finalized, "warnings": self.warnings,
                "frequency_scale": self.scale,
                "channels": [None if c is None else {
                    "text": c.text, "crc32_ok": c.crc_ok, "header_ok": c.header_ok,
                    "valid_bytes": c.valid_bytes, "sync_score": c.sync_score,
                    "complete": c.complete} for c in (self.channel_a, self.channel_b)]}


class Decoder:
    def __init__(self, config: Config, rate=None, scale=1.0):
        config.validate()
        self.config, self.scale = config, scale
        self.extractor = EnvelopeExtractor(config, rate, scale)
        self.dt = self.extractor.hop/self.extractor.rate
        self.t0 = (self.extractor.window-1)/2/self.extractor.rate
        self.env = np.empty((0, 2))
        self.locks = [None, None]
        self.headers = [None, None]
        self.scan_from = [0., 0.]
        self.records = [[], []]
        self.next_record_start = [None, None]
        self.first_complete_at = None

    @property
    def duration(self):
        return self.t0 + max(0, len(self.env)-1)*self.dt

    def feed(self, audio):
        fresh = self.extractor.feed(audio)
        if len(fresh):
            self.env = np.concatenate([self.env, fresh])

    def _sample(self, channel, times):
        return np.interp((np.asarray(times)-self.t0)/self.dt,
                         np.arange(len(self.env)), self.env[:, channel])

    def _symbols(self, channel, start, unit, count):
        centers = start+(np.arange(count)+.5)*unit
        return np.mean([self._sample(channel, centers+shift*unit)
                        for shift in (-.10, 0., .10)], axis=0)

    @staticmethod
    def _score(values, pattern):
        pattern = np.asarray(pattern, dtype=float)
        centered = pattern-pattern.mean()
        v = values-values.mean(axis=-1, keepdims=True)
        return (v @ centered)/(np.linalg.norm(v, axis=-1)*np.linalg.norm(centered)+1e-12)

    def _find_lock(self, channel):
        unit0 = self.config.unit/self.scale
        end = self.duration-len(SYNC_BITS)*unit0*1.016
        start = max(self.scan_from[channel], self.t0+unit0*4)
        if end < start:
            return
        best = None
        pattern = np.asarray(SYNC_BITS)
        # Search only new candidate starts; revisiting a short overlap permits rate refinement.
        starts = np.arange(start, end+self.dt*.1, self.dt)
        if not len(starts):
            return
        for unit in unit0*np.linspace(.985, 1.015, 13):
            for first in range(0, len(starts), 1024):
                candidates = starts[first:first+1024]
                times = candidates[:, None]+(np.arange(len(pattern))+.5)[None, :]*unit
                values = self._sample(channel, times)
                scores = self._score(values, pattern)-.1*abs(unit/unit0-1)
                index = int(np.argmax(scores))
                if best is None or scores[index] > best[0]:
                    best = (float(scores[index]), candidates[index], unit)
        self.scan_from[channel] = max(start, end-3*unit0)
        if not best or best[0] < .86:
            return
        # Fine timing search. Fractional hop centers avoid cumulative rounding errors.
        score, center, initial_unit = best
        for unit in initial_unit*np.linspace(.998, 1.002, 17):
            for candidate in np.arange(center-self.dt, center+self.dt*1.01, self.dt/4):
                values = self._symbols(channel, candidate, unit, len(pattern))
                value = float(self._score(values, pattern))-.1*abs(unit/unit0-1)
                if value > score:
                    score, best_start, best_unit = value, candidate, unit
        if 'best_start' not in locals():
            best_start, best_unit = center, initial_unit
        values = self._symbols(channel, best_start, best_unit, len(pattern))
        high = float(np.median(values[pattern == 1]))
        low = float(np.median(values[pattern == 0]))
        leader = self._symbols(channel, best_start-4*best_unit, best_unit, 4)
        if 20*np.log10(max(high, 1e-9)/max(low, 1e-9)) < 8 or np.mean(leader > (high+low)/2) < .75:
            return
        self.locks[channel] = Lock(best_start, best_unit, low, high, score)

    def _header(self, channel):
        lock = self.locks[channel]
        start = lock.start+len(SYNC_BITS)*lock.unit
        count = HEADER_BYTES*8
        if self.duration < start+count*lock.unit:
            return
        values = self._symbols(channel, start, lock.unit, count)
        raw = pack_bits(values > (lock.low+lock.high)/2)
        if crc16(raw[:-2]) != int.from_bytes(raw[-2:], "big"):
            return
        magic, version, mode, length, checksum = struct.unpack(HEADER_FORMAT, raw[:-2])
        if magic != b"AM" or version != 1 or mode != 1 or not 0 < length <= MAX_BYTES:
            return
        self.headers[channel] = (length, checksum)

    def _record(self, channel, start, lock):
        # A short pilot before EVERY byte corrects time drift and refreshes ON/OFF levels.
        unit = lock.unit
        best = None
        for shift in np.linspace(-.65, .65, 27):
            candidate = start+shift*unit
            values = self._symbols(channel, candidate, unit, len(RECORD_MARKER))
            score = float(self._score(values, RECORD_MARKER))-.008*abs(shift)
            if best is None or score > best[0]:
                best = (score, candidate, values)
        score, actual, pilot = best
        pattern = np.asarray(RECORD_MARKER)
        high = float(np.median(pilot[pattern == 1]))
        low = float(np.median(pilot[pattern == 0]))
        pilot_ok = score > .65 and 20*np.log10(max(high, 1e-9)/max(low, 1e-9)) >= 6
        if not pilot_ok:
            actual, high, low = start, lock.high, lock.low
        values = self._symbols(channel, actual+len(pattern)*unit, unit, CELL*2+8)
        soft = np.clip((values-low)/max(1e-9, high-low), 0, 1)
        decoded = []
        quality = []
        for nibble in range(2):
            cell = soft[nibble*CELL:(nibble+1)*CELL]
            errors = np.mean((MORSE_CELLS-cell[None, :])**2, axis=1)
            order = np.argsort(errors)
            decoded.append(int(order[0]))
            quality.append(float(errors[order[1]]-errors[order[0]]))
        byte = decoded[0]*16+decoded[1]
        received_crc = pack_bits(soft[CELL*2:] > .5)[0]
        ok = bool(pilot_ok and received_crc == crc8(bytes([byte])))
        confidence = min(quality)*max(0, score)
        return byte, ok, confidence, actual+RECORD_UNITS*unit

    def poll(self, final=False, partial=False):
        if len(self.env) < 50:
            return None
        for channel in range(2):
            if self.locks[channel] is None:
                self._find_lock(channel)
            if self.locks[channel] is not None and self.headers[channel] is None:
                self._header(channel)
        good = next((h for h in self.headers if h is not None), None)
        if good is None:
            return None
        length, checksum = good
        results = []
        for channel in range(2):
            lock = self.locks[channel]
            own_header = self.headers[channel]
            if lock is None:
                results.append(None)
                continue
            if own_header is not None and own_header != good:
                # Never silently combine different frames or differently sized messages.
                results.append(None)
                continue
            start = lock.start+(len(SYNC_BITS)+HEADER_BYTES*8)*lock.unit
            if self.next_record_start[channel] is None:
                self.next_record_start[channel] = start
            while len(self.records[channel]) < length:
                current = self.next_record_start[channel]
                if self.duration < current+(RECORD_UNITS+.7)*lock.unit:
                    break
                record = self._record(channel, current, lock)
                self.records[channel].append(record[:3])
                self.next_record_start[channel] = record[3]
            records = self.records[channel]
            payload = bytes(r[0] for r in records)
            complete = len(records) == length
            validity = [r[1] and own_header is not None for r in records]
            crc_ok = bool(complete and own_header is not None and all(validity)
                          and zlib.crc32(payload) == checksum)
            results.append(ChannelResult(payload, validity, [r[2] for r in records], crc_ok,
                                         own_header is not None, complete, lock.score))
        ready = any(c and c.complete for c in results)
        if ready and self.first_complete_at is None:
            self.first_complete_at = self.duration
        waiting_for_peer = False
        if ready and not final and any(c and not c.complete for c in results):
            # Microphone blocks can straddle the two independently estimated frame ends.
            if self.duration < self.first_complete_at+.12:
                waiting_for_peer = True
        live = not final and (not ready or waiting_for_peer)
        if live and not partial:
            return None
        if not any(c and c.payload for c in results):
            return None
        warnings = []
        if self.extractor.clipped/max(1, self.extractor.total) > .0001:
            warnings.append("录音存在削波/过载，请降低播放音量或麦克风增益。")
        if abs(self.scale-1) > .001:
            warnings.append(f"已补偿频率/时钟偏差 {(self.scale-1)*100:+.3f}%。")
        result = combine_channels(*results, partial=live)
        result.warnings += warnings
        result.scale = self.scale
        return result

    def progress(self):
        header = next((h for h in self.headers if h), None)
        if header:
            return f"接收中 · {max(map(len, self.records))}/{header[0]} 字节"
        if any(self.locks):
            return "读取帧头"
        return "同步中"


def combine_channels(a, b, partial=False):
    present = [(i, c) for i, c in enumerate((a, b)) if c is not None]
    if not present:
        raise ValueError("没有可解码通道。")
    _, chosen = max(present, key=lambda item: (
        item[1].crc_ok, item[1].complete, sum(item[1].valid_bytes),
        np.mean(item[1].confidence) if item[1].confidence else 0))
    data = chosen.payload
    chars, red = [], []
    index = 0
    while index < len(data):
        first = data[index]
        size = 1 if first < 128 else (2 if 0xC2 <= first <= 0xDF else (
            3 if 0xE0 <= first <= 0xEF else (4 if 0xF0 <= first <= 0xF4 else 1)))
        # UTF-8 is received by byte. Wait for a WHOLE character in live previews.
        if partial and index+size > len(data):
            break
        fragment = data[index:index+size]
        try:
            char = fragment.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            char, size, fragment = "\ufffd", 1, data[index:index+1]
        verified = bool(a and b and (partial or a.crc_ok or b.crc_ok) and a.header_ok and b.header_ok
                        and len(a.payload) >= index+size and len(b.payload) >= index+size
                        and a.payload[index:index+size] == b.payload[index:index+size]
                        and all(a.valid_bytes[index:index+size])
                        and all(b.valid_bytes[index:index+size]) and char != "\ufffd")
        chars.append(char)
        red.append(not verified)
        index += size
    warnings = []
    if not a or not b:
        warnings.append("仅有一路成功同步；采用该路结果，所有字符标红。")
    elif a.payload != b.payload:
        warnings.append("两路内容不一致；优先采用整帧校验通过的一路，否则采用有效字节更多的一路。")
    if not partial and not (a and a.crc_ok and b and b.crc_ok):
        warnings.append("至少一路未通过完整帧 CRC32 校验，红字不可视为已确认内容。")
    complete = chosen.complete and not partial
    if not complete and not partial:
        warnings.append("音频被截断，只显示已收到的部分。")
    return DecodeResult("".join(chars), red, a, b, warnings, complete=complete, finalized=not partial)


def decode_audio(audio, rate, config: Config, progress=None, cancel=None, on_partial=None):
    x = np.asarray(audio)
    if x.ndim == 1:
        x = x[:, None]
    if not np.all(np.isfinite(x)):
        raise ValueError("音频含 NaN/Inf，无法解析。")
    if max(config.frequencies) >= rate/2-100:
        raise ValueError("文件采样率不足以覆盖设定的通信频率。")
    scale = None
    # Ignore initial silence/noise when estimating the continuously transmitted masker.
    for start in range(0, min(len(x), rate*15), max(1, round(rate*.5))):
        scale = estimate_scale(x[start:start+round(rate*.6)], rate, config.masker_hz)
        if scale is not None:
            break
    decoder = Decoder(config, rate, scale or 1.0)
    step = max(1, round(rate*.25)) if on_partial else rate
    last_preview = None
    for start in range(0, len(x), step):
        if cancel is not None and cancel.is_set():
            raise InterruptedError("已停止解析。")
        decoder.feed(x[start:start+step])
        result = decoder.poll(partial=on_partial is not None)
        if progress:
            progress(decoder.progress())
        if result and result.finalized:
            return result
        if result and result.text and on_partial:
            signature = (result.text, tuple(result.red))
            if signature != last_preview:
                on_partial(result)
                last_preview = signature
    result = decoder.poll(final=True)
    if result:
        return result
    raise ValueError("没有找到有效帧头。请确认主音、两路频偏和码元时长一致，并从同步前导开始录音。")
