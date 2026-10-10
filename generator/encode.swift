// PNG 序列 → H.264 MP4 (系统 AVFoundation, 无需 ffmpeg)。用法: encode <帧目录> <输出.mp4> <fps>
import Foundation
import AVFoundation
import CoreGraphics
import ImageIO

let args = CommandLine.arguments
guard args.count >= 4, let fps = Int32(args[3]) else { print("usage: encode <dir> <out.mp4> <fps>"); exit(1) }
let dir = args[1], out = args[2]
let files = try FileManager.default.contentsOfDirectory(atPath: dir).filter { $0.hasSuffix(".png") }.sorted()
guard !files.isEmpty else { print("no frames"); exit(1) }
func load(_ path: String) -> CGImage? {
    guard let src = CGImageSourceCreateWithURL(URL(fileURLWithPath: path) as CFURL, nil) else { return nil }
    return CGImageSourceCreateImageAtIndex(src, 0, nil)
}
guard let first = load("\(dir)/\(files[0])") else { print("bad first frame"); exit(1) }
let w = first.width, h = first.height
try? FileManager.default.removeItem(atPath: out)
let writer = try AVAssetWriter(outputURL: URL(fileURLWithPath: out), fileType: .mp4)
let settings: [String: Any] = [
    AVVideoCodecKey: AVVideoCodecType.h264, AVVideoWidthKey: w, AVVideoHeightKey: h,
    AVVideoCompressionPropertiesKey: [AVVideoAverageBitRateKey: 8_000_000, AVVideoProfileLevelKey: AVVideoProfileLevelH264HighAutoLevel, AVVideoMaxKeyFrameIntervalKey: 60]
]
let input = AVAssetWriterInput(mediaType: .video, outputSettings: settings)
input.expectsMediaDataInRealTime = false
let adaptor = AVAssetWriterInputPixelBufferAdaptor(assetWriterInput: input, sourcePixelBufferAttributes: [
    kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32ARGB, kCVPixelBufferWidthKey as String: w, kCVPixelBufferHeightKey as String: h])
writer.add(input)
guard writer.startWriting() else { print("startWriting failed: \(String(describing: writer.error))"); exit(1) }
writer.startSession(atSourceTime: .zero)
let cs = CGColorSpaceCreateDeviceRGB()
for (i, f) in files.enumerated() {
    guard let img = load("\(dir)/\(f)") else { print("skip \(f)"); continue }
    while !input.isReadyForMoreMediaData { usleep(2000) }
    var pb: CVPixelBuffer?
    guard let pool = adaptor.pixelBufferPool, CVPixelBufferPoolCreatePixelBuffer(nil, pool, &pb) == kCVReturnSuccess, let buf = pb else { print("pixel buffer failed at \(i)"); exit(1) }
    CVPixelBufferLockBaseAddress(buf, [])
    let ctx = CGContext(data: CVPixelBufferGetBaseAddress(buf), width: w, height: h, bitsPerComponent: 8, bytesPerRow: CVPixelBufferGetBytesPerRow(buf), space: cs,
                        bitmapInfo: CGImageAlphaInfo.noneSkipFirst.rawValue | CGBitmapInfo.byteOrder32Little.rawValue)!
    ctx.draw(img, in: CGRect(x: 0, y: 0, width: w, height: h))
    CVPixelBufferUnlockBaseAddress(buf, [])
    if !adaptor.append(buf, withPresentationTime: CMTime(value: CMTimeValue(i), timescale: fps)) { print("append failed at \(i): \(String(describing: writer.error))"); exit(1) }
    if i % 300 == 0 { print("frame \(i)/\(files.count)") }
}
input.markAsFinished()
let sem = DispatchSemaphore(value: 0)
writer.finishWriting { sem.signal() }
sem.wait()
if writer.status == .completed { print("ok \(files.count) frames → \(out)") } else { print("failed: \(String(describing: writer.error))"); exit(1) }
