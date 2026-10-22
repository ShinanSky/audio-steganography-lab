# Auditorily Covert Audio Information Transmission Based on Spectral Masking

[简体中文](readmecn.md)

A Python research prototype for sending text through audio. A generator embeds text in two weak tones near a stronger continuous tone; a reader recovers the text from a WAV file or microphone input. The research goal is to make data-bearing sounds less noticeable to listeners while remaining machine-detectable.

**Current implementation:** a configurable continuous sinusoidal masking tone and two fixed neighboring data frequencies. This repository does not analyze music, track changing spectral peaks, remove bands from music, or calculate psychoacoustic masking thresholds. Auditory concealment is a research objective, not an experimentally established property of this implementation.

## Features

- Unicode text, including Chinese, English, line breaks, and emoji.
- PCM WAV export, live playback, WAV decoding, and microphone reception.
- Incremental display of complete characters in a large text area.
- Duplicate payloads in two frequency bands, with byte and frame checks.
- Red highlighting for characters that fail the validation rules.
- Multiple additional tones with independent levels and interference warnings.
- JSON settings import/export and optional public settings embedded in WAV.
- Monochrome Tkinter interface; labels and diagnostic messages are currently in Chinese.

## Installation

Use **Python 3.10+ with Tkinter**. `requirements.txt` lists NumPy and sounddevice. File synthesis and decoding use NumPy and the standard library; playback and recording additionally require sounddevice and a working PortAudio backend.

### Windows

Install Python with Tkinter and make `py` or `python` available. Double-click `安装依赖.cmd` to create `.venv` and install dependencies. Then open `启动生成器.cmd` (generator) or `启动读取器.cmd` (reader). Both launchers use the same local environment and contain no user-specific Python paths.

Alternatively:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe generator.py
.\.venv\Scripts\python.exe reader.py
```

### macOS

Install Python 3.10+ with working Tkinter support. Open `启动生成器.command` or `启动读取器.command`. First launch creates `.venv-macos` and installs dependencies, requiring an Internet connection. `安装依赖.command` installs dependencies separately. Allow microphone access when prompted.

If downloading or extracting the files did not preserve execution permissions, open a terminal in this folder and run:

```bash
bash 启动生成器.command
bash 启动读取器.command
```

The launcher searches common Python installation locations and the command search path. Set `AUDIO_MORSE_PYTHON` to select an executable explicitly. macOS scripts have syntax and simulated-entry coverage; physical Mac operation has not been verified.

### Linux / manual setup

Install your distribution's Tkinter and PortAudio packages if needed, then:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python generator.py
python reader.py
```

Use the same environment's Python for all commands below. On Windows without activation, substitute `.\.venv\Scripts\python.exe`; for the macOS launcher environment, use `.venv-macos/bin/python`.

## Quick start

### Decode a file

1. Open the reader and keep the default parameters.
2. Import `demo_你好.wav`: the expected text is `你好`, with both channels validated.
3. In the generator, enter your own text and save a WAV file.
4. Import it into the reader; complete Unicode characters appear as they are decoded.
5. After changing signal settings, export/import the JSON configuration at both ends, or enable public settings in the WAV.

`demo_红字校验.wav` deliberately carries `ABC` on channel A and `AXC` on channel B. The expected display is `ABC`, with only the middle `B` red. `demo_额外频率.wav` adds three tones configured in `demo_extra_settings.json`. The supplied `demo_*result.json` files are example decoding results, not personal messages.

### Receive through a microphone

Select the input device and start reception **before** playback. Begin at a short distance, capture the full preamble, and match both ends' parameters. Noise suppression, echo cancellation, automatic gain control, clipping, and strong reflections can prevent decoding. Each reception handles one frame; start reception again for the next message.

### Add tones

Click `额外叠加频率…` in the generator. Each additional tone has an independent frequency and amplitude level relative to the main tone. The checker warns about modeled channel overlap, analysis leakage, harmonics, intermodulation, duplicate frequencies, and excessive combined amplitude. Warnings do not remove selected tones automatically. Peak protection scales the entire waveform if needed, preserving relative tone amplitudes. These checks do not guarantee interference-free physical reception.

## Signal and protocol

| Parameter | Default |
| --- | --- |
| Sample rate | 48,000 Hz |
| Continuous masking tone | 1,800 Hz |
| Data channels A / B | 1,920 Hz / 2,040 Hz |
| Main-tone peak | −12 dBFS |
| Each data tone relative to the main tone | −30 dB in amplitude |
| Morse unit | 60 ms |
| Continuous-on leader | Approximately 1.5 s |

The data tones use on/off signaling with smoothed transitions. The main tone and optional additional tones remain continuous during transmission.

