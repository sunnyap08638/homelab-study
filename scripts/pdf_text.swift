import AppKit
import Foundation
import PDFKit
import Vision

guard CommandLine.arguments.count == 2,
      let pdf = PDFDocument(url: URL(fileURLWithPath: CommandLine.arguments[1])),
      pdf.pageCount > 0, !pdf.isLocked else {
    FileHandle.standardError.write(Data("Provide an unlocked PDF.\n".utf8))
    exit(1)
}

var pages: [String] = []
for index in 0..<pdf.pageCount {
    guard let page = pdf.page(at: index) else { continue }
    let embedded = page.string?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
    let size = page.bounds(for: .cropBox).size
    guard size.width > 0, size.height > 0 else { continue }

    // Goodnotes pages can contain ink without a PDF text layer.
    let scale = 2200 / max(size.width, size.height)
    let image = page.thumbnail(of: NSSize(width: size.width * scale, height: size.height * scale),
                               for: .cropBox)
    var rect = NSRect(origin: .zero, size: image.size)
    var recognized = ""
    if let bitmap = image.cgImage(forProposedRect: &rect, context: nil, hints: nil) {
        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .accurate
        request.usesLanguageCorrection = true
        do {
            try VNImageRequestHandler(cgImage: bitmap).perform([request])
            recognized = request.results?.compactMap { $0.topCandidates(1).first?.string }
                .joined(separator: "\n") ?? ""
        } catch {
            if embedded.isEmpty {
                FileHandle.standardError.write(Data("OCR failed on page \(index + 1): \(error)\n".utf8))
                exit(1)
            }
        }
    }
    let text = recognized.count > embedded.count ? recognized : embedded
    if !text.isEmpty { pages.append("Page \(index + 1)\n\n\(text)") }
}

guard !pages.isEmpty else {
    FileHandle.standardError.write(Data("No text recognized in this PDF.\n".utf8))
    exit(1)
}
print(pages.joined(separator: "\n\n"))
