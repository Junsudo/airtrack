import CoreLocation
import CoreMotion
import Foundation
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
    private let altimeter = CMAltimeter()
    private var altimeterStarted = false
    private weak var webView: WKWebView?
    private var buffer: [[String: Any]] = []
    private var foreground = true
    private var started = false
    private var pageReady = false
    private var flushing = false
    private var sincePersist = 0
    private var lastFix: [String: Any]?
    private static let bufferKey = "airtrack.nativeBuffer.v1"
    private static let bufferCap = 30_000          // ~8시간 @1fix/s

    override init() {
        super.init()
        mgr.delegate = self
        mgr.desiredAccuracy = kCLLocationAccuracyBest
        // distanceFilter를 걸면 정지 상태에서 fix가 한 번만 오고, 그 한 번을
        // (웹 부팅 전이라) 놓치면 '위치 대기'에 갇힌다. 연속 수신(~1 Hz)이
        // 버퍼 설계(bufferCap ~8시간 @1fix/s)와도 맞다.
        mgr.distanceFilter = kCLDistanceFilterNone
        mgr.activityType = .airborne
        mgr.pausesLocationUpdatesAutomatically = false
        restoreBuffer()
    }

    func attach(_ wv: WKWebView) { webView = wv }

    /// 페이지 로드 완료(didFinish)마다 호출. 위치 갱신은 delegate 설정 시점의
    /// authorization 콜백으로 이미 돌고 있을 수 있으므로, 페이지 준비 전에
    /// 도착해 버려진 fix를 마지막 값으로 다시 주입하고 밀린 배치를 전달한다.
    /// 재로드(캐시 리셋·프로세스 복구)는 새 페이지 상태로 시작하므로
    /// 권한 거부 상태도 매번 다시 통보해야 한다.
    func start() {
        pageReady = true
        if started {
            let st = mgr.authorizationStatus
            if st == .denied || st == .restricted {
                inject("window.__nativeDenied && window.__nativeDenied()")
            }
            injectLastFix()
            flushBuffer()
            return
        }
        started = true
        switch mgr.authorizationStatus {
        case .notDetermined:
            mgr.requestWhenInUseAuthorization()
        default:
            beginUpdates()   // denied/restricted면 여기서 __nativeDenied 통보
        }
        injectLastFix()
        flushBuffer()
        startBaro()
    }

    /// WKWebView 콘텐츠 프로세스가 죽으면 didFinish까지 주입을 막는다
    func pageGone() { pageReady = false }

    /// 기압계 → __nativeBaro(hPa). PA 환산은 웹이 한다.
    /// 여압 객실에서는 객실 기압을 읽는다 — 항공기 FL이 아님 (라벨에 반영됨).
    private func startBaro() {
        guard !altimeterStarted, CMAltimeter.isRelativeAltitudeAvailable() else { return }
        altimeterStarted = true
        altimeter.startRelativeAltitudeUpdates(to: .main) { [weak self] data, _ in
            guard let self, self.foreground, self.pageReady, let d = data else { return }
            let hpa = d.pressure.doubleValue * 10.0   // kPa → hPa
            self.inject("window.__nativeBaro && window.__nativeBaro(\(String(format: "%.2f", hpa)))")
        }
    }

    private func injectLastFix() {
        // 오래된 fix를 새것처럼 재주입하지 않는다 — 1 Hz 스트림이 곧 갱신한다
        guard let f = lastFix, let t = f["t"] as? Int,
              Date().timeIntervalSince1970 * 1000 - Double(t) < 30_000 else { return }
        inject("window.__nativeFix && window.__nativeFix(\(jsonString(f)))")
    }

    func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        beginUpdates()
    }

    private func beginUpdates() {
        let st = mgr.authorizationStatus
        guard st == .authorizedWhenInUse || st == .authorizedAlways else {
            if st == .denied || st == .restricted {
                inject("window.__nativeDenied && window.__nativeDenied()")
            }
            return
        }
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
            guard loc.horizontalAccuracy >= 0 else { continue }   // 무효 fix
            let f: [String: Any] = [
                "lat": loc.coordinate.latitude,
                "lon": loc.coordinate.longitude,
                "alt": loc.verticalAccuracy > 0 ? loc.altitude : NSNull(),
                "spd": loc.speed >= 0 ? loc.speed : NSNull(),
                "crs": loc.course >= 0 ? loc.course : NSNull(),
                "acc": loc.horizontalAccuracy,
                "t": Int(loc.timestamp.timeIntervalSince1970 * 1000),
            ]
            lastFix = f
            if foreground, pageReady, webView != nil {
                flushBuffer()
                inject("window.__nativeFix && window.__nativeFix(\(jsonString(f)))")
            } else {
                buffer.append(f)
                if buffer.count > Self.bufferCap { buffer.removeFirst(buffer.count - Self.bufferCap) }
                // buffer.count 기준이면 cap 도달 후 매 fix마다 전체 직렬화가 돈다
                sincePersist += 1
                if sincePersist >= 20 { persistBuffer(); sincePersist = 0 }
            }
        }
    }

    func locationManager(_ manager: CLLocationManager, didUpdateHeading heading: CLHeading) {
        guard foreground, heading.headingAccuracy >= 0 else { return }
        let h = heading.trueHeading >= 0 ? heading.trueHeading : heading.magneticHeading
        inject("window.__nativeHeading && window.__nativeHeading(\(h))")
    }

    func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) {
        if (error as? CLError)?.code == .denied {
            inject("window.__nativeDenied && window.__nativeDenied()")
        }
    }

    // MARK: - buffer

    private func flushBuffer() {
        // 백그라운드 트랙이 담긴 버퍼는 웹이 수신 확인(ack)한 뒤에만 비운다.
        // 페이지 준비 전에 지우면 UserDefaults 사본까지 사라져 복구 불가.
        guard !buffer.isEmpty, pageReady, !flushing, let wv = webView else { return }
        flushing = true
        let batch = buffer
        let js = "(window.__nativeBatch && window.__nativeBatch(\(jsonString(batch)))) || 0"
        DispatchQueue.main.async { [weak self] in
            wv.evaluateJavaScript(js) { res, _ in
                guard let self else { return }
                self.flushing = false
                guard let n = res as? Int, n > 0 else { return }   // 미수신 — 다음 기회에 재시도
                self.buffer.removeFirst(min(batch.count, self.buffer.count))
                self.persistBuffer()
            }
        }
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
