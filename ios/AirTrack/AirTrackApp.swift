import SwiftUI

/// 웹앱(번들된 docs/)을 WKWebView로 싣고, 위치는 native가 공급하는 셸.
/// scenePhase를 LocationBridge에 넘겨 백그라운드 버퍼링/복귀 플러시를 구동한다.
@main
struct AirTrackApp: App {
    @Environment(\.scenePhase) private var scenePhase
    @StateObject private var bridge = LocationBridge()

    var body: some Scene {
        WindowGroup {
            WebContainer(bridge: bridge)
                .ignoresSafeArea()
                .preferredColorScheme(.dark)
                .statusBarHidden(false)
        }
        .onChange(of: scenePhase) { _, phase in
            bridge.scenePhaseChanged(phase)
        }
    }
}
