// OCR helper for indexer/slides.py (macOS only, uses Apple's Vision framework).
// Usage: swift indexer/ocr_vision.swift image1.webp image2.webp ...
// Prints one JSON object: {"<path>": "<recognized text, one line per observation>", ...}
// Used to catch text that lives inside slide images (screenshots, tables), which
// pdftotext cannot see. The caller never logs the text; it only tests and scrubs it.
//
// Usage: swift indexer/ocr_vision.swift --boxes image1.webp ...   (added Oct 8, read-along)
// Prints {"<path>": [[word, x0, y0, x1, y1, line], ...], ...}: each recognized word with its
// box as fractions of the image, from the TOP left (Vision's origin is the bottom left, so y is
// flipped here), and the number of the text line it came from. indexer/slide_boxes.py uses it
// for image-only slides, scrubs it like the slide text, and never prints it.
import Foundation
import Vision
import ImageIO

let args = Array(CommandLine.arguments.dropFirst())
let boxesMode = args.first == "--boxes"
let paths = boxesMode ? Array(args.dropFirst()) : args

func recognize(_ path: String) -> [VNRecognizedTextObservation]? {
    let url = URL(fileURLWithPath: path)
    guard let src = CGImageSourceCreateWithURL(url as CFURL, nil),
          let image = CGImageSourceCreateImageAtIndex(src, 0, nil) else {
        return nil
    }
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = false
    let handler = VNImageRequestHandler(cgImage: image, options: [:])
    do {
        try handler.perform([request])
        return request.results ?? []
    } catch {
        return nil
    }
}

func round4(_ v: CGFloat) -> Double {
    return (Double(v) * 10000).rounded() / 10000
}

var results: [String: Any] = [:]
for path in paths {
    let observations = recognize(path) ?? []
    if !boxesMode {
        let lines = observations.compactMap { $0.topCandidates(1).first?.string }
        results[path] = lines.joined(separator: "\n")
        continue
    }
    var words: [[Any]] = []
    for (lineNo, obs) in observations.enumerated() {
        guard let cand = obs.topCandidates(1).first else { continue }
        let s = cand.string
        s.enumerateSubstrings(in: s.startIndex..<s.endIndex, options: .byWords) { sub, range, _, _ in
            guard let sub = sub, let rect = try? cand.boundingBox(for: range) else { return }
            let b = rect.boundingBox
            words.append([sub, round4(b.minX), round4(1 - b.maxY), round4(b.maxX), round4(1 - b.minY), lineNo])
        }
    }
    results[path] = words
}
let data = try JSONSerialization.data(withJSONObject: results, options: [])
FileHandle.standardOutput.write(data)
