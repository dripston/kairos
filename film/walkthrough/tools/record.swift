// record.swift: drive a real page in an offscreen WKWebView and record it as frames + an event log.
//
//   swiftc -O record.swift -o record && ./record <script.json> <outdir>
//
// script.json: {"url": "...", "width": 1600, "height": 900, "scale": 2, "steps": [ ... ]}
// Steps (each an object; the first key names the action):
//   {"wait": ms}                                   let the page run (no frames)
//   {"waitFor": "js expr", "timeout": ms}          poll until the expression is truthy
//   {"js": "code"}                                 run code (result logged)
//   {"click": "css selector", "text": "contains", "name": "label", "nth": 0}
//                                                  log the element's rect, then click it for real
//   {"type": "css selector", "value": "text", "cps": 14}  type into an input, a frame every 2 chars
//   {"key": "p"}                                   keydown on the document
//   {"rect": "css selector", "text": "...", "name": "label"}   log a rect for the camera
//   {"snap": "label", "settle": ms}                one frame after settling
//   {"burst": seconds, "fps": 8, "label": "x"}     frames while the page runs
// Frames: <outdir>/f00001.jpg …; log: <outdir>/log.json (frames with real time, events with rects in CSS px).
// Nothing here sends anything anywhere: the pages are local and their adapters are dry-run.
import AppKit
import Foundation
import WebKit
setvbuf(stdout, nil, _IOLBF, 0)

func fail(_ m: String) -> Never { FileHandle.standardError.write((m + "\n").data(using: .utf8)!); exit(1) }
func spin(_ s: Double) { RunLoop.main.run(until: Date().addingTimeInterval(s)) }
func waitUntil(_ timeout: Double = 30, _ cond: () -> Bool) -> Bool {
    let end = Date().addingTimeInterval(timeout)
    while !cond() { if Date() > end { return false }; RunLoop.main.run(until: Date().addingTimeInterval(0.005)) }
    return true
}

let args = CommandLine.arguments
guard args.count >= 3 else { fail("usage: record script.json outdir") }
let script = try! JSONSerialization.jsonObject(with: Data(contentsOf: URL(fileURLWithPath: args[1]))) as! [String: Any]
let outdir = URL(fileURLWithPath: args[2])
try? FileManager.default.removeItem(at: outdir)
try! FileManager.default.createDirectory(at: outdir, withIntermediateDirectories: true)

let W = (script["width"] as? Double) ?? 1600, H = (script["height"] as? Double) ?? 900
let scale = (script["scale"] as? Double) ?? 2

let app = NSApplication.shared
app.setActivationPolicy(.prohibited)
let window = NSWindow(contentRect: NSRect(x: -20000, y: -20000, width: W, height: H), styleMask: [.borderless], backing: .buffered, defer: false)
let cfg = WKWebViewConfiguration()
cfg.websiteDataStore = .nonPersistent()
// An offscreen window gets no display-link ticks, so CSS animations and transitions never progress and
// elements that animate in from opacity 0 stay invisible. Freeze them at their final state instead.
let freezeCSS = "*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important}" + ((script["css"] as? String) ?? "")
let freezeJS = "(function(){var s=document.createElement('style');s.textContent=\(String(data: try! JSONSerialization.data(withJSONObject: [freezeCSS]), encoding: .utf8)!)[0];(document.head||document.documentElement).appendChild(s);})()"
cfg.userContentController.addUserScript(WKUserScript(source: freezeJS, injectionTime: .atDocumentEnd, forMainFrameOnly: true))
let web = WKWebView(frame: NSRect(x: 0, y: 0, width: W, height: H), configuration: cfg)
window.contentView = web
window.orderBack(nil)

