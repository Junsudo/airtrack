#!/bin/bash
# AirTrack을 아이폰에 (재)설치한다.
#
# 무료 개발자 프로필은 7일 만료라, 앱이 안 열리기 시작하면 이걸 다시 실행.
# CleanFeed reinstall.sh와 같은 흐름 — 웹 번들 동기화 → 빌드 → devicectl 설치.
#
# 주의: 무료 팀은 기기당 앱 3개까지. CleanFeed·FujiRemote·FujiRecipe가 슬롯을
# 차지하고 있으면 하나를 지운 뒤 설치해야 한다.
set -euo pipefail

DEVICE="${AIRTRACK_DEVICE:-A0820535-7854-52DA-8242-6781CA5A88C5}"   # Junsu iPhone
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

./sync-web.sh
xcodegen generate --quiet

xcodebuild -project AirTrack.xcodeproj -scheme AirTrack \
  -destination "platform=iOS,id=$DEVICE" -allowProvisioningUpdates -quiet build

APP="$(xcodebuild -project AirTrack.xcodeproj -scheme AirTrack \
  -destination "platform=iOS,id=$DEVICE" -showBuildSettings 2>/dev/null \
  | grep -m1 BUILT_PRODUCTS_DIR | awk '{print $3}')/AirTrack.app"

xcrun devicectl device install app --device "$DEVICE" "$APP"
echo "설치 완료 — 무료 프로필 기준 7일 뒤 만료되면 다시 실행"
