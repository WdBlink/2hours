import array
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform == "darwin" and shutil.which("swiftc") and shutil.which("ffmpeg"),
                     "requires macOS Swift and ffmpeg")
class RecordingAudioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.root = Path(cls.directory.name)
        source = (ROOT / "vendor/blurt/recorder/macos/BlurtRecorder.swift").read_text()
        helper = source[source.index("final class RecordingAudioTrack {"):source.index("// MARK: - Recorder")]
        harness = r'''import Foundation
import AVFoundation
import CoreMedia
__HELPER__
let mode = CommandLine.arguments[1]
let writer = try AVAssetWriter(outputURL: URL(fileURLWithPath: CommandLine.arguments[2]), fileType: .mp4)
let input = AVAssetWriterInput(mediaType: .audio, outputSettings: [AVFormatIDKey: kAudioFormatMPEG4AAC,
    AVSampleRateKey: 48000, AVNumberOfChannelsKey: 1, AVEncoderBitRateKey: 96000])
input.expectsMediaDataInRealTime = true
writer.add(input)
let track = RecordingAudioTrack(input: input)
let base = CMTime(seconds: 12345, preferredTimescale: 48000)
func at(_ seconds: Double) -> CMTime { CMTimeAdd(base, CMTime(seconds: seconds, preferredTimescale: 48000)) }
func ready() {
    let deadline = Date().addingTimeInterval(3)
    while !input.isReadyForMoreMediaData {
        precondition(writer.status == .writing && Date() < deadline)
        Thread.sleep(forTimeInterval: 0.001)
    }
}
func sample(_ seconds: Double, duration: Double = 0.1, kind: String) -> CMSampleBuffer {
    let rate = kind.contains("44100") ? 44100.0 : 48000.0
    let channels: AVAudioChannelCount = kind.contains("stereo") ? 2 : 1
    let isFloat = kind.contains("float")
    let interleaved = !kind.contains("planar")
    let format = AVAudioFormat(commonFormat: isFloat ? .pcmFormatFloat32 : .pcmFormatInt16,
                              sampleRate: rate, channels: channels, interleaved: interleaved)!
    let count = Int((duration * rate).rounded())
    let pcm = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(count))!
    pcm.frameLength = AVAudioFrameCount(count)
    for i in 0..<count {
        let value = sin(2 * Double.pi * 440 * (seconds + Double(i) / rate)) * 0.25
        for channel in 0..<Int(channels) {
            let buffer = interleaved ? 0 : channel
            let index = interleaved ? i * Int(channels) + channel : i
            if isFloat { pcm.floatChannelData![buffer][index] = Float(value) }
            else { pcm.int16ChannelData![buffer][index] = Int16(value * 32767) }
        }
    }
    var timing = CMSampleTimingInfo(duration: CMTime(value: 1, timescale: CMTimeScale(rate)),
                                   presentationTimeStamp: at(seconds), decodeTimeStamp: .invalid)
    var result: CMSampleBuffer?
    precondition(CMSampleBufferCreate(allocator: nil, dataBuffer: nil, dataReady: false,
        makeDataReadyCallback: nil, refcon: nil, formatDescription: format.formatDescription,
        sampleCount: count, sampleTimingEntryCount: 1, sampleTimingArray: &timing,
        sampleSizeEntryCount: 0, sampleSizeArray: nil, sampleBufferOut: &result) == noErr)
    precondition(CMSampleBufferSetDataBufferFromAudioBufferList(result!, blockBufferAllocator: nil,
        blockBufferMemoryAllocator: nil, flags: 0, bufferList: pcm.audioBufferList) == noErr)
    precondition(CMSampleBufferSetDataReady(result!) == noErr)
    return result!
}
precondition(writer.startWriting())
writer.startSession(atSourceTime: base)
track.start(at: base)
ready(); track.fill(until: at(20))
precondition(track.paddingFrames == 48000 && CMTimeCompare(track.nextPTS, at(1)) == 0)
if mode == "overlap" {
    ready(); precondition(track.append(sample(0.75, duration: 0.5, kind: "float_stereo_44100")))
    ready(); track.fill(until: at(1.25))
    precondition(abs(CMTimeGetSeconds(CMTimeSubtract(track.nextPTS, at(1.25)))) < 0.002)
    let before = track.nextPTS
    precondition(!track.append(sample(0.5, kind: "int16")) && track.nextPTS == before)
    ready(); track.fill(until: at(2.25))
    ready(); precondition(track.append(sample(2.25, duration: 0.5, kind: "float_planar_stereo")))
} else if mode == "packets" {
    for tick in 0..<100 {
        ready(); _ = track.append(sample(1 + Double(tick * 1024) / 44100,
                                        duration: 1024.0 / 44100, kind: "float_planar_stereo_44100"))
    }
} else if mode == "change" {
    for (index, kind) in ["int16", "float_stereo", "float_planar_stereo_44100", "int16_stereo_44100"].enumerated() {
        for tick in 0..<10 {
            ready(); precondition(track.append(sample(Double(index + 1) + Double(tick) / 10, kind: kind)))
        }
    }
} else {
    for tick in 10..<30 {
        ready(); precondition(track.append(sample(Double(tick) / 10, kind: mode)))
    }
}
let end = mode == "overlap" ? 2.75 : mode == "change" ? 5 : mode == "packets" ? 1 + 102400.0 / 44100 : 3
ready(); track.fill(until: at(end))
let duration = CMTimeGetSeconds(CMTimeSubtract(track.nextPTS, base))
input.markAsFinished()
writer.endSession(atSourceTime: track.nextPTS)
let done = DispatchSemaphore(value: 0)
writer.finishWriting { done.signal() }
precondition(done.wait(timeout: .now() + 10) == .success && writer.status == .completed)
print("{\"duration\":\(duration),\"padding_frames\":\(track.paddingFrames)}")
'''.replace("__HELPER__", helper)
        (cls.root / "main.swift").write_text(harness)
        compiled = subprocess.run([shutil.which("swiftc"), "-suppress-warnings", str(cls.root / "main.swift"),
                                   "-o", str(cls.root / "check")], capture_output=True, text=True, timeout=30)
        if compiled.returncode:
            cls.directory.cleanup()
            raise AssertionError(compiled.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def render(self, mode):
        video = self.root / f"{mode}.mp4"
        result = subprocess.run([str(self.root / "check"), mode, str(video)], capture_output=True,
                                text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        raw = subprocess.check_output([shutil.which("ffmpeg"), "-v", "error", "-xerror", "-i", str(video),
                                       "-map", "0:a:0", "-ac", "1", "-ar", "48000", "-f", "f32le", "-"], timeout=20)
        samples = array.array("f", raw)
        if sys.byteorder != "little":
            samples.byteswap()
        return json.loads(result.stdout), samples

    def assert_silence(self, samples, start, end):
        window = samples[int(start * 48000):int(end * 48000)]
        self.assertLess(max(map(abs, window)), 0.002)

    def assert_tone(self, samples, start, end):
        window = samples[int(start * 48000):int(end * 48000)]
        self.assertGreater(sum(value * value for value in window) / len(window), 0.01)
        crossings = sum(a < 0 <= b for a, b in zip(window, window[1:]))
        self.assertAlmostEqual(crossings / (len(window) / 48000), 440, delta=5)

    def test_silence_then_normalized_audio_preserves_pitch_and_duration(self):
        for kind in ("int16", "float", "float_stereo", "float_planar_stereo",
                     "float_stereo_44100", "int16_stereo_44100"):
            with self.subTest(kind=kind):
                result, samples = self.render(kind)
                self.assertAlmostEqual(result["duration"], 3, delta=0.002)
                self.assertAlmostEqual(len(samples) / 48000, 3, delta=0.02)
                self.assert_silence(samples, 0.2, 0.8)
                self.assert_tone(samples, 1.2, 1.8)
                self.assert_tone(samples, 2.2, 2.8)

    def test_source_format_changes_preserve_audio(self):
        result, samples = self.render("change")
        self.assertAlmostEqual(result["duration"], 5, delta=0.002)
        for start in (1.2, 2.2, 3.2, 4.2):
            self.assert_tone(samples, start, start + 0.6)

    def test_fractional_sample_rate_packets_do_not_drift(self):
        result, samples = self.render("packets")
        self.assertAlmostEqual(result["duration"], 1 + 102400 / 44100, delta=1 / 48000)
        self.assert_tone(samples, 1.2, 1.8)
        self.assert_tone(samples, 2.2, 2.8)
        self.assert_tone(samples, result["duration"] - 0.2, result["duration"] - 0.05)

    def test_late_audio_keeps_nonoverlapping_tail_and_fill_is_bounded(self):
        result, samples = self.render("overlap")
        self.assertAlmostEqual(result["duration"], 2.75, delta=0.002)
        self.assertEqual(result["padding_frames"], 96000)
        self.assert_silence(samples, 0.2, 0.8)
        self.assert_tone(samples, 1.05, 1.2)
        self.assert_silence(samples, 1.4, 2.1)
        self.assert_tone(samples, 2.35, 2.6)