final class Nav: NSObject, WKNavigationDelegate {
    var done = false
    func webView(_ w: WKWebView, didFinish n: WKNavigation!) { done = true }
    func webViewWebContentProcessDidTerminate(_ w: WKWebView) { FileHandle.standardError.write("WEB CONTENT PROCESS TERMINATED\n".data(using: .utf8)!) }
    func webView(_ w: WKWebView, didFail n: WKNavigation!, withError e: Error) { fail("nav failed: \(e)") }
    func webView(_ w: WKWebView, didFailProvisionalNavigation n: WKNavigation!, withError e: Error) { fail("nav failed: \(e)") }
}
let nav = Nav(); web.navigationDelegate = nav
// Pages may ask confirm()/alert(); answer them (OK) and log the text, so a dialog never blocks the run.
final class UI: NSObject, WKUIDelegate {
    func webView(_ w: WKWebView, runJavaScriptAlertPanelWithMessage m: String, initiatedByFrame f: WKFrameInfo, completionHandler: @escaping () -> Void) { print("alert:", m); completionHandler() }
    func webView(_ w: WKWebView, runJavaScriptConfirmPanelWithMessage m: String, initiatedByFrame f: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) { print("confirm (OK):", m); completionHandler(true) }
    func webView(_ w: WKWebView, runJavaScriptTextInputPanelWithPrompt p: String, defaultText d: String?, initiatedByFrame f: WKFrameInfo, completionHandler: @escaping (String?) -> Void) { print("prompt:", p); completionHandler(d) }
}
let ui = UI(); web.uiDelegate = ui

func js(_ code: String) -> Any? {
    var result: Any?; var finished = false
    web.evaluateJavaScript(code) { r, e in
        if let e = e { FileHandle.standardError.write("js error: \(e.localizedDescription)\n  in: \(code.prefix(160))\n".data(using: .utf8)!) }
        result = r; finished = true
    }
    _ = waitUntil(20) { finished }
    return result
}

var frames: [[String: Any]] = []
var events: [[String: Any]] = []
let t0 = Date()
var label = ""

func snap() { autoreleasepool { snapInner() } }
func snapInner() {
    let c = WKSnapshotConfiguration()
    c.snapshotWidth = NSNumber(value: W * scale / (window.backingScaleFactor))
    c.afterScreenUpdates = true
    var img: NSImage?
    for attempt in 0..<5 {
        var finished = false
        web.takeSnapshot(with: c) { i, e in img = i; finished = true
            if let e = e { FileHandle.standardError.write("frame \(frames.count + 1) snapshot retry \(attempt): \(e.localizedDescription)\n".data(using: .utf8)!) } }
        _ = waitUntil(10) { finished }
        if img != nil { break }
        spin(0.3)
    }
    guard let im = img, let cg = im.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
        FileHandle.standardError.write("frame skipped\n".data(using: .utf8)!); return }
    let idx = frames.count + 1
    let url = outdir.appendingPathComponent(String(format: "f%05d.jpg", idx))
    let dest = CGImageDestinationCreateWithURL(url as CFURL, "public.jpeg" as CFString, 1, nil)!
    CGImageDestinationAddImage(dest, cg, [kCGImageDestinationLossyCompressionQuality: 0.88] as CFDictionary)
    CGImageDestinationFinalize(dest)
    frames.append(["i": idx, "t": Date().timeIntervalSince(t0), "label": label, "px": [cg.width, cg.height]])
}

func finder(_ step: [String: Any], _ key: String) -> String {
    let sel = (step[key] as? String) ?? "*"
    let text = (step["text"] as? String) ?? ""
    let nth = (step["nth"] as? Int) ?? 0
    let selJ = String(data: try! JSONSerialization.data(withJSONObject: [sel, text]), encoding: .utf8)!
    return """
    (function(){var a=\(selJ);var els=[].slice.call(document.querySelectorAll(a[0]));
    if(a[1]){els=els.filter(function(e){return (e.innerText||e.value||'').indexOf(a[1])>=0;});
      els.sort(function(x,y){return (x.innerText||'').length-(y.innerText||'').length;});}
    els=els.filter(function(e){var r=e.getBoundingClientRect();return r.width>0&&r.height>0;});
    return els[\(nth)]||null;})()
    """
}

func logRect(_ step: [String: Any], _ key: String, _ type: String) -> Bool {
    let f = finder(step, key)
    let r = js("(function(){var e=\(f);if(!e)return null;e.scrollIntoView({block:'nearest',inline:'nearest'});var r=e.getBoundingClientRect();return [r.x,r.y,r.width,r.height,window.scrollY];})()") as? [Double]
    guard let rr = r else {
        FileHandle.standardError.write("not found: \(step)\n".data(using: .utf8)!); return false
    }
    events.append(["type": type, "name": (step["name"] as? String) ?? "", "t": Date().timeIntervalSince(t0),
                   "frame": frames.count, "rect": Array(rr[0..<4]), "scrollY": rr[4]])
    return true
}

