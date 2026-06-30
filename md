# 미니 SIP 서버 (PBX) 프로젝트 진행 기록

> miniSIPServer를 대체하는 Python 순수 socket 기반 SIP 프록시(PBX).
> 외부 SIP 라이브러리 없이 표준 socket으로 SIP 메시지를 직접 파싱/생성.
> **이 문서는 작업할 때마다 갱신한다.**

최종 갱신: 2026-06-26

---

## 1. 목표 / 환경

- **목표**: UDP 5060에서 SIP 수신 → REGISTER로 내선 위치 등록 → INVITE를 상대 내선으로 프록시 → ACK/BYE/CANCEL 중계로 통화 완성.
- **PBX(노트북) IP**: `192.168.0.112`
- **내선 / 실제 단말** (동적 학습, 아래는 현재 관측값):
  - `101` → `192.168.0.114:5060` (MoimStone IP335S)
  - `102` → `192.168.0.113:5060` (MoimStone IP335S)
- **인증(401)**: 폐쇄망 사용이라 현재 생략.
- **미디어(RTP 음성)**: 서버를 거치지 않고 단말끼리 직접. 우리는 **시그널링만 중계**.

---

## 2. 파일 구성

| 파일 | 역할 |
|------|------|
| `sipmsg.py` | SIP 메시지 파서/생성 공용 모듈 (parse/encode, 헤더 헬퍼, `aor_user`) |
| `step3_proxy.py` | **(SIP 엔진)** REGISTER + INVITE/ACK/BYE/CANCEL 프록시 + 안정화 + 조회/토큰 HTTP API + FCM 발송 + GUI용 스냅샷/`run_server` |
| `gui_pbx.py` | **(관리 GUI)** PySide6 기반 miniSIPServer 스타일 관리 콘솔 |
| `pbx-5017e-...0e71872fbb.json` | 🔑 Firebase 서비스계정 키 (FCM 발송용, **비밀 — 공유/커밋 금지**) |
| `requirements.txt` | 의존성(PySide6, firebase-admin) |
| `run_gui.bat` / `run_server.bat` | Windows 실행 편의 배치 |
| `INSTALL.md` | 다른 PC 설치 가이드 (Windows + GUI) |
| `md` | 이 진행 기록 문서 |
| `dist/pbx_gui.exe` | 빌드된 실행 파일 |
| `dist/pbx.log`, `dist/pbx_registrar.json`, `dist/pbx_devices.json`, `dist/pbx_tokens.json` | exe 런타임 데이터(로그·등록부·기기배정·FCM토큰) |

> 정리(2026-06-29): 학습용 `step1_dump.py`/`step2_register.py`, 빌드 임시물(build/, __pycache__, *.spec), 옛 루트 로그/등록부, `pbx_install.zip`, 구버전 `dist/pbx_gui.zip` 삭제. (1·2단계 내용은 아래 "진행 단계"에 기록으로 남김)
> 2026-06-29: 안드로이드 인터폰 앱 프로젝트를 `pbx/interphone/` 하위로 이동(빌드 정상 확인). 앱 소스/키스토어/google-services.json 모두 그 안에 있음.

---

## 3. 진행 단계

### ✅ 1단계 — UDP 수신 + 메시지 덤프 (`step1_dump.py`)
- UDP 5060 바인드, 수신 메시지 출력.
- **검증**: 실제 IP335S 두 대의 REGISTER 메시지 수신 확인. 응답 없으니 재전송 관측(정상).
- 알게 된 점: 내선번호는 `To`/`From`의 user 부분, `Via`에 `;rport`, 등록 위치는 패킷 출발지(addr) 사용이 안전.

### ✅ 2단계 — 파서 + REGISTER + 200 OK (`step2_register.py`, `sipmsg.py`)
- `{내선: (ip, port)}` 등록부 저장, 200 OK 응답.
- **검증**: 101/102 등록 성공, 200 OK 수신 후 재전송 멈춤(=폰이 등록 성공 인식).

### ✅ 3단계 — INVITE 프록시 (통화 연결) (`step3_proxy.py`)
- 요청 전달: 내 `Via`를 맨 위 추가, `Max-Forwards` 감소, 초기 INVITE는 Request-URI를 상대 등록 contact로 교체.
- 응답 라우팅: 내 `Via` 제거 후 다음 `Via` 주소로 전달(RFC 3261, 무상태).
- 다이얼로그 유지: INVITE에 `Record-Route(;lr)` 추가 → ACK/BYE도 우리를 경유. in-dialog 요청은 Call-ID별 통화 상태로 방향 판단.
- **검증**: `REGISTER→INVITE→100→180→200→ACK→(통화)→BYE→200` 전체 흐름 정상. 양방향 음성 확인. BYE는 발신/수신 양쪽 방향 모두 정상.

