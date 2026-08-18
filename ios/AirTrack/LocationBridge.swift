import Foundation
import CoreLocation
import SwiftUI
import WebKit

/// native 위치 → 웹앱 주입 브리지.
///
/// 포그라운드: fix마다 window.__nativeFix(...)로 실시간 주입.
/// 백그라운드: WKWebView JS가 정지되므로 native가 버퍼에 쌓고(UserDefaults에
/// 주기 보존 — 앱이 죽어도 유지), 복귀하면 window.__nativeBatch(...)로 일괄
/// 주입해 트랙 공백을 메운다. UIBackgroundModes=location +
/// allowsBackgroundLocationUpdates로 화면이 꺼져도 fix가 계속 온다.
final class LocationBridge: NSObject, ObservableObject, CLLocationManagerDelegate {
    private let mgr = CLLocationManager()
    private weak var webView: WKWebView?
    private var buffer: [[String: Any]] = []
    private var foreground = true
    private var started = false
    private static let bufferKey = "airtrack.nativeBuffer.v1"
    private static let bufferCap = 30_000          // ~8시간 @1fix/s

    override init() {
        super.init()
        mgr.delegate = self
        mgr.desiredAccuracy = kCLLocationAccuracyBest
        mgr.distanceFilter = 15
        mgr.activityType = .airborne
        mgr.pausesLocationUpdatesAutomatically = false
        restoreBuffer()
    }

    func attach(_ wv: WKWebView) { webView = wv }

    func start() {
        guard !started else { return }
        started = true
        switch mgr.authorizationStatus {
        case .notDetermined:
            mgr.requestWhenInUseAuthorization()
        default:
            beginUpdates()
        }
    }

    func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        beginUpdates()
    }

    private func beginUpdates() {
        let st = mgr.authorizationStatus
        guard st == .authorizedWhenInUse || st == .authorizedAlways else { return }
        // background mode capability + 이 플래그 조합이면 While-Using 허가로도
        // 백그라운드에서 계속 수신된다 (상태막대에 위치 표시가 뜸)
        mgr.allowsBackgroundLocationUpdates = true
        mgr.showsBackgroundLocationIndicator = true
        mgr.startUpdatingLocation()
        mgr.startUpdatingHeading()
    }

    func scenePhaseChanged(_ phase: ScenePhase) {
        foreground = (phase == .active)
        if foreground { flushBuffer() }
        else { persistBuffer() }
    }

    // MARK: - fixes

    func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        for loc in locations {
            let f: [String: Any] = [
                "lat": loc.coordinate.latitude,
                "lon": loc.coordinate.longitude,
                "alt": loc.verticalAccuracy > 0 ? loc.altitude : NSNull(),
                "spd": loc.speed >= 0 ? loc.speed : NSNull(),
                "crs": loc.course >= 0 ? loc.course : NSNull(),
                "acc": loc.horizontalAccuracy,
                "t": Int(loc.timestamp.timeIntervalSince1970 * 1000),
            ]
            if foreground, webView != nil {
                flushBuffer()
                inject("window.__nativeFix && window.__nativeFix(\(jsonString(f)))")
            } else {
                buffer.append(f)
                if buffer.count > Self.bufferCap { buffer.removeFirst(buffer.count - Self.bufferCap) }
                if buffer.count % 20 == 0 { persistBuffer() }
            }
        }
    }

    func locationManager(_ manager: CLLocationManager, didUpdateHeading heading: CLHeading) {
        guard foreground, heading.headingAccuracy >= 0 else { return }
        let h = heading.trueHeading >= 0 ? heading.trueHeading : heading.magneticHeading
        inject("window.__nativeHeading && window.__nativeHeading(\(h))")
    }

    func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) {
        // kCLErrorDenied 등 — 웹 쪽 pill이 '위치 대기'로 남는다. 별도 처리 불요.
    }

    // MARK: - buffer

    private func flushBuffer() {
        guard !buffer.isEmpty, webView != nil else { return }
        let batch = buffer
        buffer = []
        persistBuffer()
        inject("window.__nativeBatch && window.__nativeBatch(\(jsonString(batch)))")
    }

    private func persistBuffer() {
        if let d = try? JSONSerialization.data(withJSONObject: buffer) {
            UserDefaults.standard.set(d, forKey: Self.bufferKey)
        }
    }

    private func restoreBuffer() {
        if let d = UserDefaults.standard.data(forKey: Self.bufferKey),
           let arr = try? JSONSerialization.jsonObject(with: d) as? [[String: Any]] {
            buffer = arr
        }
    }

    // MARK: - injection

    private func jsonString(_ obj: Any) -> String {
        guard let d = try? JSONSerialization.data(withJSONObject: obj),
              let s = String(data: d, encoding: .utf8) else { return "null" }
        return s
    }

    private func inject(_ js: String) {
        DispatchQueue.main.async { [weak self] in
            self?.webView?.evaluateJavaScript(js, completionHandler: nil)
        }
    }
}
