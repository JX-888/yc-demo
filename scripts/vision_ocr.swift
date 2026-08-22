import AppKit
import Foundation
import ImageIO
import Vision

func fail(_ message: String, code: Int32 = 1) -> Never {
    FileHandle.standardError.write(Data((message + "\n").utf8))
    exit(code)
}

guard CommandLine.arguments.count >= 2 else {
    fail("Usage: swift vision_ocr.swift <image_path>")
}

let imageURL = URL(fileURLWithPath: CommandLine.arguments[1])
guard let imageSource = CGImageSourceCreateWithURL(imageURL as CFURL, nil) else {
    fail("Cannot read image: \(imageURL.path)")
}
guard let cgImage = CGImageSourceCreateImageAtIndex(imageSource, 0, nil) else {
    fail("Cannot decode image: \(imageURL.path)")
}

let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.usesLanguageCorrection = true

do {
    let supported = try request.supportedRecognitionLanguages()
    let preferred = ["zh-Hans", "zh-Hant", "en-US"]
    let selected = preferred.filter { supported.contains($0) }
    if !selected.isEmpty {
        request.recognitionLanguages = selected
    }
} catch {
    // Keep Vision defaults if the runtime cannot report supported languages.
}

let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])

do {
    try handler.perform([request])
} catch {
    fail("OCR failed: \(error.localizedDescription)")
}

let lines = (request.results ?? [])
    .compactMap { $0.topCandidates(1).first?.string.trimmingCharacters(in: .whitespacesAndNewlines) }
    .filter { !$0.isEmpty }

print(lines.joined(separator: "\n"))