### ✅ 안정화 (`step3_proxy.py`)
1. **등록 만료 자동 정리**: 백그라운드 스레드(15초 주기)가 `expires_at` 지난 항목 삭제. 만료 내선 호출 시 404.
2. **로깅 정리**: UTF-8 로그 파일(`pbx.log`) + 콘솔 동시, 타임스탬프, 등록 현황 표 출력.
3. **등록 현황 조회**: 콘솔 명령 `list`/`l`(등록), `calls`/`c`(통화), `help`/`h`.
4. **CANCEL 검증 + 버그 수정**: 벨 울릴 때 발신자 취소 → `CANCEL→200→487→ACK` 정상.
5. **(보너스) 등록부 영속화**: `pbx_registrar.json`에 저장/복구 → 재시작해도 등록 유지.

설정 상수(`step3_proxy.py` 상단): `MY_IP`(자동 감지·`PBX_IP` 환경변수로 override 가능), `PORT=5060`, `MAX_EXPIRES=600`(등록 허가 상한·재등록 주기), `CLEANUP_INTERVAL=15`.

### 📦 다른 PC 설치 (포터블화)
- `MY_IP`를 하드코딩 → **자동 감지**(`detect_local_ip()`)로 변경. 우선순위: 환경변수 `PBX_IP` > 외부 라우트 로컬 IP > 127.0.0.1. 이제 어느 PC에 복사해도 코드 수정 없이 동작.
- 설치 파일: `requirements.txt`(PySide6), `run_gui.bat`/`run_server.bat`, `INSTALL.md`(Windows+GUI 가이드).
- 핵심 주의: 한 LAN에 PBX는 1대만 5060 바인드. 전화기 SIP Proxy 주소를 새 PC IP로 변경 후 재등록.
- **단일 exe(서버 전용)**: PyInstaller로 `step3_proxy.py`를 `dist/pbx_server.exe`(약 8.4MB)로 빌드. Python 설치 없이 더블클릭 실행. `sipmsg.py`는 자동 포함. 빌드: `python -m PyInstaller --onefile --console --name pbx_server step3_proxy.py`. 검증: 5060 바인드 + 등록 처리 OK.
  - 콘솔 한글 깨짐 방지: `_enable_utf8_console()`(Windows `SetConsoleOutputCP(65001)` + stdout utf-8 재설정) 추가.
  - **주의(Smart App Control)**: Windows 11 SAC가 "켜짐"이면 서명 안 된 exe를 강제 차단. 대상 PC에서 SAC off면 실행 가능(SmartScreen은 "추가 정보→실행"). SAC는 한 번 끄면 재설치 전까지 못 켬.

### 🚧 관리 GUI (`gui_pbx.py`, PySide6) — 진행 중
miniSIPServer 스타일 윈도우 관리 콘솔. **SIP 엔진은 절대 안 건드림.**

- **아키텍처**: SIP 서버는 백그라운드 데몬 스레드(`step3_proxy.run_server(with_console=False)`)에서 그대로 실행. GUI는 메인 스레드(Qt). 데이터 공유는 `state_lock`으로 보호된 스냅샷 함수만 사용:
  - `snapshot_registrar()` → `[{ext, ip, port, user_agent, expires_in}]`
  - `snapshot_calls()` → `[{call_id, caller_ext, callee_ext, answered, duration}]`
  - GUI는 `QTimer`로 1.5초마다 스냅샷을 읽어 표시(메인 스레드에서만 위젯 갱신).
