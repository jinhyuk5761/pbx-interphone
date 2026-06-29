# Mini PBX 설치 가이드 (Windows + GUI)

다른 Windows PC에 이 PBX를 설치하는 방법. **IP는 자동 감지**되므로 코드 수정 없이 동작합니다.

---

## 1. 복사할 파일

새 PC에 폴더 하나(예: `C:\pbx`)를 만들고 아래 파일을 복사합니다.

**필수**
- `sipmsg.py` — SIP 파서
- `step3_proxy.py` — SIP 서버(엔진)
- `gui_pbx.py` — 관리 GUI
- `requirements.txt` — 의존성 목록
- `run_gui.bat`, `run_server.bat` — 실행 편의 (선택)

**복사 안 해도 됨** (자동 생성됨)
- `pbx_registrar.json` — 등록부. 새 PC에서 새로 생성됨. (이전 PC의 등록정보는 IP가 다르므로 가져오지 말 것)
- `pbx.log` — 로그 파일

> `step1_dump.py`, `step2_register.py`는 학습용이라 운영엔 불필요.

---

## 2. Python 설치

1. https://www.python.org/downloads/ 에서 Python 3.10 이상 설치.
2. 설치 첫 화면에서 **"Add python.exe to PATH" 체크** (중요).
3. 확인:
   ```powershell
   python --version
   ```

---

## 3. 의존성 설치 (GUI용)

새 PC의 `C:\pbx` 폴더에서:
```powershell
cd C:\pbx
python -m pip install -r requirements.txt
```
(PySide6 설치에 수백 MB·몇 분 걸릴 수 있음. 서버만 콘솔로 쓸 거면 이 단계 생략 가능 — `step3_proxy.py`는 표준 라이브러리만 사용.)

---

## 4. 방화벽 허용 (UDP 5060)

전화기가 서버로 SIP를 보낼 수 있게 인바운드 허용. **관리자 PowerShell**에서:
```powershell
New-NetFirewallRule -DisplayName "SIP UDP 5060" -Direction Inbound -Protocol UDP -LocalPort 5060 -Action Allow
```
(또는 첫 실행 시 뜨는 방화벽 팝업에서 "개인 네트워크 허용".)

---

## 5. 실행

- **GUI 관리 콘솔**: `run_gui.bat` 더블클릭 또는
  ```powershell
  python C:\pbx\gui_pbx.py
  ```
- **서버만(콘솔)**: `run_server.bat` 또는 `python C:\pbx\step3_proxy.py`

실행하면 자동 감지된 IP가 상단에 표시됩니다(예: `내 IP 192.168.0.50 | 포트 5060`).

---

## 6. 전화기(IP335S 등) 설정

각 전화기 웹 설정에서 **SIP Server / Proxy** 주소를 **새 PC의 IP**로 변경:
- SIP Server / Proxy: `<새 PC의 IP>`, 포트 `5060`
- 내선번호(User ID): `101`, `102`, ...
- 등록 후 GUI의 "등록 단말" 테이블에 나타나면 성공.

> 전화기를 재부팅하거나 계정 설정을 저장하면 즉시 재등록됩니다.

---

## 7. IP 자동 감지 / 수동 지정

- 기본: 외부로 향하는 기본 라우트의 로컬 IP를 자동 사용.
- **랜카드가 여러 개거나 VPN이 있어 엉뚱한 IP가 잡히면**, 환경변수로 고정:
  ```powershell
  $env:PBX_IP = "192.168.0.50"   # 원하는 IP
  python gui_pbx.py
  ```
  또는 `step3_proxy.py`의 `MY_IP = detect_local_ip()` 줄을 `MY_IP = "192.168.0.50"`처럼 직접 지정.

---

## 8. 주의사항

- **한 LAN에 PBX는 하나만** 5060을 바인드해야 합니다. 기존 miniSIPServer나 다른 PBX가 5060을 쓰고 있으면 종료하세요.
- 두 PC에서 동시에 이 PBX를 켜고 같은 전화기를 등록시키면 충돌합니다. 운영 PC 한 대에서만 실행하세요.
- 서버를 껐다 켜도 `pbx_registrar.json` 덕분에 등록은 (만료 전까지) 유지됩니다.
- 음성(RTP)은 전화기끼리 직접 흐릅니다. 통화는 되는데 한쪽 소리만 안 들리면 전화기/네트워크 방화벽의 RTP 포트를 확인하세요(서버 문제 아님).
