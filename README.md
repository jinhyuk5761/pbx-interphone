# Mini PBX + Android 인터폰

순수 Python(socket) 으로 만든 미니 SIP 서버(PBX)와, 그 PBX에 등록해 통화하는
안드로이드 인터폰 앱(Kotlin + Linphone SDK) 프로젝트입니다.
miniSIPServer 를 대체하는 것이 목표입니다.

## 구성

| 경로 | 내용 |
|------|------|
| `sipmsg.py` | SIP 메시지 파서/생성 |
| `step3_proxy.py` | SIP 서버(PBX) 엔진 — REGISTER/INVITE/ACK/BYE 프록시 + 조회 API + FCM 발송 |
| `gui_pbx.py` | PySide6 관리 콘솔 GUI |
| `interphone/` | 안드로이드 인터폰 앱 (Kotlin) |
| `INSTALL.md` | 다른 PC 설치 가이드 |
| `md` | 개발 진행 기록 |

## PBX 서버 실행

```bash
pip install -r requirements.txt        # PySide6, firebase-admin
python gui_pbx.py                      # 관리 GUI
# 또는
python step3_proxy.py                  # 콘솔 전용
```

- UDP 5060 = SIP, TCP 8088 = 조회 API
- `step3_proxy.py` 상단 `MY_IP` 는 자동 감지(환경변수 `PBX_IP` 로 고정 가능)
- FCM 푸시를 쓰려면 **본인 Firebase 서비스계정 키(JSON)** 를 PBX 폴더에 두세요
  (보안상 이 저장소에는 포함돼 있지 않습니다)

## 안드로이드 앱 빌드

```bash
cd interphone
./gradlew bundleRelease     # 또는 assembleDebug
```

빌드하려면 **본인 것**을 추가해야 합니다 (저장소에서 제외됨):
- `interphone/app/google-services.json` — Firebase 콘솔에서 받은 클라이언트 설정
- `interphone/keystore.properties` + `interphone/upload-keystore.jks` — 릴리스 서명 키
  (디버그 빌드만 할 거면 서명 키는 불필요)
- `interphone/local.properties` — `sdk.dir=...` (Android SDK 경로)

앱의 PBX 주소는 `SipManager.kt` 의 `SERVER` 상수에서 변경합니다.

## ⚠️ 보안

다음 파일들은 **비밀**이라 `.gitignore` 로 제외되어 저장소에 없습니다.
포크/클론해서 쓰실 때는 본인 것으로 채워주세요.
- Firebase 서비스계정 키 (`*firebase-adminsdk*.json`)
- 서명 키스토어 (`*.jks`, `keystore.properties`)
- `google-services.json`