- **step3_proxy 리팩터링(기능 동일, 호환 유지)**: `main()`을 `run_server()`+`serve_forever()`로 분리, `setup_logging()` 중복호출 방지, REGISTER 시 `user_agent` 저장, 통화 상태에 `caller_ext`/`callee_ext` 추가, 위 스냅샷 함수 추가. CLI 실행(`python step3_proxy.py`)도 그대로 동작.
- **GUI 단계**:
  - ✅ 1단계: 메인 창 + 서버 상태줄 + **등록 단말 테이블**(내선/IP:포트/단말명/만료까지) + 자동갱신.
  - ✅ 2단계: **현재 통화 테이블**(발신/수신/상태/경과시간). `snapshot_calls()` 사용, 상태=통화중/연결중 색상 표시.
  - ✅ 3단계: **실시간 로그 패널**. `QtLogHandler`(logging→thread-safe deque) + QTimer 드레인, 다크 테마, "로그 지우기".
  - 🔧 4단계(다듬기): 상태줄에 등록/통화 개수, 좌우·상하 QSplitter 레이아웃, 5060 사용중이면 안내.
  - ✅ 5단계(운영 기능 추가):
    - **만료시간 무한 토글**: 체크 시 `pbx.INFINITE_EXPIRY=True` → cleanup_loop/lookup_alive 가 등록 만료를 건너뜀(서버가 절대 안 잊음). 표의 "만료까지"는 ∞ 표시.
    - **5060 트래픽 기기 탭**: 엔진이 `seen_sources` 로 5060 에 패킷 보낸 모든 출발지(IP:포트)를 기록 → `snapshot_sources()`. 최근메시지/패킷수/마지막활동/등록내선 표시(미등록 기기도 보임).
    - **로컬 5060 점유 프로세스 표시**: 상단 우측에 netstat+tasklist 로 5060 잡은 프로세스 표시. 2개 이상이면 빨간색 "충돌" 경고(PBX 중복 실행 감지).
  - 레이아웃: 상단 상태줄 / 컨트롤줄(만료무한·5060점유) / [등록단말 | 현재통화] / 하단 탭(실시간 로그 · 5060 트래픽 기기).
  - ⚠️ 주의: PBX(콘솔/GUI)는 한 번에 하나만 실행. 둘 다 켜면 5060 충돌로 GUI 가 빈 등록부를 표시(SO_REUSEADDR 이중바인드).
  - 검증: 1단계는 라이브 데이터로 동작 확인(수 분 구동). 2·3단계는 컴파일·임포트·서버바인드 검증 완료, 라이브 화면 확인은 5060 확보(miniSIPServer 종료) 후 사용자 세션에서.

---

## 4. 버그 / 해결 기록

- **[해결] 실패응답 ACK 무한 재전송**: 첫 CANCEL 테스트에서 `487`이 1·2·4·8초 간격으로 계속 재전송됨.
  - 원인: non-2xx 응답에 대한 ACK는 INVITE와 **동일한 Via branch**여야 트랜잭션 매칭되는데, 새 branch를 생성하고 있었음.
  - 해결: 통화 성립 여부(`answered`) 추적 → 미응답 통화의 ACK는 INVITE branch 재사용. (2xx 통화 ACK는 별도 트랜잭션이라 새 branch 유지.)
- **[완화] 재시작 시 등록부 소실**: 폰이 긴 expires로 등록되어 재시작 후 한동안 빈 등록부.
  - 해결: 등록부 디스크 영속화(`pbx_registrar.json`) + `MAX_EXPIRES`로 재등록 주기 단축.
- 참고: PowerShell `*>` 리다이렉트는 로그를 UTF-16으로 저장 → `grep`/`tail` 매칭 깨짐. `pbx.log`는 Python이 직접 UTF-8로 기록하므로 문제 없음.

---

## 5. 실행 / 테스트 방법

```powershell
# 권장: 터미널에서 직접 실행 (콘솔 명령 list/calls 사용 가능)
python C:\Users\User\pbx\step3_proxy.py
```

- 콘솔에서 `list`(등록 현황), `calls`(통화 현황), `help`.
- 윈도우 방화벽: 최초 실행 시 UDP 5060 인바운드 허용 필요.
  `New-NetFirewallRule -DisplayName "SIP UDP 5060" -Direction Inbound -Protocol UDP -LocalPort 5060 -Action Allow`
- 통화 테스트: 101에서 102 다이얼 → 응답/통화/종료. 취소 테스트: 벨 울릴 때 발신측 종료.

---

## 6. 현재 상태

- 3단계(프록시) + 안정화까지 완료. **통화/취소/등록 만료/영속화 모두 검증됨.** miniSIPServer 핵심 대체 가능 수준.

## 7. 향후 작업 후보 (선택)

- 3대 이상 내선 / 동시 다중 통화 부하 점검
- 통화 중 대기, 호 전환(REFER) 등 부가 기능
- 인증(401 + MD5 다이제스트) — 폐쇄망 벗어날 경우
- 재전송/타임아웃 견고화, 운영용 서비스화(자동 시작)

---

## 변경 이력

