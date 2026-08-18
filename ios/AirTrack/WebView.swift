import SwiftUI
import UIKit
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
        wv.uiDelegate = context.coordinator      // confirm()/alert() — 없으면 confirm이 항상 false
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

    final class Coordinator: NSObject, WKNavigationDelegate, WKUIDelegate {
        let bridge: LocationBridge
        init(bridge: LocationBridge) { self.bridge = bridge }

        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
            // 페이지가 준비된 뒤에 권한 요청 → 프롬프트가 지도 위에 뜬다
            bridge.start()
        }

        /// 콘텐츠 프로세스가 죽으면 WKWebView는 스스로 리로드하지 않는다 —
        /// 기내에서 흰 화면으로 방치되는 걸 막기 위해 즉시 리로드한다.
        /// didFinish가 다시 start()를 불러 fix·권한 상태를 복원한다.
        func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
            bridge.pageGone()
            webView.reload()
        }

        // WKUIDelegate가 없으면 window.confirm()이 항상 false로 완료돼
        // 캐시 리셋·트랙 삭제 버튼이 소리 없이 죽는다.
        func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String,
                     initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
            guard let vc = Self.topViewController() else { completionHandler(false); return }
            let a = UIAlertController(title: nil, message: message, preferredStyle: .alert)
            a.addAction(UIAlertAction(title: "취소", style: .cancel) { _ in completionHandler(false) })
            a.addAction(UIAlertAction(title: "확인", style: .default) { _ in completionHandler(true) })
            vc.present(a, animated: true)
        }

        func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String,
                     initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
            guard let vc = Self.topViewController() else { completionHandler(); return }
            let a = UIAlertController(title: nil, message: message, preferredStyle: .alert)
            a.addAction(UIAlertAction(title: "확인", style: .default) { _ in completionHandler() })
            vc.present(a, animated: true)
        }

        private static func topViewController() -> UIViewController? {
            let scene = UIApplication.shared.connectedScenes
                .compactMap { $0 as? UIWindowScene }
                .first { $0.activationState == .foregroundActive }
                ?? UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }.first
            var vc = scene?.keyWindow?.rootViewController
                ?? scene?.windows.first?.rootViewController
            while let p = vc?.presentedViewController { vc = p }
            return vc
        }
    }
}
