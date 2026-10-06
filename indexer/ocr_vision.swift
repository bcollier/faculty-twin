// OCR helper for indexer/slides.py (macOS only, uses Apple's Vision framework).
// Usage: swift indexer/ocr_vision.swift image1.webp image2.webp ...
// Prints one JSON object: {"<path>": "<recognized text, one line per observation>", ...}
// Used to catch text that lives inside slide images (screenshots, tables), which
// pdftotext cannot see. The caller never logs the text; it only tests and scrubs it.
import Foundation
import Vision
import ImageIO

var results: [String: String] = [:]
for path in CommandLine.arguments.dropFirst() {
    let url = URL(fileURLWithPath: path)
    guard let src = CGImageSourceCreateWithURL(url as CFURL, nil),
          let image = CGImageSourceCreateImageAtIndex(src, 0, nil) else {
        results[path] = ""
        continue
    }
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = false
    let handler = VNImageRequestHandler(cgImage: image, options: [:])
    do {
        try handler.perform([request])
        let lines = (request.results ?? []).compactMap { $0.topCandidates(1).first?.string }
        results[path] = lines.joined(separator: "\n")
    } catch {
        results[path] = ""
    }
}
let data = try JSONSerialization.data(withJSONObject: results, options: [])
FileHandle.standardOutput.write(data)