- 2026-06-26: 1~3단계 + 안정화(만료정리/로깅/현황조회/CANCEL수정/영속화) 완료. 이 문서 최초 작성.
- 2026-06-26: 관리 GUI 착수(PySide6 설치). step3_proxy를 GUI용으로 리팩터링(run_server/serve_forever/스냅샷/user_agent, 기능 호환). GUI 1단계(등록 단말 테이블) 구현·서버 바인드 검증.
- 2026-06-26: 포터블화. `MY_IP` 자동 감지(+`PBX_IP` override). 설치 파일(requirements.txt, run_gui.bat, run_server.bat, INSTALL.md) 추가. 다른 Windows PC 설치 가이드 작성.
- 2026-06-26: 서버 전용 단일 exe 빌드(`dist/pbx_server.exe`, PyInstaller). UTF-8 콘솔 출력 추가. 실행·바인드·등록 검증(이 PC는 Smart App Control 차단 → 사용자가 off 후 정상 실행 확인).
- 2026-06-26: 프로그램 완성. 콘솔 `main()`에 오류 처리(5060 사용중 안내 + 창 유지). GUI 완성: 현재 통화 테이블 + 실시간 로그 패널 + 상태줄 개수/스플리터 레이아웃. (라이브 GUI 확인은 miniSIPServer 종료로 5060 확보 후.)
- 2026-06-26: GUI 포함 단일 exe 빌드 `dist/pbx_gui.exe`(약 44MB, PyInstaller --onefile --windowed). 실행 검증(창 유지 OK). 다른 PC용 배포물.
- 2026-06-26: GUI 운영 기능 추가 — 만료시간 무한 토글, 5060 트래픽 기기 탭(seen_sources/snapshot_sources), 로컬 5060 점유 프로세스 표시(충돌 감지). exe 재빌드·실행 검증(5060 단독 바인드).
- 2026-06-26: [버그수정] "끊었는데 계속 연결중" — 미응답 통화가 취소/거절(4xx/5xx/6xx 최종응답)되면 `ended=True` 표시 → snapshot_calls 에서 숨김, cleanup 이 10초 내 정리. GUI exe 재빌드.
- 2026-06-26: 조회 HTTP API 추가(포트 8088) — `GET /api/extensions`(등록 내선 목록), `GET /api/next?start=101`(오름차순 빈 번호). 안드로이드 앱이 번호 중복확인·자동배정에 사용. run_server 에서 데몬 스레드로 기동. GUI exe 재빌드. (방화벽 8088 TCP 인바운드 허용 필요할 수 있음 — 관리자 권한.)
- 2026-06-26: 로그/등록부/기기배정 파일을 실행위치 무관 고정경로(BASE_DIR=exe 또는 스크립트 폴더)로 저장. CRLF keep-alive 핑은 조용히 CRLF로 응답(미처리 로그 제거).
- 2026-06-26: 번호 밀림 방지 — 기기ID(앱 ANDROID_ID)→번호 배정을 `pbx_devices.json`에 영속화. `/api/next?device=`로 같은 기기는 기존 번호 재사용. /api/extensions·/api/next 는 **살아있는(만료 전) 등록만** 집계 → 죽은 등록이 번호를 건너뛰지 않음.
- 2026-06-26: 등록 만료 튜닝 — MAX_EXPIRES 2000초로(응답 Expires 항상 2000 강제, 둘 일치), CLEANUP_INTERVAL 30초. REGISTER 로그에 재등록 경과시간 표시: `(요청 Xs -> 부여 2000s, 재등록 / 직전 등록 후 Ns 경과)` / 최초는 "최초 등록". 전화기 실제 재등록 주기 확인용.
- 2026-06-26: [버그수정] 모르는 call 의 BYE 에도 200 OK 응답 → 단말 BYE 무한 재전송 중단. (관측: 폰이 WiFi↔모바일데이터(464XLAT 192.0.0.2) 사이를 오가며 셀룰러 레그 BYE 가 매칭 안 돼 폭주했음 — 근본은 폰 모바일데이터 OFF 로 WiFi 전용 사용.)
- 2026-06-26: REGISTER 로그에 요청 Expires 절반을 "재등록 주기 ~Ns마다"로 표시(예상 주기). 실제 경과(관측)와 함께 비교용.
- 2026-06-26: FCM 1단계(토큰) — 앱: Firebase SDK(messaging) 통합(plugin 4.5.0, bom 33.7.0), FcmService(onNewToken→PBX 전송, onMessageReceived→로그). PBX: `POST /api/token {ext,token}` 엔드포인트 + 내선↔토큰 매핑 `pbx_tokens.json` 영속화. 실제 폰 토큰 등록 검증 완료.
- 2026-06-26: GUI 단말 삭제 — 등록 단말 표에서 선택 후 "선택 단말 삭제" → `unregister_ext(ext)` (registrar 에서 제거+로그). 단말 켜져있으면 재등록 시 재출현.
- 2026-06-26: FCM 2단계(발송) — PBX 가 firebase-admin 으로 푸시 발송. `pip install firebase-admin`. 키: `pbx-5017e-firebase-adminsdk-fbsvc-0e71872fbb.json`(코드 하드코딩 X, 파일에서 읽음; exe/스크립트 위치 모두 탐색). INVITE→토큰등록 내선 시 data 메시지(type=incoming_call, caller/callee/call_id, android priority high) 발송. 발송 성공/실패 로그. 스크립트+exe 모두 발송 성공 검증.
  - **exe 빌드 시 firebase-admin 번들 플래그 필요**: `--collect-all firebase_admin --collect-all google --copy-metadata firebase_admin --copy-metadata google-api-core`. (이거 빼면 exe 에서 FCM 비활성)
  - 다음 단계: 앱 onMessageReceived 에서 IncomingCallActivity 띄우기 + 외부망(셀룰러) 통화 경로.