// load
web.load(URLRequest(url: URL(string: script["url"] as! String)!))
guard waitUntil(30, { nav.done }) else { fail("page did not load") }
spin(1.0)
print("loaded \(script["url"]!)  backing scale \(window.backingScaleFactor)")

for step in (script["steps"] as! [[String: Any]]) {
    if let ms = step["wait"] as? Double { spin(ms / 1000) }
    else if let cond = step["waitFor"] as? String {
        let ok = waitUntil(((step["timeout"] as? Double) ?? 15000) / 1000) { (js("!!(\(cond))") as? Bool) == true }
        if !ok { FileHandle.standardError.write("waitFor timed out: \(cond)\n".data(using: .utf8)!) }
    }
    else if let code = step["js"] as? String { let r = js(code); if let r = r { print("js →", String(describing: r).prefix(200)) } }
    else if step["click"] != nil {
        if logRect(step, "click", "click") {
            spin(0.05)
            _ = js("(function(){var e=\(finder(step, "click"));if(!e)return;['pointerdown','mousedown','pointerup','mouseup'].forEach(function(t){e.dispatchEvent(new MouseEvent(t,{bubbles:true}));});e.click();})()")
        }
    }
    else if step["type"] != nil {
        _ = logRect(step, "type", "type")
        let value = (step["value"] as? String) ?? ""
        let cps = (step["cps"] as? Double) ?? 14
        var typed = ""
        for (i, ch) in value.enumerated() {
            typed.append(ch)
            let v = String(data: try! JSONSerialization.data(withJSONObject: [typed]), encoding: .utf8)!
            _ = js("""
            (function(){var e=\(finder(step, "type"));if(!e)return;e.focus();var v=\(v)[0];
            var p=Object.getPrototypeOf(e);var d=Object.getOwnPropertyDescriptor(p,'value');
            if(d&&d.set)d.set.call(e,v);else e.value=v;e.dispatchEvent(new Event('input',{bubbles:true}));})()
            """)
            spin(1 / cps)
            if i % 2 == 1 || i == value.count - 1 { snap() }
        }
    }
    else if let k = step["key"] as? String {
        _ = js("document.dispatchEvent(new KeyboardEvent('keydown',{key:'\(k)',bubbles:true}))")
    }
    else if let until = step["clickUntil"] as? String { // click a selector until a condition holds (each click snapped)
        let maxN = (step["max"] as? Int) ?? 8, w = ((step["wait"] as? Double) ?? 900) / 1000
        var n = 0
        while (js("!!(\(until))") as? Bool) != true && n < maxN {
            _ = js("document.querySelector('\(step["sel"] as! String)').click()"); spin(w); label = (step["label"] as? String) ?? "until"; snap(); n += 1
        }
        print("clickUntil \(until): \(n) clicks, ok=\((js("!!(\(until))") as? Bool) == true)")
    }
    else if step["rect"] != nil { _ = logRect(step, "rect", "rect") }
    else if let l = step["snap"] as? String { label = l; spin(((step["settle"] as? Double) ?? 350) / 1000); snap() }
    else if let secs = step["burst"] as? Double {
        label = (step["label"] as? String) ?? label
        let fps = (step["fps"] as? Double) ?? 8
        let end = Date().addingTimeInterval(secs)
        var next = Date()
        while Date() < end {
            snap(); next = next.addingTimeInterval(1 / fps)
            let wait = next.timeIntervalSinceNow; if wait > 0 { spin(wait) }
        }
    }
    if let l = step["label"] as? String, step["burst"] == nil { label = l }
}

let log: [String: Any] = ["url": script["url"]!, "viewport": ["w": W, "h": H, "scale": scale], "frames": frames, "events": events]
try! JSONSerialization.data(withJSONObject: log, options: [.prettyPrinted]).write(to: outdir.appendingPathComponent("log.json"))
print("frames \(frames.count)  events \(events.count)  → \(outdir.path)")