```text
0.25 s of main tone
continuous-on leader
20 repetitions of 10 (40 symbols)
32-bit synchronization word
header: version, mode, UTF-8 byte count, payload CRC32, header CRC16
each byte: short synchronization pattern + two Morse hex slots + CRC8
0.25 s of trailing main tone
```

Text becomes UTF-8 bytes, then hexadecimal digits represented by standard Morse symbols for `0–9` and `A–F`. This is not a direct Morse alphabet for Chinese. Each payload byte occupies 68 units: approximately 4.08 seconds by default, plus frame overhead. Default `你好` transmission takes approximately 36.56 seconds. The limits are 256 UTF-8 bytes and 10 minutes of audio.

The reader uses windowed joint tone fitting, relative-amplitude normalization, preamble-based thresholds, main-tone frequency/clock estimation, and per-byte resynchronization to mitigate interference.

## Validation and red highlighting

The two channels are decoded independently. During reception, black characters indicate agreement and successful corresponding byte CRC8 checks; frame CRC32 remains pending. Final coloring also considers frame validation.

- Disagreement or a missing/invalid corresponding byte produces red highlighting.
- The display prefers a complete payload that passes CRC32; otherwise, it shows an available candidate.
- If neither complete payload passes CRC32, all displayed characters are red.
- `verified` is true only when both complete channels pass frame validation and no characters are red.
- Interrupted or truncated reception can yield partial text, but is not reported as fully verified.

Saving text in the GUI writes a TXT file and a neighboring `.validation.json` report. Plain text does not preserve coloring. CRC detects accidental errors; it does not provide correction or message authentication. Agreement is not an absolute guarantee of correctness.

## Command line

```bash
python generator.py --message "Hello, 你好!" --output message.wav
python reader.py --input message.wav --json result.json
python generator.py --message "HELLO" --play
python generator.py --message "HELLO" --include-settings --output message.wav
python generator.py --message "HELLO" --config demo_settings.json --output message.wav
python generator.py --message "HELLO" --tone 500:-18 --tone 730:-18 --output layered.wav
python reader.py --input message.wav --config demo_settings.json
python selftest.py
```

Reader exit code `0` means fully verified; `2` means decoded but not fully verified. Failures such as an absent valid frame produce a nonzero error exit. Embedded WAV settings override receiver settings unless `--ignore-settings` is specified. The generator CLI includes no settings unless `--include-settings` is supplied. Optional metadata contains public signal parameters, not plaintext; messages are recovered from the waveform.

## Limitations and testing

- **Listening tests have not established auditory concealment.** Low digital amplitude alone does not establish masking; audibility depends on listeners, playback levels, equipment, and signal configuration.
- This is experimental information hiding and signal encoding, not encryption. The frequencies and protocol can be analyzed.
- This version uses synthesized tones, not MP3/WAV/OGG music as a carrier. There is no dynamic music tracking, masking-threshold model, or dedicated ultrasonic mode.
- File input supports uncompressed 8/16/24/32-bit integer PCM WAV. MP3, OGG, and floating-point WAV are not supported. Lossy conversion may destroy the payload.
- There is no forward error correction or automatic retransmission.
- `selftest.py` contains 39 deterministic cases covering waveform round trips, Unicode, streaming, disagreement marking, noise, echoes, gain/clock changes, truncation, and additional-tone checks. Tests do not play sound or access the microphone. Simulated results do not establish real-room performance or inaudibility.

## Repository and publication notes

| File | Purpose |
| --- | --- |
| `generator.py`, `reader.py` | GUI and CLI entry points |
| `modem.py` | Protocol, synthesis, PCM WAV I/O, decoding |
| `ui_common.py` | Shared widgets, settings, audio-device support |
| `launcher.py`, `windows_launch.cmd` | Shared application and Windows environment launchers |
| `*.cmd`, `*.command`, `mac_launch.sh` | Platform launch/install scripts |
| `selftest.py`, `requirements.txt` | Tests and Python dependencies |
| `demo_*` | Synthetic audio, public settings, example results |

The publication copy excludes bundled platform-specific dependencies, bytecode caches, and the generated historical test report. The previous macOS guide is consolidated into this README and its Chinese translation. `.gitignore` excludes local environments, optional `vendor/`, logs, and common generated outputs while preserving supplied examples. Put personal recordings and decoded messages in the ignored `recordings/`, `outputs/`, or `decoded/` directories. Ignore rules do not remove already tracked files from the Git index or history.

## License

A project license has not been selected. No license or author identity has been invented for this publication copy. Third-party dependencies have their own licenses and are installed separately.