- 2026-06-29: [앱] 수신 벨소리 = 폰 기본 벨소리로 재설정(MediaPlayer 반복) + 통화 효과음 추가(걸 때 OutgoingInit→TONE_PROP_BEEP, 끊을 때 End→TONE_CDMA_PIP, ToneGenerator). 이어폰 라우팅 강화(call+core 양쪽 지정 + 0.8초 뒤 재적용 + 로그).
- 2026-06-29: [앱][버그수정] **블루투스 이어폰 통화/벨소리** — 매니페스트에 `BLUETOOTH_CONNECT`(+구형 BLUETOOTH/ADMIN maxSdk30) 권한 추가 + 런타임 요청(API31+). 없으면 안드12+ 가 블루투스를 통화 오디오 장치로 못 봐 본체로 나옴. 라우팅이 블루투스 통화용(SCO/HFP, 재생+녹음 가능 Bluetooth) 우선 선택. 수신 벨소리는 블루투스 출력장치 있으면 `setPreferredDevice()`로 강제 출력(USAGE_MEDIA) → 스피커로 새는 것 방지.
  - 로컬 테스트 설치는 **release APK**(서명 일치)로. 폰에 Play 설치본이 있으면 서명 불일치(INSTALL_FAILED_UPDATE_INCOMPATIBLE)로 한 번 uninstall 필요했음.
- 2026-06-30: [앱] UI 다크 미니멀 키패드로 리모델링(colors/themes/styles, activity_main 다이얼러+통화중, activity_incoming 펄스+원형버튼, 벡터 아이콘 자체제작). 다이얼 숫자 30sp, 통화중 컨트롤 68dp+간격 확대.
- 2026-06-30: [앱] 통화중 기능 — 음소거(마이크 뮤트), 스피커폰 전환(AudioDevice.Type.Speaker로 출력 강제+로그). 키패드 버튼=현재 끊고 새 통화(NEW_CALL), 돌려주기=블라인드 전환(REFER, transfer()). pendingAction 으로 다이얼러 재사용.
- 2026-06-30: [PBX] **통화 넘겨주기 지원** — 디스패치에 REFER/NOTIFY/SUBSCRIBE 추가(통화중 in-dialog 요청이라 forward_request 가 반대편으로 중계). exe 재빌드 필요(전환 동작하려면 PBX도 최신이어야 함).
- 2026-06-29: [PBX][버그수정] **INVITE 재전송에 매번 새 branch → 수신단말이 별개 통화로 오인(486 Busy 자동) → 200 OK 무한 재전송 → BYE→481**. 원인: forward_request 의 초기 INVITE 처리가 재전송 때도 `gen_branch()` 로 새 branch 생성·통화상태 덮어쓰기. 수정: call_id 가 이미 있고 ended 아니면 기존 `invite_branch` 재사용(통화상태 보존, FCM 재발송 안 함). 진짜 새 통화만 새 branch 생성. **PBX 재시작 필요.**
- 2026-06-29: [PBX][버그수정] **603 Decline 후 ACK→405 무한 재전송 루프** — 실패응답(non-2xx)의 ACK 는 hop-by-hop. 수정: (1) `send_nonok_ack()` 신설 — PBX 가 3xx~6xx 받는 즉시 수신자에게 INVITE 와 같은 branch 로 ACK 직접 전송(재전송 중단), (2) 발신자가 보낸 실패-ACK 는 PBX 가 흡수(수신자로 전달 안 함; 전달 시 수신자가 트랜잭션 밖 ACK 로 보고 405 거절), (3) 통화상태에 `invite_ruri` 저장해 정확한 ACK 생성. **적용하려면 PBX 재시작 필요.**
