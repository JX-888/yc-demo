import Foundation
import ImageIO
import Vision

struct Box {
    var x: Int
    var y: Int
    var w: Int
    var h: Int

    var maxX: Int { x + w }
    var maxY: Int { y + h }

    func intersectsOrNear(_ other: Box, padding: Int) -> Bool {
        return x - padding <= other.maxX
            && maxX + padding >= other.x
            && y - padding <= other.maxY
            && maxY + padding >= other.y
    }

    func union(_ other: Box) -> Box {
        let nx = min(x, other.x)
        let ny = min(y, other.y)
        let mx = max(maxX, other.maxX)
        let my = max(maxY, other.maxY)
        return Box(x: nx, y: ny, w: mx - nx, h: my - ny)
    }
}

struct FocusResult: Encodable {
    let marked_region_count: Int
    let focus_text: String
}

func fail(_ message: String, code: Int32 = 1) -> Never {
    FileHandle.standardError.write(Data((message + "\n").utf8))
    exit(code)
}

func clamp(_ value: Int, _ low: Int, _ high: Int) -> Int {
    return max(low, min(high, value))
}

func isRedMarker(r: UInt8, g: UInt8, b: UInt8, a: UInt8) -> Bool {
    if a < 80 { return false }
    let ri = Int(r)
    let gi = Int(g)
    let bi = Int(b)
    if ri < 145 { return false }
    if ri - max(gi, bi) < 45 { return false }
    if gi > 170 && bi > 170 { return false }
    return true
}

func recognizeText(_ cgImage: CGImage) -> String {
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
        // Keep Vision defaults if supported languages cannot be queried.
    }

    let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
    do {
        try handler.perform([request])
    } catch {
        return ""
    }

    return (request.results ?? [])
        .compactMap { $0.topCandidates(1).first?.string.trimmingCharacters(in: .whitespacesAndNewlines) }
        .filter { !$0.isEmpty }
        .joined(separator: "\n")
}

guard CommandLine.arguments.count >= 2 else {
    fail("Usage: swift marked_focus_ocr.swift <image_path>")
}

let imageURL = URL(fileURLWithPath: CommandLine.arguments[1])
guard let imageSource = CGImageSourceCreateWithURL(imageURL as CFURL, nil),
      let cgImage = CGImageSourceCreateImageAtIndex(imageSource, 0, nil) else {
    fail("Cannot read image: \(imageURL.path)")
}

let width = cgImage.width
let height = cgImage.height
let bytesPerPixel = 4
let bytesPerRow = width * bytesPerPixel
var pixels = [UInt8](repeating: 0, count: height * bytesPerRow)

guard let colorSpace = CGColorSpace(name: CGColorSpace.sRGB),
      let context = CGContext(
        data: &pixels,
        width: width,
        height: height,
        bitsPerComponent: 8,
        bytesPerRow: bytesPerRow,
        space: colorSpace,
        bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
      ) else {
    fail("Cannot create bitmap context")
}

context.draw(cgImage, in: CGRect(x: 0, y: 0, width: width, height: height))

let cellSize = 8
let gridW = (width + cellSize - 1) / cellSize
let gridH = (height + cellSize - 1) / cellSize
var grid = [Bool](repeating: false, count: gridW * gridH)

for y in 0..<height {
    for x in 0..<width {
        let offset = y * bytesPerRow + x * bytesPerPixel
        let r = pixels[offset]
        let g = pixels[offset + 1]
        let b = pixels[offset + 2]
        let a = pixels[offset + 3]
        if isRedMarker(r: r, g: g, b: b, a: a) {
            let gx = x / cellSize
            let gy = y / cellSize
            grid[gy * gridW + gx] = true
        }
    }
}

var visited = [Bool](repeating: false, count: gridW * gridH)
var rawBoxes: [Box] = []
let neighbors = [(-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1)]

for gy in 0..<gridH {
    for gx in 0..<gridW {
        let start = gy * gridW + gx
        if visited[start] || !grid[start] { continue }

        var queue = [(gx, gy)]
        visited[start] = true
        var index = 0
        var minX = gx
        var maxX = gx
        var minY = gy
        var maxY = gy
        var count = 0

        while index < queue.count {
            let (cx, cy) = queue[index]
            index += 1
            count += 1
            minX = min(minX, cx)
            maxX = max(maxX, cx)
            minY = min(minY, cy)
            maxY = max(maxY, cy)

            for (dx, dy) in neighbors {
                let nx = cx + dx
                let ny = cy + dy
                if nx < 0 || nx >= gridW || ny < 0 || ny >= gridH { continue }
                let ni = ny * gridW + nx
                if visited[ni] || !grid[ni] { continue }
                visited[ni] = true
                queue.append((nx, ny))
            }
        }

        let box = Box(
            x: minX * cellSize,
            y: minY * cellSize,
            w: min(width - minX * cellSize, (maxX - minX + 1) * cellSize),
            h: min(height - minY * cellSize, (maxY - minY + 1) * cellSize)
        )

        if count >= 2 && (box.w >= 16 || box.h >= 16) {
            rawBoxes.append(box)
        }
    }
}

var expanded: [Box] = rawBoxes.map { box in
    let horizontalMark = box.w >= box.h * 4
    let leftPad = max(24, width / 40)
    let rightPad = leftPad
    let topPad = horizontalMark ? max(80, height / 8) : max(40, height / 20)
    let bottomPad = horizontalMark ? max(32, height / 25) : max(40, height / 20)
    let nx = clamp(box.x - leftPad, 0, width - 1)
    let ny = clamp(box.y - topPad, 0, height - 1)
    let mx = clamp(box.maxX + rightPad, 1, width)
    let my = clamp(box.maxY + bottomPad, 1, height)
    return Box(x: nx, y: ny, w: max(1, mx - nx), h: max(1, my - ny))
}

var merged: [Box] = []
for box in expanded {
    var current = box
    var changed = true
    while changed {
        changed = false
        var kept: [Box] = []
        for existing in merged {
            if current.intersectsOrNear(existing, padding: 20) {
                current = current.union(existing)
                changed = true
            } else {
                kept.append(existing)
            }
        }
        merged = kept
    }
    merged.append(current)
}

let boxes = merged
    .filter { $0.w * $0.h >= 800 }
    .sorted { ($0.y, $0.x) < ($1.y, $1.x) }
    .prefix(6)

var texts: [String] = []
for box in boxes {
    guard let crop = cgImage.cropping(to: CGRect(x: box.x, y: box.y, width: box.w, height: box.h)) else {
        continue
    }
    let text = recognizeText(crop).trimmingCharacters(in: .whitespacesAndNewlines)
    if !text.isEmpty {
        texts.append(text)
    }
}

let result = FocusResult(marked_region_count: rawBoxes.count, focus_text: texts.joined(separator: "\n\n---\n\n"))
let encoder = JSONEncoder()
encoder.outputFormatting = [.withoutEscapingSlashes]
let data = try encoder.encode(result)
print(String(data: data, encoding: .utf8) ?? "{}")
