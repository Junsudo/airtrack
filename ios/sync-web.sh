#!/bin/bash
# 웹앱(docs/)을 iOS 번들 리소스(AirTrack/web/)로 복사한다.
# 웹 쪽을 고친 뒤 앱을 리빌드하기 전에 실행. reinstall.sh가 자동으로 부른다.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
rsync -a --delete "$ROOT/../docs/" "$ROOT/AirTrack/web/"
echo "synced: $(du -sh "$ROOT/AirTrack/web" | cut -f1)"
