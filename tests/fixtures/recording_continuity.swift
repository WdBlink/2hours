import Foundation
import AVFoundation
import CoreVideo
import CoreMedia

let mode = CommandLine.arguments[3]
let opts = (out: CommandLine.arguments[2], fps: 10, mic: mode != "noaudio")
__AUDIO_TRACK__

final class Harness {
    var writer: AVAssetWriter!
    var vIn: AVAssetWriterInput!
    var aIn: AVAssetWriterInput?
    var micTrack: RecordingAudioTrack?
    var systemTrack: RecordingAudioTrack?
    var lastVideoSample: CMSampleBuffer?
    var lastVideo = CMTime.invalid
    var frames = 0
    var sessionStart = CMTime(seconds: 1000, preferredTimescale: 48000)
    var offset = CMTime.zero

    init() throws {
        let W = 320, H = 240
__SETUP__
        vIn.expectsMediaDataInRealTime = true
        writer.add(vIn)
__AUDIO_SETUP__
        if mode == "dualempty" {
            let input = AVAssetWriterInput(mediaType: .audio, outputSettings: [
                AVFormatIDKey: kAudioFormatMPEG4AAC, AVSampleRateKey: 48000,
                AVNumberOfChannelsKey: 1, AVEncoderBitRateKey: 96000])
            input.expectsMediaDataInRealTime = true
            writer.add(input)
            systemTrack = RecordingAudioTrack(input: input)
        }
        precondition(writer.startWriting())
        writer.startSession(atSourceTime: sessionStart)
        micTrack?.start(at: sessionStart)
        systemTrack?.start(at: sessionStart)
    }

__VIDEO_METHODS__
__ADJUSTED__
}

func shifted(_ sb: CMSampleBuffer, by offset: CMTime) -> CMSampleBuffer {
    var count = 0
    CMSampleBufferGetSampleTimingInfoArray(sb, entryCount: 0, arrayToFill: nil, entriesNeededOut: &count)
    var timing = [CMSampleTimingInfo](repeating: CMSampleTimingInfo(), count: count)
    CMSampleBufferGetSampleTimingInfoArray(sb, entryCount: count, arrayToFill: &timing, entriesNeededOut: &count)
    for i in 0..<count {
        timing[i].presentationTimeStamp = CMTimeAdd(timing[i].presentationTimeStamp, offset)
        if timing[i].decodeTimeStamp.isValid { timing[i].decodeTimeStamp = CMTimeAdd(timing[i].decodeTimeStamp, offset) }
    }
    var out: CMSampleBuffer?
    precondition(CMSampleBufferCreateCopyWithNewTiming(allocator: nil, sampleBuffer: sb,
        sampleTimingEntryCount: count, sampleTimingArray: &timing, sampleBufferOut: &out) == noErr)
    return out!
}

let h = try Harness()
let asset = AVURLAsset(url: URL(fileURLWithPath: CommandLine.arguments[1]))
let reader = try AVAssetReader(asset: asset)
let video = AVAssetReaderTrackOutput(track: asset.tracks(withMediaType: .video)[0], outputSettings:
    [kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange])
let audio = AVAssetReaderTrackOutput(track: asset.tracks(withMediaType: .audio)[0], outputSettings:
    [AVFormatIDKey: kAudioFormatLinearPCM, AVSampleRateKey: 48000, AVNumberOfChannelsKey: 1])
reader.add(video); reader.add(audio)
precondition(reader.startReading())
var v = video.copyNextSampleBuffer(), a = audio.copyNextSampleBuffer()
var lastTick = -1.0
while v != nil || a != nil {
    let useVideo = v != nil && (a == nil || CMTimeCompare(CMSampleBufferGetPresentationTimeStamp(v!),
                                                        CMSampleBufferGetPresentationTimeStamp(a!)) <= 0)
    let sample = useVideo ? v! : a!
    let seconds = CMTimeGetSeconds(CMSampleBufferGetPresentationTimeStamp(sample))
    // A real pause shifts capture timestamps but must not add five seconds to the movie.
    if mode == "pause" && seconds >= 7 { h.offset = CMTime(seconds: 5, preferredTimescale: 48000) }
    let adjusted = h.adjusted(shifted(sample, by: CMTimeAdd(h.sessionStart, h.offset)))!
    if useVideo {
        if !["static", "both", "dualempty", "noaudio"].contains(mode) || h.lastVideoSample == nil {
            h.lastVideoSample = adjusted
            while !h.vIn.isReadyForMoreMediaData { Thread.sleep(forTimeInterval: 0.002) }
            h.appendVideo(adjusted)
        }
        v = video.copyNextSampleBuffer()
    } else {
        if !["empty", "both", "noaudio"].contains(mode) {
            while !h.aIn!.isReadyForMoreMediaData { Thread.sleep(forTimeInterval: 0.002) }
            _ = h.micTrack?.append(adjusted)
        }
        a = audio.copyNextSampleBuffer()
    }
    if seconds - lastTick >= 0.25 {
        h.keepMediaMoving(until: CMTimeAdd(h.sessionStart, CMTime(seconds: seconds - 1, preferredTimescale: 48000)))
        lastTick = seconds
    }
}
if CommandLine.arguments[4] == "kill" {
    // The parent sends SIGKILL: no finishWriting, endSession or process cleanup can run.
    Thread.sleep(forTimeInterval: 1)
    print("READY"); fflush(stdout)
    while true { Thread.sleep(forTimeInterval: 1) }
}
let end = CMTimeAdd(h.sessionStart, CMTime(seconds: 15, preferredTimescale: 48000))
while !h.vIn.isReadyForMoreMediaData { Thread.sleep(forTimeInterval: 0.002) }
precondition(h.finishMedia(at: end))
h.vIn.markAsFinished(); h.aIn?.markAsFinished(); h.systemTrack?.input.markAsFinished()
h.writer.endSession(atSourceTime: end)
let done = DispatchSemaphore(value: 0)
h.writer.finishWriting { done.signal() }
precondition(done.wait(timeout: .now() + 20) == .success && h.writer.status == .completed)
