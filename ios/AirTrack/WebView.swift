import SwiftUI
import WebKit

/// 번들 web/ 폴더를 airtrack://app/... 커스텀 스킴으로 서빙한다.
/// file://로 직접 열면 WKWebView가 fetch()를 막아 GeoJSON 로드가 안 되기
/// 때문에 스킴 핸들러가 필요하다. service worker는 WKWebView에 없지만
/// 모든 자산이 번들에 있으므로 오프라인은 구조적으로 보장된다.
final class BundleSchemeHandler: NSObject, WKURLSchemeHandler {
    private let mime: [String: String] = [
        "html": "text/html", "js": "application/javascript", "css": "text/css",
        "json": "application/json", "geojson": "application/json",
        "webmanifest": "application/manifest+json", "png": "image/png",
        "pbf": "application/octet-stream", "svg": "image/svg+xml",
    ]

    func webView(_ webView: WKWebView, start task: WKURLSchemeTask) {
        guard let url = task.request.url else { return }
        var path = url.path
        if path.isEmpty || path == "/" { path = "/index.html" }
        let clean = String(path.dropFirst())          // "data/airways.geojson"
        guard let base = Bundle.main.resourceURL?.appendingPathComponent("web"),
              case let fileURL = base.appendingPathComponent(clean),
              let data = try? Data(contentsOf: fileURL) else {
            task.didFailWithError(NSError(domain: "airtrack", code: 404))
            return
        }
        let ext = (clean as NSString).pathExtension.lowercased()
        let type = mime[ext] ?? "application/octet-stream"
        let resp = HTTPURLResponse(url: url, statusCode: 200, httpVersion: "HTTP/1.1",
                                   headerFields: ["Content-Type": type,
                                                  "Cache-Control": "no-cache"])!
        task.didReceive(resp)
        task.didReceive(data)
        task.didFinish()
    }

    func webView(_ webView: WKWebView, stop task: WKURLSchemeTask) {}
}

struct WebContainer: UIViewRepresentable {
    let bridge: LocationBridge

    func makeCoordinator() -> Coordinator { Coordinator(bridge: bridge) }

    func makeUIView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        config.setURLSchemeHandler(BundleSchemeHandler(), forURLScheme: "airtrack")
        // 웹 쪽이 native 모드임을 페이지 로드 전에 알 수 있게 한다
        let flag = WKUserScript(source: "window.__NATIVE = true;",
                                injectionTime: .atDocumentStart, forMainFrameOnly: true)
        config.userContentController.addUserScript(flag)
        config.allowsInlineMediaPlayback = true

        let wv = WKWebView(frame: .zero, configuration: config)
        wv.navigationDelegate = context.coordinator
        wv.scrollView.isScrollEnabled = false      // 지도 제스처는 MapLibre가 처리
        wv.scrollView.contentInsetAdjustmentBehavior = .never
        wv.isOpaque = false
        wv.backgroundColor = UIColor(red: 0.039, green: 0.086, blue: 0.125, alpha: 1)
        if #available(iOS 16.4, *) { wv.isInspectable = true }

        bridge.attach(wv)
        wv.load(URLRequest(url: URL(string: "airtrack://app/index.html")!))
        return wv
    }

    func updateUIView(_ uiView: WKWebView, context: Context) {}

    final class Coordinator: NSObject, WKNavigationDelegate {
        let bridge: LocationBridge
        init(bridge: LocationBridge) { self.bridge = bridge }

        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
            // 페이지가 준비된 뒤에 권한 요청 → 프롬프트가 지도 위에 뜬다
            bridge.start()
        }
    }
}
