"""Deterministic waveform tests; does not play audio or access the microphone."""
from __future__ import annotations
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import threading
import unittest
import wave
import numpy as np
from modem import (Config, Decoder, ChannelResult, combine_channels, synthesize, decode_audio,
                   write_wav, read_wav, make_protocol, RECORD_UNITS, HEADER_BYTES, SYNC_BITS,
                   ExtraTone, analyze_extra_tones, parse_extra_tone)


class ModemTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = Config()
        cls.text = "AB中文🙂"
        cls.audio, cls.info = synthesize(cls.text, cls.config)

    def assert_decoded(self, audio, config=None, rate=None, text=None):
        config = config or self.config
        result = decode_audio(audio, rate or config.sample_rate, config)
        self.assertEqual(result.text, self.text if text is None else text)
        self.assertTrue(result.verified, result.summary())
        return result

    def test_clean_utf8(self):
        self.assert_decoded(self.audio)

    def test_wav_without_metadata(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            path = Path(directory)/"test.wav"
            write_wav(path, self.audio, self.config, False)
            audio, rate, embedded = read_wav(path)
            self.assertIsNone(embedded)
            self.assert_decoded(audio, rate=rate)

    def test_settings_only_metadata(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            path = Path(directory)/"test.wav"
            write_wav(path, self.audio, self.config, True)
            audio, rate, embedded = read_wav(path)
            self.assertEqual(embedded, self.config)
            self.assert_decoded(audio, embedded, rate)
            with path.open("rb") as stream:
                stream.seek(12)
                while chunk := stream.read(8):
                    import struct
                    tag, size = struct.unpack("<4sI", chunk)
                    if tag == b"amdm":
                        settings = json.loads(stream.read(size))
                        self.assertEqual(set(settings), set(vars(self.config)) - {"extra_tones"})
                        break
                    stream.seek(size+size%2, 1)

    def test_broadband_noise(self):
        rng = np.random.default_rng(153)
        noisy = self.audio+rng.normal(0, 10**(-38/20), len(self.audio))
        self.assert_decoded(noisy)

    def test_heavy_noise_never_reported_verified(self):
        rng = np.random.default_rng(887)
        noisy = self.audio+rng.normal(0, .20, len(self.audio))
        try:
            result = decode_audio(noisy, self.config.sample_rate, self.config)
        except ValueError:
            return
        self.assertFalse(result.verified)

    def test_off_band_interference(self):
        times = np.arange(len(self.audio))/self.config.sample_rate
        noise = .04*np.sin(2*np.pi*703*times)+.035*np.sin(2*np.pi*2753*times)
        self.assert_decoded(self.audio+noise)

    def test_short_echo(self):
        echo = np.pad(self.audio[:-960], (960, 0))*.3
        self.assert_decoded(self.audio+echo)

    def test_late_echo(self):
        echo = np.pad(self.audio[:-2400], (2400, 0))*.25
        self.assert_decoded(self.audio+echo)

    def test_gain_change(self):
        self.assert_decoded(self.audio*np.linspace(.15, .9, len(self.audio)))

    def test_low_gain(self):
        self.assert_decoded(self.audio*.015)

    def test_clock_fast(self):
        self.assert_decoded(np.interp(np.arange(0, len(self.audio)-1, 1.003),
                                      np.arange(len(self.audio)), self.audio))

    def test_clock_slow(self):
        self.assert_decoded(np.interp(np.arange(0, len(self.audio)-1, .997),
                                      np.arange(len(self.audio)), self.audio))

    def test_initial_noise_and_fractional_offset(self):
        rng = np.random.default_rng(55)
        prefix = rng.normal(0, .0003, 70000+123)
        self.assert_decoded(np.concatenate([prefix, self.audio]))

    def test_stereo_phase_inversion(self):
        self.assert_decoded(np.column_stack([self.audio, -self.audio]))

    def test_other_frequencies_and_sample_rate(self):
        config = replace(self.config, sample_rate=44100, masker_hz=1271.3,
                         offset_a=133.4, offset_b=291.7, unit_ms=73)
        audio, _ = synthesize("test!", config)
        self.assert_decoded(audio, config, text="test!")

    def test_16khz(self):
        config = replace(self.config, sample_rate=16000)
        audio, _ = synthesize("16K", config)
        self.assert_decoded(audio, config, text="16K")

    def test_streaming_chunks(self):
        decoder = Decoder(self.config)
        result = None
        chunk_size = round(self.config.sample_rate*.08)
        for index, start in enumerate(range(0, len(self.audio), chunk_size)):
            decoder.feed(self.audio[start:start+chunk_size])
            if index % 4 == 0:
                result = decoder.poll()
                if result:
                    break
        result = result or decoder.poll(final=True)
        self.assertIsNotNone(result)
        self.assertEqual(result.text, self.text)
        self.assertTrue(result.verified, result.summary())

    def test_one_channel_missing_is_red(self):
        bits, _ = make_protocol("ABC", self.config)
        audio, _ = synthesize("ABC", self.config, [bits, np.zeros_like(bits)])
        result = decode_audio(audio, self.config.sample_rate, self.config)
        self.assertEqual(result.text, "ABC")
        self.assertEqual(result.red, [True]*3)
        self.assertFalse(result.verified)

    def test_disagree_only_affected_character_red(self):
        bits, info = make_protocol("ABC", self.config)
        alternate, _ = make_protocol("AXC", self.config)
        damaged = bits.copy()
        start = info["payload_start_unit"]+RECORD_UNITS
        damaged[start:start+RECORD_UNITS] = alternate[start:start+RECORD_UNITS]
        audio, _ = synthesize("ABC", self.config, [bits, damaged])
        result = decode_audio(audio, self.config.sample_rate, self.config)
        self.assertEqual(result.text, "ABC")
        self.assertEqual(result.channel_b.text, "AXC")
        self.assertEqual(result.red, [False, True, False])
        self.assertTrue(result.channel_a.crc_ok)
        self.assertFalse(result.channel_b.crc_ok)

    def test_same_wrong_data_not_verified(self):
        bits, info = make_protocol("ABC", self.config)
        alternate, _ = make_protocol("AXC", self.config)
        start = info["payload_start_unit"]+RECORD_UNITS
        bits[start:start+RECORD_UNITS] = alternate[start:start+RECORD_UNITS]
        audio, _ = synthesize("ABC", self.config, [bits, bits])
        result = decode_audio(audio, self.config.sample_rate, self.config)
        self.assertEqual(result.text, "AXC")
        self.assertEqual(result.red, [True]*3)
        self.assertFalse(result.verified)

    def test_bad_header_on_one_channel(self):
        bits, info = make_protocol("ABC", self.config)
        damaged = bits.copy()
        damaged[info["leader_units"]+len(SYNC_BITS)+10] ^= 1
        audio, _ = synthesize("ABC", self.config, [damaged, bits])
        result = decode_audio(audio, self.config.sample_rate, self.config)
        self.assertEqual(result.text, "ABC")
        self.assertEqual(result.red, [True]*3)
        self.assertFalse(result.verified)

    def test_truncated_file_is_partial_and_red(self):
        end = .25+(self.info["payload_start_unit"]+RECORD_UNITS*2.5)*self.config.unit
        result = decode_audio(self.audio[:round(end*self.config.sample_rate)],
                              self.config.sample_rate, self.config)
        self.assertEqual(result.text, "AB")
        self.assertFalse(result.complete)
        self.assertEqual(result.red, [True, True])

    def test_noise_is_not_a_message(self):
        rng = np.random.default_rng(7)
        with self.assertRaises(ValueError):
            decode_audio(rng.normal(0, .01, self.config.sample_rate*18),
                         self.config.sample_rate, self.config)

    def test_continuous_masker_is_not_a_message(self):
        t = np.arange(self.config.sample_rate*18)/self.config.sample_rate
        with self.assertRaises(ValueError):
            decode_audio(.2*np.sin(2*np.pi*self.config.masker_hz*t),
                         self.config.sample_rate, self.config)

    def test_invalid_parameters(self):
        for change in ({"unit_ms": 5}, {"offset_a": -100}, {"masker_dbfs": 0},
                       {"masker_hz": float("nan")}, {"offset_b": 100}):
            with self.assertRaises(ValueError):
                replace(self.config, **change).validate()
        with self.assertRaises(ValueError):
            make_protocol("", self.config)

    def test_cancellation(self):
        event = threading.Event()
        event.set()
        with self.assertRaises(InterruptedError):
            decode_audio(self.audio, self.config.sample_rate, self.config, cancel=event)

    def test_wrong_frequency_rejected(self):
        with self.assertRaises(ValueError):
            decode_audio(self.audio, self.config.sample_rate, replace(self.config, masker_hz=1400))

    def test_extra_tones_do_not_change_protocol(self):
        config = replace(self.config, extra_tones=[ExtraTone(500, -18), ExtraTone(730, -18), ExtraTone(3100, -18)])
        self.assertFalse(analyze_extra_tones(config).warnings)
        self.assertTrue(np.array_equal(make_protocol(self.text, config)[0], make_protocol(self.text, self.config)[0]))
        audio, info = synthesize(self.text, config)
        self.assertEqual(info["extra_tones"], 3)
        # Intentionally decode with the old base parameters: no list of extras is needed.
        self.assert_decoded(audio, self.config)

    def test_extra_tone_actually_present_at_requested_level(self):
        config = replace(self.config, extra_tones=[ExtraTone(701.25, -12)])
        audio, info = synthesize(self.text, config)
        start, end = 24000, 36000
        times = np.arange(start, end)/self.config.sample_rate
        expected = 10**((config.masker_dbfs-12)/20)*np.sin(2*np.pi*701.25*times)
        np.testing.assert_allclose(audio[start:end]-self.audio[start:end], expected, atol=4e-8)
        self.assertEqual(info["mix_attenuation_db"], 0)

    def test_no_small_fixed_limit_on_number_of_tones(self):
        tones = [ExtraTone(250+index*6.37, -60) for index in range(100)]
        config = replace(self.config, extra_tones=tones)
        audio, info = synthesize("A", config)
        self.assertEqual(info["extra_tones"], 100)
        self.assert_decoded(audio, self.config, text="A")

    def test_direct_frequency_and_main_clock_conflicts_warn(self):
        for frequency, expected in ((1920, "A 路"), (2040, "B 路"), (1810, "时钟校准")):
            with self.subTest(frequency=frequency):
                analysis = analyze_extra_tones(replace(self.config, extra_tones=[ExtraTone(frequency)]))
                self.assertTrue(any(expected in w for w in analysis.warnings), analysis.warnings)
                self.assertTrue(analysis.per_tone[0])

    def test_harmonics_and_intermodulation_warn(self):
        harmonic = analyze_extra_tones(replace(self.config, extra_tones=[ExtraTone(960, -12)]))
        self.assertTrue(any("谐波" in warning for warning in harmonic.warnings))
        mixing = analyze_extra_tones(replace(self.config, extra_tones=[ExtraTone(700), ExtraTone(1220)]))
        self.assertTrue(any("互调" in warning for warning in mixing.warnings))

    def test_many_strong_tones_avoid_clipping(self):
        config = replace(self.config, extra_tones=[ExtraTone(500, 12), ExtraTone(730, 12), ExtraTone(3100, 12)])
        analysis = analyze_extra_tones(config)
        self.assertTrue(any("数字上限" in warning for warning in analysis.warnings))
        audio, info = synthesize("CLIP", config)
        self.assertGreater(info["mix_attenuation_db"], 0)
        self.assertLessEqual(float(np.max(abs(audio))), .920001)
        self.assert_decoded(audio, self.config, text="CLIP")
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            path = Path(directory)/"scaled.wav"
            write_wav(path, audio, config, False)
            saved, rate, _ = read_wav(path)
            self.assert_decoded(saved, self.config, rate=rate, text="CLIP")

    def test_tone_settings_round_trip_and_legacy_config(self):
        config = replace(self.config, extra_tones=[ExtraTone(500, -18), ExtraTone(3100, -24)])
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            path = Path(directory)/"config.json"
            config.save(path)
            self.assertEqual(Config.load(path), config)
            legacy = dict(vars(self.config))
            legacy.pop("extra_tones")
            path.write_text(json.dumps(legacy), encoding="utf-8")
            self.assertEqual(Config.load(path), self.config)
            audio, _ = synthesize("FILE", config)
            wav = Path(directory)/"settings.wav"
            write_wav(wav, audio, config, True)
            samples, rate, embedded = read_wav(wav)
            self.assertEqual(embedded.extra_tones, [])
            self.assert_decoded(samples, embedded, rate=rate, text="FILE")

    def test_bad_extra_tones_rejected(self):
        for tone in (ExtraTone(0), ExtraTone(24000), ExtraTone(float("nan")),
                     ExtraTone(500, float("inf")), ExtraTone(500, 13)):
            with self.subTest(tone=tone), self.assertRaises(ValueError):
                replace(self.config, extra_tones=[tone]).validate()
        self.assertEqual(parse_extra_tone("700:-18"), ExtraTone(700, -18))
        self.assertEqual(parse_extra_tone("700"), ExtraTone(700, -12))

    def test_live_stream_displays_characters_before_frame_end(self):
        text = "你A🙂B"
        audio, _ = synthesize(text, self.config)
        decoder = Decoder(self.config)
        previews = []
        final = None
        step = round(self.config.sample_rate*.08)
        last = None
        for start in range(0, len(audio), step):
            decoder.feed(audio[start:start+step])
            result = decoder.poll(partial=True)
            if result is None:
                continue
            if result.finalized:
                final = result
                break
            signature = (result.text, tuple(result.red))
            if result.text and signature != last:
                previews.append((start+step, result))
                last = signature
        final = final or decoder.poll(final=True)
        self.assertTrue(final.verified)
        self.assertEqual(final.text, text)
        self.assertGreaterEqual(len(previews), 3)
        self.assertEqual(previews[0][1].text, "你")
        self.assertLess(previews[0][0], len(audio)*.6)
        for _, result in previews:
            self.assertTrue(text.startswith(result.text), result.text)
            self.assertNotIn("\ufffd", result.text)
            self.assertFalse(result.finalized)
            self.assertFalse(result.verified)

    def test_live_utf8_waits_for_whole_character(self):
        raw = "中".encode("utf-8")
        def channel(data):
            return ChannelResult(data, [True]*len(data), [.9]*len(data), False, True, False)
        for length in (1, 2):
            partial = combine_channels(channel(raw[:length]), channel(raw[:length]), partial=True)
            self.assertEqual(partial.text, "")
            self.assertFalse(partial.finalized)
        partial = combine_channels(channel(raw), channel(raw), partial=True)
        self.assertEqual(partial.text, "中")
        self.assertEqual(partial.red, [False])
        self.assertFalse(partial.verified)

    def test_live_mismatch_and_final_crc_update(self):
        a = ChannelResult(b"ABC", [True]*3, [.9]*3, False, True, False)
        b = ChannelResult(b"AXC", [True]*3, [.8]*3, False, True, False)
        partial = combine_channels(a, b, partial=True)
        self.assertEqual(partial.text, "ABC")
        self.assertEqual(partial.red, [False, True, False])
        final = combine_channels(a, b)
        self.assertEqual(final.red, [True]*3)
        self.assertTrue(final.finalized)

    def test_file_decode_partial_callback(self):
        previews = []
        final = decode_audio(self.audio, self.config.sample_rate, self.config, on_partial=previews.append)
        self.assertTrue(final.verified)
        self.assertTrue(previews)
        self.assertEqual(previews[0].text, "A")
        self.assertTrue(any(p.text == "AB中" for p in previews))
        self.assertTrue(all(not p.finalized and self.text.startswith(p.text) for p in previews))


if __name__ == "__main__":
    unittest.main(verbosity=2)
