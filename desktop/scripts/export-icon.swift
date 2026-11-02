import AppKit

// Export the approved design to native application assets. Remove only the
// connected white presentation background; the enclosed white ribbon stays.
let root = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent()
let sourceURL = root.appendingPathComponent("design/app-icon/loops-app-icon.png")
guard let representation = NSBitmapImageRep(data: try Data(contentsOf: sourceURL)),
      let source = representation.cgImage else { fatalError("无法读取图标设计稿") }
let width = source.width, height = source.height
var pixels = [UInt8](repeating: 0, count: width * height * 4)
let colorSpace = CGColorSpace(name: CGColorSpace.sRGB)!
let bitmapInfo = CGImageAlphaInfo.premultipliedLast.rawValue | CGBitmapInfo.byteOrder32Big.rawValue
pixels.withUnsafeMutableBytes { bytes in
    let context = CGContext(data: bytes.baseAddress, width: width, height: height,
                            bitsPerComponent: 8, bytesPerRow: width * 4,
                            space: colorSpace, bitmapInfo: bitmapInfo)!
    context.draw(source, in: CGRect(x: 0, y: 0, width: width, height: height))
}
var exterior = [Bool](repeating: false, count: width * height)
var queue = [Int]()
func visit(_ index: Int) {
    guard !exterior[index] else { return }
    let offset = index * 4
    guard pixels[offset] >= 235, pixels[offset + 1] >= 235, pixels[offset + 2] >= 235 else { return }
    exterior[index] = true
    queue.append(index)
}
for x in 0..<width { visit(x); visit((height - 1) * width + x) }
for y in 0..<height { visit(y * width); visit(y * width + width - 1) }
var cursor = 0
while cursor < queue.count {
    let index = queue[cursor], x = index % width, y = index / width
    cursor += 1
    if x > 0 { visit(index - 1) }
    if x + 1 < width { visit(index + 1) }
    if y > 0 { visit(index - width) }
    if y + 1 < height { visit(index + width) }
}
for index in queue { for channel in 0..<4 { pixels[index * 4 + channel] = 0 } }
let provider = CGDataProvider(data: Data(pixels) as CFData)!
let artwork = CGImage(width: width, height: height, bitsPerComponent: 8, bitsPerPixel: 32,
                      bytesPerRow: width * 4, space: colorSpace, bitmapInfo: CGBitmapInfo(rawValue: bitmapInfo),
                      provider: provider, decode: nil, shouldInterpolate: true, intent: .defaultIntent)!
func writePNG(size: Int, to destination: URL) throws {
    let context = CGContext(data: nil, width: size, height: size, bitsPerComponent: 8,
                            bytesPerRow: size * 4, space: colorSpace, bitmapInfo: bitmapInfo)!
    context.interpolationQuality = .high
    context.draw(artwork, in: CGRect(x: 0, y: 0, width: size, height: size))
    let bitmap = NSBitmapImageRep(cgImage: context.makeImage()!)
    try bitmap.representation(using: .png, properties: [:])!.write(to: destination)
}
let fm = FileManager.default
let assets = root.appendingPathComponent("assets")
let iconset = root.appendingPathComponent(".local/Loops.iconset")
try fm.createDirectory(at: assets, withIntermediateDirectories: true)
try fm.createDirectory(at: iconset, withIntermediateDirectories: true)
try writePNG(size: 1024, to: root.appendingPathComponent("public/assets/loops-icon.png"))
for size in [16, 32, 128, 256, 512] {
    try writePNG(size: size, to: iconset.appendingPathComponent("icon_\(size)x\(size).png"))
    try writePNG(size: size * 2, to: iconset.appendingPathComponent("icon_\(size)x\(size)@2x.png"))
}
let converter = Process()
converter.executableURL = URL(fileURLWithPath: "/usr/bin/iconutil")
converter.arguments = ["--convert", "icns", "--output", assets.appendingPathComponent("loops.icns").path, iconset.path]
try converter.run()
converter.waitUntilExit()
guard converter.terminationStatus == 0 else { fatalError("Mac 图标导出失败") }
print("已导出 public/assets/loops-icon.png 与 assets/loops.icns")
