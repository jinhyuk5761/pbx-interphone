"""
step3_proxy.py — 미니 PBX(사설 전화 교환기) SIP 엔진
=====================================================

[이 프로그램이 하는 일 — 한 문장]
전화기/앱들이 보내는 SIP 신호(등록/전화걸기/끊기)를 UDP 5060 포트에서 받아서,
"누가 어디 있는지(등록부)"를 기억하고, 전화를 상대방에게 '중계(proxy)' 합니다.

[비유]
회사 전화 교환원과 같습니다.
- 직원(전화기)이 "저 여기 있어요, 내선 101입니다"(REGISTER) 하면 명단에 적어둡니다.
- 누가 "101 연결해줘"(INVITE) 하면, 명단을 보고 101에게 전화를 돌려줍니다(중계).
- 통화가 끝나면(BYE) 양쪽을 끊어줍니다.

[중요 개념 3가지]
1) 등록부(registrar): 내선번호 -> 그 단말의 실제 IP/포트 를 적어두는 명단.
   전화기는 주기적으로 다시 등록(refresh)해서 "나 아직 살아있어요"를 알립니다.
2) 프록시(proxy): INVITE 같은 요청이 오면 상대에게 '전달'하고, 응답을 '역방향'으로 돌려줍니다.
   목소리(RTP)는 우리를 거치지 않고 전화기끼리 직접 흐릅니다. 우리는 '신호'만 중계.
3) 동시성(thread): 네트워크 수신, 만료 정리, HTTP API 가 동시에 같은 명단을 건드리므로
   state_lock 으로 한 번에 하나씩만 건드리게 보호합니다.

[추가 기능]
- 등록 만료 자동 정리 / 등록부·기기배정·FCM토큰 디스크 저장
- 조회용 HTTP API(8088): 앱이 빈 내선번호 자동배정/중복확인에 사용
- FCM 푸시: 앱(내선)으로 전화 오면 푸시로 깨워서 받게 함
"""

# ---- 표준 라이브러리 (파이썬에 기본 내장) ----
import socket        # UDP/TCP 통신 (네트워크의 핵심)
import time          # 현재 시각, 만료 계산
import random        # 무작위 문자열(브랜치) 생성
import threading     # 동시 실행(스레드)과 잠금(Lock)
import logging       # 로그 기록 (화면 + 파일)
import sys           # 표준출력, 실행환경(exe 여부) 확인
import json          # 등록부/토큰을 파일(JSON)로 저장/복구
import os            # 파일 경로, 환경변수
from http.server import BaseHTTPRequestHandler, HTTPServer   # 간단한 HTTP API 서버
from urllib.parse import urlparse, parse_qs                  # HTTP 주소/쿼리 파싱

# firebase-admin 은 '있으면 쓰고 없으면 끄는' 선택적 의존성입니다.
# (설치 안 돼 있어도 PBX 자체는 돌아가야 하므로 try/except 로 감쌈)
try:
    import firebase_admin
    from firebase_admin import credentials, messaging
    _FCM_AVAILABLE = True
except Exception:
    _FCM_AVAILABLE = False

import sipmsg        # 우리가 만든 SIP 파서 (같은 폴더의 sipmsg.py)


def detect_local_ip():
    """
    이 PC 의 '랜(LAN) IP' 를 자동으로 알아냅니다.
    (다른 PC 에 그대로 복사해도 동작하도록 — IP 하드코딩 방지)

    우선순위:
      1) 환경변수 PBX_IP 가 있으면 그 값
      2) 인터넷 방향으로 나가는 경로의 내 IP (실제 패킷은 안 보냄)
      3) 둘 다 실패하면 127.0.0.1
    """
    env = os.environ.get("PBX_IP")
    if env:
        return env
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # 8.8.8.8(구글 DNS)로 'connect' 하면 OS 가 "그쪽으로 나가려면 내 어떤 IP 를 쓰지?"
        # 를 정해줍니다. UDP connect 는 실제 패킷을 보내지 않으므로 안전/빠릅니다.
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


# ==================== 설정값(상수) ====================
# IP 를 고정하고 싶으면 아래를 직접 문자열로 바꾸거나, 환경변수 PBX_IP 로 지정.
MY_IP = detect_local_ip()    # 이 PBX(서버)의 IP. SIP 의 Via/Record-Route 에 들어감.
PORT = 5060                  # SIP 표준 포트 (UDP)
HOST = "0.0.0.0"             # 0.0.0.0 = 이 PC 의 '모든' 네트워크 카드에서 수신

# 로그/저장 파일은 '실행 위치와 무관하게' 항상 프로그램과 같은 폴더에 둡니다.
# (exe 로 빌드되면 sys.frozen=True. 그땐 exe 폴더, 아니면 이 .py 파일 폴더)
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(BASE_DIR, "pbx.log")              # 로그 파일
REG_FILE = os.path.join(BASE_DIR, "pbx_registrar.json")   # 등록부 저장 파일
DEVICE_FILE = os.path.join(BASE_DIR, "pbx_devices.json")  # 기기ID -> 배정번호 저장
TOKEN_FILE = os.path.join(BASE_DIR, "pbx_tokens.json")    # 내선 -> FCM토큰 저장
FCM_KEY_NAME = "pbx-5017e-firebase-adminsdk-fbsvc-0e71872fbb.json"   # Firebase 비밀키 파일명

MAX_EXPIRES = 3600    # 등록 만료(초). 응답으로도 항상 이 값을 줘서 "만료시각 = 알려준 값"을 일치.
                      # 전화기 재등록 주기(~1800s)보다 넉넉히 커서, 인터넷이 잠깐 끊겨도 안 끊김.
CLEANUP_INTERVAL = 30 # 만료된 등록을 청소하는 주기(초)
INFINITE_EXPIRY = False   # GUI 토글. True 면 만료로 등록을 절대 안 지움(죽은 단말도 유지)
HTTP_PORT = 8088      # 조회용 HTTP API 포트 (앱이 번호 배정/중복확인에 사용)
AUTO_START = 101      # 번호 자동배정 시작값 (101 부터 오름차순으로 빈 번호 찾기)

# ==================== 서버가 들고 있는 '상태(데이터)' ====================
# 등록부: { "101": {"addr":(ip,port), "contact":"<uri>", "user_agent":..., "expires_at":시각} }
registrar = {}
# 통화 상태: { call_id: {"caller":(ip,port), "callee":(ip,port), "invite_branch":..., ...} }
calls = {}
# 5060 으로 패킷을 보낸 모든 출발지(네트워크 기기 목록용): {(ip,port): {first,last,count,last_method}}
seen_sources = {}
# 기기 식별자(앱 ANDROID_ID) -> 배정된 내선번호 (재설치해도 같은 번호 유지)
device_assignments = {}
# 내선번호 -> FCM 토큰 (앱으로 푸시 보낼 때 사용)
fcm_tokens = {}

# [중요] 위 데이터들은 여러 스레드가 동시에 건드립니다.
# Lock(자물쇠)으로 "with state_lock:" 블록 안에서는 한 스레드만 접근하게 보호합니다.
state_lock = threading.Lock()


# ---------------------------------------------------------------- 로깅(기록)
def _enable_utf8_console():
    """Windows 콘솔에서 한글이 깨지지 않게 출력 인코딩을 UTF-8 로 맞춥니다."""
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)   # 콘솔 코드페이지 = UTF-8
        except Exception:
            pass
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def setup_logging():
    """
    로그를 '화면(콘솔)' 과 'pbx.log 파일' 두 곳에 동시에 남기도록 설정합니다.
    (이미 설정돼 있으면 또 안 함 — GUI/CLI 가 둘 다 호출해도 안전)
    """
    logger = logging.getLogger("pbx")
    if logger.handlers:                  # 이미 핸들러가 붙어있으면 = 이미 설정됨
        return logger
    _enable_utf8_console()
    logger.setLevel(logging.INFO)
    # 로그 형식: "시:분:초 메시지"
    fmt = logging.Formatter("%(asctime)s %(message)s", datefmt="%H:%M:%S")

    fh = logging.FileHandler(LOG_FILE, encoding="utf-8")   # 파일에 기록(UTF-8 → 한글 OK)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    ch = logging.StreamHandler(sys.stdout)                 # 화면에 기록
    ch.setFormatter(fmt)
    logger.addHandler(ch)
    return logger


# 코드 곳곳에서 log.info("...") 로 로그를 남길 때 쓰는 객체
log = logging.getLogger("pbx")


# ---------------------------------------------------------------- 작은 도우미 함수들
def gen_branch():
    """
    SIP 'branch' 값을 새로 만듭니다.
    branch 는 하나의 요청-응답(트랜잭션)을 구분하는 고유 ID 입니다.
    규칙상 'z9hG4bK' 로 시작해야 합니다.
    """
    return "z9hG4bK" + "".join(random.choice("0123456789abcdef") for _ in range(16))


def contact_uri(contact_header):
    """
    Contact 헤더에서 < > 안의 실제 주소(URI)만 꺼냅니다.
    예: '<sip:101@192.168.0.114:5060;transport=udp>;expires=3600'
        -> 'sip:101@192.168.0.114:5060;transport=udp'
    """
    if not contact_header:
        return None
    if "<" in contact_header and ">" in contact_header:
        return contact_header[contact_header.index("<") + 1: contact_header.index(">")]
    return contact_header.split(";")[0].strip()


def via_target(via_value):
    """
    Via 헤더를 보고 '응답을 어디로 돌려보낼지' (ip, port) 를 계산합니다.

    Via 예: 'SIP/2.0/UDP 192.168.0.50:5060;branch=...;received=1.2.3.4;rport=6000'
    - 기본은 'sent-by'(192.168.0.50:5060) 를 씁니다.
    - 단, NAT 환경 대비로 received=(진짜 IP), rport=(진짜 포트) 가 있으면 그걸 우선합니다.
    """
    params = via_value.split(";")
    sentby = params[0].split()[-1]                  # "SIP/2.0/UDP 1.2.3.4:5060" 에서 "1.2.3.4:5060"
    host = sentby.split(":")[0]
    port = int(sentby.split(":")[1]) if ":" in sentby else 5060
    for p in params[1:]:
        p = p.strip()
        if p.startswith("received="):
            host = p.split("=", 1)[1]               # 실제 IP 로 교체
        elif p.startswith("rport="):
            try:
                port = int(p.split("=", 1)[1])      # 실제 포트로 교체
            except ValueError:
                pass
    return (host, port)


def make_response(req, code, reason):
    """
    어떤 요청(req)에 대한 'SIP 응답' 메시지를 만듭니다. (예: 200 OK, 404 Not Found)
    응답은 요청의 Via/From/To/Call-ID/CSeq 를 그대로 되돌려줘야 단말이 매칭할 수 있습니다.
    """
    resp = sipmsg.SipMessage()
    resp.is_response = True
    resp.status_code = code
    resp.reason = reason
    # Via 는 요청에 있던 것을 '전부' 그대로 복사 (응답 라우팅에 사용됨)
    for via in req.get_all("Via"):
        resp.headers.append(("Via", via))
    if req.get("From"):
        resp.headers.append(("From", req.get("From")))
    to = req.get("To")
    # To 에 tag 가 없으면 우리가 하나 붙여줍니다(응답 규칙).
    if to and ";tag=" not in to:
        to = to + f";tag=pbx{int(time.time()) % 100000}"
    if to:
        resp.headers.append(("To", to))
    for h in ("Call-ID", "CSeq"):
        if req.get(h):
            resp.headers.append((h, req.get(h)))
    resp.set("Content-Length", "0")     # 바디 없음
    return resp


# ---------------------------------------------------------------- 파일 저장/복구 (영속화)
# 프로그램을 껐다 켜도 등록/배정/토큰이 유지되도록 JSON 파일로 저장하고 시작 시 불러옵니다.

def save_registrar():
    """등록부를 JSON 파일로 저장. (lock 으로 안전하게 복사본을 떠서 파일에 씀)"""
    try:
        with state_lock:
            # addr 는 튜플(ip,port)인데 JSON 은 튜플이 없어 list 로 변환해 저장
            snapshot = {ext: {"addr": list(r["addr"]),
                              "contact": r.get("contact"),
                              "user_agent": r.get("user_agent", ""),
                              "expires_at": r["expires_at"]}
                        for ext, r in registrar.items()}
        # 임시파일에 먼저 쓰고 os.replace 로 한 번에 교체 = 쓰다 죽어도 파일이 안 깨짐(원자적)
        tmp = REG_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, ensure_ascii=False, indent=2)
        os.replace(tmp, REG_FILE)
    except Exception as e:
        log.info(f"[!] registrar 저장 실패: {e}")


def load_registrar():
    """시작할 때 등록부를 파일에서 복구. 이미 만료시간이 지난 항목은 버립니다."""
    if not os.path.exists(REG_FILE):
        return
    try:
        with open(REG_FILE, encoding="utf-8") as f:
            data = json.load(f)
        now = time.time()
        with state_lock:
            for ext, r in data.items():
                if r["expires_at"] > now:                # 아직 안 만료된 것만 복구
                    registrar[ext] = {"addr": tuple(r["addr"]),   # list -> 다시 튜플로
                                      "contact": r.get("contact"),
                                      "user_agent": r.get("user_agent", ""),
                                      "expires_at": r["expires_at"]}
        log.info(f"[LOAD] 등록부 복구 ({len(registrar)}개):\n" + fmt_registrar())
    except Exception as e:
        log.info(f"[!] registrar 복구 실패: {e}")


def save_devices():
    """기기ID->번호 배정표 저장 (위 save_registrar 와 같은 방식)."""
    try:
        with state_lock:
            snap = dict(device_assignments)
        tmp = DEVICE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(snap, f, ensure_ascii=False, indent=2)
        os.replace(tmp, DEVICE_FILE)
    except Exception as e:
        log.info(f"[!] devices 저장 실패: {e}")


def load_devices():
    """기기ID->번호 배정표 복구."""
    if not os.path.exists(DEVICE_FILE):
        return
    try:
        with open(DEVICE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        with state_lock:
            device_assignments.update(data)
        log.info(f"[LOAD] 기기 배정 복구 ({len(device_assignments)}개)")
    except Exception as e:
        log.info(f"[!] devices 복구 실패: {e}")


def save_tokens():
    """내선->FCM토큰 표 저장."""
    try:
        with state_lock:
            snap = dict(fcm_tokens)
        tmp = TOKEN_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(snap, f, ensure_ascii=False, indent=2)
        os.replace(tmp, TOKEN_FILE)
    except Exception as e:
        log.info(f"[!] tokens 저장 실패: {e}")


def load_tokens():
    """내선->FCM토큰 표 복구."""
    if not os.path.exists(TOKEN_FILE):
        return
    try:
        with open(TOKEN_FILE, encoding="utf-8") as f:
            data = json.load(f)
        with state_lock:
            fcm_tokens.update(data)
        log.info(f"[LOAD] FCM 토큰 복구 ({len(fcm_tokens)}개): {sorted(fcm_tokens)}")
    except Exception as e:
        log.info(f"[!] tokens 복구 실패: {e}")


def unregister_ext(ext):
    """관리자(GUI)가 특정 내선의 등록을 강제로 지웁니다. 지웠으면 True 반환."""
    with state_lock:
        existed = registrar.pop(ext, None)   # 명단에서 제거 (없으면 None)
    if existed:
        save_registrar()
        log.info(f"[ADMIN] 내선 {ext} 등록 강제 삭제")
    return existed is not None


# ---------------------------------------------------------------- FCM 푸시(앱 깨우기)
# 앱(내선)이 백그라운드/종료 상태여도 전화가 오면 푸시로 깨워서 받게 합니다.
_fcm_app = None              # firebase 앱 객체 (1회 초기화)
_fcm_init_done = False       # 초기화 시도 했는지
_fcm_lock = threading.Lock() # 초기화 동시 진입 방지


def _fcm_key_path():
    """Firebase 비밀키 파일을 여러 후보 위치에서 찾습니다 (exe/스크립트 위치 모두 대응)."""
    candidates = [
        os.path.join(BASE_DIR, FCM_KEY_NAME),
        os.path.join(os.path.dirname(BASE_DIR), FCM_KEY_NAME),
        os.path.join(r"C:\Users\User\pbx", FCM_KEY_NAME),
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return None


def init_fcm():
    """firebase-admin 을 딱 1번만 초기화. 푸시를 보낼 수 있으면 True."""
    global _fcm_app, _fcm_init_done
    with _fcm_lock:
        if _fcm_init_done:                       # 이미 시도했으면 결과만 재사용
            return _fcm_app is not None
        _fcm_init_done = True
        if not _FCM_AVAILABLE:                   # 라이브러리 자체가 없음
            log.info("[FCM] firebase-admin 미설치 -> 푸시 비활성")
            return False
        key = _fcm_key_path()
        if not key:                              # 비밀키 파일이 없음
            log.info(f"[FCM] 서비스계정 키({FCM_KEY_NAME}) 없음 -> 푸시 비활성")
            return False
        try:
            cred = credentials.Certificate(key)  # 비밀키로 인증정보 생성
            _fcm_app = firebase_admin.initialize_app(cred)
            log.info(f"[FCM] 초기화 완료 (키: {os.path.basename(key)})")
            return True
        except Exception as e:
            log.info(f"[FCM] 초기화 실패: {e}")
            return False


def send_fcm_push(callee_ext, caller_ext, call_id):
    """
    수신자(callee_ext)에게 FCM 토큰이 등록돼 있으면, 그 토큰으로 '전화 왔다' 푸시를 보냅니다.
    네트워크 발송은 시간이 걸릴 수 있어 별도 스레드에서 처리(SIP 수신을 막지 않게).
    """
    with state_lock:
        token = fcm_tokens.get(callee_ext)
    if not token:
        return                       # 토큰 없는 내선(예: 일반 하드폰)은 푸시 안 함
    if not init_fcm():
        return                       # FCM 사용 불가

    def _send():
        try:
            # 'data' 메시지로 보내면 앱이 백그라운드여도 onMessageReceived 가 호출됨.
            # priority=high 면 앱을 빠르게(졸고 있어도) 깨울 수 있음.
            msg = messaging.Message(
                data={
                    "type": "incoming_call",
                    "caller": str(caller_ext or ""),
                    "callee": str(callee_ext),
                    "call_id": str(call_id or ""),
                },
                token=token,
                android=messaging.AndroidConfig(priority="high"),
            )
            resp = messaging.send(msg)
            log.info(f"[FCM] 푸시 발송 성공: 내선 {callee_ext} <- 발신 {caller_ext} (id={resp})")
        except Exception as e:
            log.info(f"[FCM] 푸시 발송 실패: 내선 {callee_ext}: {e}")

    threading.Thread(target=_send, daemon=True).start()


# ---------------------------------------------------------------- 보기 좋은 문자열 / 스냅샷
def fmt_registrar():
    """등록 현황을 사람이 보기 좋은 여러 줄 문자열로 만듭니다(로그/콘솔용)."""
    with state_lock:
        if not registrar:
            return "  (등록된 내선 없음)"
        now = time.time()
        lines = []
        for ext in sorted(registrar):
            r = registrar[ext]
            ip, p = r["addr"]
            left = int(r["expires_at"] - now)               # 만료까지 남은 초
            lines.append(f"  {ext:<6} {ip}:{p:<6}  만료까지 {left:>4}s")
        return "\n".join(lines)


def fmt_calls():
    """진행 중 통화를 보기 좋은 문자열로(콘솔용)."""
    with state_lock:
        if not calls:
            return "  (진행 중 통화 없음)"
        lines = []
        for cid, c in calls.items():
            lines.append(f"  {cid[:12]}  {c['caller'][0]} <-> {c['callee'][0]}")
        return "\n".join(lines)


# GUI(다른 스레드)가 내부 데이터를 직접 만지면 위험하므로, lock 으로 보호된 '복사본'만 넘겨줍니다.
def snapshot_registrar():
    """등록 단말 목록 복사본. [{ext, ip, port, user_agent, expires_in}, ...]"""
    now = time.time()
    with state_lock:
        return [
            {
                "ext": ext,
                "ip": r["addr"][0],
                "port": r["addr"][1],
                "user_agent": r.get("user_agent", ""),
                "expires_in": int(r["expires_at"] - now),
            }
            for ext, r in sorted(registrar.items())
        ]


def _record_source(addr, msg):
    """5060 으로 패킷을 보낸 모든 출발지를 기록(네트워크에 어떤 기기가 통신했는지 보기 위함)."""
    if msg.is_response:
        method = f"응답 {msg.status_code}"
    else:
        method = msg.method or "?"
    now = time.time()
    with state_lock:
        s = seen_sources.get(addr)
        if s:                                  # 이미 본 출발지면 횟수/시각만 갱신
            s["last"] = now
            s["count"] += 1
            s["last_method"] = method
        else:                                  # 처음 보는 출발지면 새로 기록
            seen_sources[addr] = {"first": now, "last": now,
                                  "count": 1, "last_method": method}


def snapshot_sources():
    """5060 으로 통신한 기기 목록 복사본. [{ip,port,count,last_method,idle,ext}]"""
    now = time.time()
    with state_lock:
        # (ip,port) -> 내선번호 역매핑 (등록된 기기면 내선번호도 같이 보여주려고)
        reg_map = {tuple(r["addr"]): ext for ext, r in registrar.items()}
        items = [
            {
                "ip": ip,
                "port": port,
                "count": s["count"],
                "last_method": s["last_method"],
                "idle": int(now - s["last"]),      # 마지막 통신 후 경과 초
                "ext": reg_map.get((ip, port), ""),
            }
            for (ip, port), s in seen_sources.items()
        ]
    items.sort(key=lambda x: x["idle"])            # 최근 활동순 정렬
    return items


def snapshot_calls():
    """진행 중 통화 목록 복사본. (종료 표시된 통화는 숨김)"""
    now = time.time()
    with state_lock:
        return [
            {
                "call_id": cid,
                "caller_ext": c.get("caller_ext", c["caller"][0]),
                "callee_ext": c.get("callee_ext", c["callee"][0]),
                "caller_ip": c["caller"][0],
                "callee_ip": c["callee"][0],
                "answered": c.get("answered", False),         # 통화 연결됨 여부
                "duration": int(now - c.get("created", now)), # 경과 시간(초)
            }
            for cid, c in calls.items()
            if not c.get("ended")                            # 끝난 통화는 목록에서 제외
        ]


# ---------------------------------------------------------------- REGISTER 처리
def handle_register(sock, req, addr):
    """
    전화기가 "나 여기 있어요"(REGISTER)를 보내면 호출됩니다.
    - 내선번호를 알아내 등록부에 (IP/포트와 함께) 적습니다.
    - 200 OK 로 "등록됐어요, N초간 유효" 응답을 돌려줍니다.
    - Expires:0 이면 '등록 해제' 요청 -> 명단에서 지웁니다.
    sock: UDP 소켓,  req: 파싱된 REGISTER 메시지,  addr: 보낸 곳 (ip, port)
    """
    # 내선번호는 To 헤더의 user 부분(없으면 From)에서 추출
    ext = sipmsg.aor_user(req.get("To")) or sipmsg.aor_user(req.get("From"))
    expires_hdr = req.get("Expires")
    req_expires = int(expires_hdr) if expires_hdr and expires_hdr.isdigit() else 3600

    if req_expires == 0:
        # Expires:0 = "나 로그아웃" -> 등록부에서 제거
        with state_lock:
            existed = registrar.pop(ext, None)
        if existed:
            log.info(f"[REG] {ext} 등록 해제 (from {addr[0]}:{addr[1]})")
        granted = 0
    else:
        # 항상 MAX_EXPIRES 로 부여(전화기가 더 길게 요청해도 줄여서 응답 = 만료시각 일치).
        granted = MAX_EXPIRES
        now = time.time()
        with state_lock:
            old = registrar.get(ext)
            # 직전 등록 이후 몇 초 만에 다시 왔는지(실제 재등록 주기 관찰용)
            interval = int(now - old["registered_at"]) if old and old.get("registered_at") else None
            registrar[ext] = {
                "addr": addr,                                  # 이 단말의 실제 IP/포트
                "contact": contact_uri(req.get("Contact")),
                "user_agent": req.get("User-Agent") or "",     # 단말 종류(예: MOIMSTONE IP335S)
                "expires_at": now + granted,                   # 이 시각 지나면 만료
                "registered_at": now,
            }
        cyc = f"재등록 / 직전 등록 후 {interval}s 경과" if interval is not None else "최초 등록"
        period = req_expires // 2                              # 보통 요청값 절반이 재등록 주기
        log.info(f"[REG] {ext} -> {addr[0]}:{addr[1]} "
                 f"(요청 {req_expires}s -> 부여 {granted}s, 재등록 주기 ~{period}s마다, {cyc})")
        log.info("[REG] 현재 등록:\n" + fmt_registrar())

    save_registrar()                                           # 파일에도 저장

    # 200 OK 응답 만들어 보내기 (Contact 그대로 echo, 부여한 Expires 명시)
    resp = make_response(req, 200, "OK")
    if req.get("Contact"):
        resp.headers.append(("Contact", req.get("Contact")))
    resp.set("Expires", str(granted))
    sock.sendto(resp.encode(), addr)


# ---------------------------------------------------------------- 요청 중계 (INVITE/ACK/BYE/CANCEL)
def strip_our_route(msg):
    """
    맨 위 Route 헤더가 '우리(MY_IP)'를 가리키면 제거합니다.
    (loose routing: 통화 중 요청이 우리를 거쳐가도록 Record-Route 를 넣었는데,
     우리에게 도착하면 그 Route 한 개는 떼고 다음으로 보내야 함)
    """
    routes = msg.get_all("Route")
    if routes and MY_IP in routes[0]:
        removed = False
        new = []
        for n, v in msg.headers:
            if not removed and n.lower() == "route" and MY_IP in v:
                removed = True          # 우리 Route '한 개'만 제거
                continue
            new.append((n, v))
        msg.headers = new


def lookup_alive(ext):
    """등록부에서 'ext' 를 찾되, 만료 안 된 살아있는 것만 돌려줍니다. 없으면 None."""
    with state_lock:
        reg = registrar.get(ext)
        if not reg:
            return None
        if not INFINITE_EXPIRY and reg["expires_at"] < time.time():
            registrar.pop(ext, None)    # 만료된 건 이 김에 청소
            return None
        return dict(reg)                # 복사본 반환(밖에서 안전하게 사용)


def send_nonok_ack(sock, st, resp):
    """
    실패응답(3xx~6xx, 예: 603 Decline / 486 Busy)을 보낸 '수신자'에게
    PBX 가 직접 ACK 를 보냅니다.

    [왜 필요한가]
    SIP 규칙상 실패응답에 대한 ACK 는 'hop-by-hop'(구간별) 이라, 응답을 받은 쪽이
    바로 그 응답을 보낸 쪽에게 ACK 를 돌려줘야 합니다. PBX 가 이 ACK 를 안 보내면
    수신자(전화기)는 "내 603 을 못 받았나?" 하며 603 을 계속 재전송합니다.
    (발신자가 보내는 ACK 를 그냥 수신자로 넘기면, 수신자가 트랜잭션 밖 요청으로 보고
     405 로 거절 -> 무한 재전송 루프가 생깁니다. 그래서 PBX 가 직접 만들어 보냅니다.)

    INVITE 와 '같은 branch' 를 써야 수신자의 INVITE 트랜잭션과 매칭됩니다.
    """
    try:
        ack = sipmsg.SipMessage()
        ack.method = "ACK"
        ack.uri = st.get("invite_ruri") or resp.get("To")    # 요청대상 = INVITE 와 동일
        # Via: INVITE 때 우리가 쓴 branch 그대로 (트랜잭션 매칭)
        ack.headers.append(("Via", f"SIP/2.0/UDP {MY_IP}:{PORT};branch={st['invite_branch']}"))
        ack.set("Max-Forwards", "70")
        if resp.get("From"):
            ack.headers.append(("From", resp.get("From")))    # 발신자(태그 포함)
        if resp.get("To"):
            ack.headers.append(("To", resp.get("To")))        # 수신자(603 이 붙인 태그 포함)
        if resp.get("Call-ID"):
            ack.headers.append(("Call-ID", resp.get("Call-ID")))
        # CSeq 번호는 INVITE 와 같게, 메서드만 ACK 로 (예: "1 INVITE" -> "1 ACK")
        cseq_num = (resp.get("CSeq") or "1 INVITE").split()[0]
        ack.headers.append(("CSeq", f"{cseq_num} ACK"))
        ack.set("Content-Length", "0")
        sock.sendto(ack.encode(), st["callee"])
        log.info(f"[ACK] PBX -> {st['callee'][0]} (실패응답 {resp.status_code} 확인, 재전송 중단)")
    except Exception as e:
        log.info(f"[!] 실패응답 ACK 생성 오류: {e}")


def forward_request(sock, req, src_addr):
    """
    전화 관련 '요청'(INVITE/ACK/BYE/CANCEL)을 상대에게 중계합니다.
    핵심 아이디어:
      - 우리 Via 를 맨 위에 추가 -> 그래야 '응답'이 우리를 거쳐 되돌아옴.
      - 초기 INVITE 면 상대 등록주소를 찾아 보내고, 통화 상태(calls)를 기록.
      - 통화 도중 요청(ACK/BYE 등)은 통화 상태를 보고 '반대편'으로 보냄.
    """
    method = req.method
    call_id = req.get("Call-ID")                 # 통화를 구분하는 고유 ID
    to_tag = ";tag=" in (req.get("To") or "")    # To 에 tag 가 있으면 '통화 도중' 요청

    reuse_branch = None     # 특정 경우 INVITE 와 같은 branch 를 재사용해야 함(아래 설명)

    if method == "INVITE" and not to_tag:
        # ===== (1) 새 전화 걸기 (초기 INVITE) =====
        callee_ext = sipmsg.aor_user(req.uri)    # 누구한테 거는지(목적지 내선)
        reg = lookup_alive(callee_ext)
        if not reg:
            # 상대가 등록 안 돼 있으면 "404 없음" 으로 발신자에게 응답
            log.info(f"[INV] {callee_ext} 미등록/만료 -> 404")
            sock.sendto(make_response(req, 404, "Not Found").encode(), src_addr)
            return
        dest = reg["addr"]                       # 상대의 실제 IP/포트
        if reg.get("contact"):
            req.uri = reg["contact"]             # 요청 대상 주소를 상대의 Contact 로 교체
        caller_ext = sipmsg.aor_user(req.get("From"))
        with state_lock:
            existing = calls.get(call_id)
            if existing and not existing.get("ended"):
                # 재전송된 INVITE: 반드시 '같은 branch' 를 재사용해야 함.
                # (새 branch 를 주면 수신 단말이 별개의 새 통화로 오인 -> 한쪽을 486 Busy 로
                #  거절하고, 발신자는 먼저 온 486 으로 통화를 끝내 200 OK 가 무한 재전송됨)
                branch = existing["invite_branch"]
                is_new_call = False
            else:
                # 진짜 새 통화일 때만 새 branch 생성 + 통화상태 기록
                branch = gen_branch()
                is_new_call = True
                calls[call_id] = {"caller": src_addr, "callee": dest,
                                  "caller_ext": caller_ext, "callee_ext": callee_ext,
                                  "invite_branch": branch, "answered": False,
                                  "invite_ruri": req.uri,   # 실패응답(603 등)의 ACK 를 만들 때 사용
                                  "created": time.time()}
        reuse_branch = branch
        log.info(f"[INV] {caller_ext}({src_addr[0]}) -> {callee_ext}({dest[0]})")
        # 상대가 앱(FCM 토큰 보유)이면 푸시로 깨움. 새 통화일 때만 1번(재전송 땐 중복 X).
        if is_new_call:
            send_fcm_push(callee_ext, caller_ext, call_id)
    else:
        # ===== (2) 통화 도중 요청 (ACK / BYE / CANCEL / re-INVITE) =====
        with state_lock:
            st = calls.get(call_id)
            st = dict(st) if st else None
        if not st:
            # 우리가 모르는 통화의 요청
            if method == "BYE":
                # BYE 는 200 OK 라도 줘야 상대가 'BYE 재전송'을 멈춥니다.
                sock.sendto(make_response(req, 200, "OK").encode(), src_addr)
                log.info(f"[BYE] 알 수 없는 call {call_id[:8]} -> 200 OK (재전송 중단)")
            else:
                log.info(f"[{method}] 알 수 없는 call {call_id}, 무시")
            return
        # 보낸 쪽이 발신자면 -> 수신자에게, 수신자면 -> 발신자에게 (반대편으로 중계)
        if src_addr == st["caller"]:
            dest = st["callee"]
        elif src_addr == st["callee"]:
            dest = st["caller"]
        else:
            # 포트가 살짝 달라도 IP 로 판단(예외 대비)
            dest = st["callee"] if src_addr[0] == st["caller"][0] else st["caller"]
        log.info(f"[{method}] {src_addr[0]} -> {dest[0]} (call {call_id[:8]})")

        if method == "CANCEL":
            # CANCEL 은 '취소할 INVITE' 와 같은 branch 여야 상대가 매칭함
            reuse_branch = st.get("invite_branch")
        if method == "ACK" and not st.get("answered"):
            # 실패응답(486/603 등)에 대한 ACK 는 hop-by-hop 입니다.
            # PBX 가 실패응답을 받는 즉시 '수신자'에게 직접 ACK 를 보냈으므로
            # (forward_response 의 send_nonok_ack 참고), 발신자가 보낸 이 ACK 는
            # 여기서 '흡수'하고 수신자에게 전달하지 않습니다.
            # (전달하면 수신자가 트랜잭션 밖 ACK 로 보고 405 로 거절 -> 603 무한 재전송)
            log.info(f"[ACK] 실패응답 ACK 흡수, 전달 안 함 (call {call_id[:8]})")
            return
        if method == "BYE":
            with state_lock:
                calls.pop(call_id, None)         # 통화 종료 -> 상태 제거

    # --- 여기부터는 모든 요청 공통: 헤더 손질 후 상대에게 전달 ---
    strip_our_route(req)                          # 우리 Route 제거(loose routing)

    branch = reuse_branch or gen_branch()
    # 우리 Via 를 맨 위에 추가 = "응답은 나를 거쳐 돌아와" 표시
    req.headers.insert(0, ("Via", f"SIP/2.0/UDP {MY_IP}:{PORT};branch={branch}"))

    if method == "INVITE":
        # Record-Route = "이 통화의 이후 요청들(ACK/BYE)도 나를 거쳐가" 표시
        req.headers.insert(1, ("Record-Route", f"<sip:{MY_IP}:{PORT};lr>"))

    # Max-Forwards 1 감소(무한 루프 방지용 홉 카운터)
    mf = req.get("Max-Forwards")
    if mf and mf.isdigit():
        req.set("Max-Forwards", str(max(0, int(mf) - 1)))

    sock.sendto(req.encode(), dest)               # 상대에게 전달!


# ---------------------------------------------------------------- 응답 중계
def forward_response(sock, resp):
    """
    상대가 보낸 '응답'(100/180/200/404/487 ...)을 발신자 쪽으로 되돌려 보냅니다.
    원리: 맨 위 Via 가 '우리'이므로, 그걸 떼어내고 그 다음 Via 가 가리키는 곳으로 보냅니다.
    """
    vias = resp.get_all("Via")
    if not vias:
        return
    if MY_IP not in vias[0]:
        # 맨 위 Via 가 우리가 아니면 우리가 처리할 응답이 아님
        log.info(f"[RESP] top Via 가 우리 것이 아님, 무시: {vias[0]}")
        return
    # 우리 Via '한 개' 제거
    removed = False
    new = []
    for n, v in resp.headers:
        if not removed and n.lower() == "via" and MY_IP in v:
            removed = True
            continue
        new.append((n, v))
    resp.headers = new

    # INVITE 에 대한 응답이면 통화 상태를 갱신:
    #  - 200~299(성공) -> answered=True (연결됨)
    #  - 300 이상(실패) -> ended=True   (취소/거절/통화중/미응답)
    cseq = resp.get("CSeq") or ""
    nonok_st = None     # 실패응답이면, 수신자에게 직접 ACK 보낼 통화상태(복사본)
    if cseq.endswith("INVITE"):
        cid = resp.get("Call-ID")
        with state_lock:
            if cid in calls:
                if 200 <= resp.status_code < 300:
                    calls[cid]["answered"] = True
                elif resp.status_code >= 300:
                    calls[cid]["ended"] = True
                    nonok_st = dict(calls[cid])   # lock 밖에서 ACK 만들 때 쓸 복사본
    # 실패응답(3xx~6xx)이면 수신자에게 PBX 가 직접 ACK 를 보내 재전송을 멈춤
    if nonok_st is not None:
        send_nonok_ack(sock, nonok_st, resp)

    # 다음 Via(=발신자 쪽)로 응답 전달
    next_via = resp.get("Via")
    if not next_via:
        return
    dest = via_target(next_via)
    log.info(f"[RESP] {resp.status_code} {resp.reason} -> {dest[0]} ({cseq})")
    sock.sendto(resp.encode(), dest)


# ---------------------------------------------------------------- 백그라운드 작업들
def cleanup_loop():
    """
    30초마다 도는 '청소부' 스레드.
    - 만료된 등록 삭제
    - 끝났거나 응답 없이 오래된 통화 상태 삭제
    - 오래 조용한 출발지 기록 삭제
    """
    while True:
        time.sleep(CLEANUP_INTERVAL)
        now = time.time()
        expired = []
        if not INFINITE_EXPIRY:                   # '무한 유지' 모드면 만료 청소 안 함
            with state_lock:
                for ext in list(registrar):
                    if registrar[ext]["expires_at"] < now:
                        expired.append(ext)
                        del registrar[ext]
        for ext in expired:
            log.info(f"[EXP] {ext} 등록 만료 -> 삭제")
        if expired:
            log.info("[EXP] 현재 등록:\n" + fmt_registrar())
            save_registrar()

        with state_lock:
            for cid in list(calls):
                c = calls[cid]
                if c.get("ended") and now - c.get("created", now) > 32:
                    # 끝난 통화는 32초(SIP 재전송 윈도우 Timer H) 뒤 정리.
                    # 그래야 실패응답(486/603)의 ACK 가 늦게 와도 상대에게 전달돼 재전송이 멈춤.
                    del calls[cid]
                elif not c.get("answered") and now - c.get("created", now) > 60:
                    del calls[cid]                # 응답 없이 60초 지난 통화 정리
            for src in list(seen_sources):
                if now - seen_sources[src]["last"] > 3600:
                    del seen_sources[src]         # 1시간 조용한 출발지 정리


def console_loop():
    """콘솔(검은 창)에서 명령을 입력받습니다: list / calls / help"""
    help_text = ("명령:  list(l)=등록현황  calls(c)=통화현황  help(h)=도움말")
    print(help_text)
    while True:
        try:
            line = sys.stdin.readline()
        except Exception:
            return
        if line == "":              # 입력 끝(백그라운드 실행 등) -> 콘솔 기능 종료
            return
        cmd = line.strip().lower()
        if cmd in ("list", "l", "reg"):
            print("=== 등록 현황 ===\n" + fmt_registrar())
        elif cmd in ("calls", "c"):
            print("=== 통화 현황 ===\n" + fmt_calls())
        elif cmd in ("help", "h", "?"):
            print(help_text)
        elif cmd == "":
            pass
        else:
            print(f"알 수 없는 명령: {cmd}  ({help_text})")


# ---------------------------------------------------------------- 조회용 HTTP API (포트 8088)
class ApiHandler(BaseHTTPRequestHandler):
    """
    앱이 '내 번호 자동배정/중복확인' 에 쓰는 작은 JSON API.

      GET  /api/extensions      -> {"registered": ["101","102",...]}  (현재 등록된 번호들)
      GET  /api/next?start=101&device=<기기ID>
                                -> {"next": "104"}   (오름차순 빈 번호; 같은 기기는 기존 번호 재사용)
      POST /api/token  body={"ext":"103","token":"..."}
                                -> 내선<->FCM토큰 매핑 저장
    """
    def _send_json(self, obj, code=200):
        """파이썬 dict 를 JSON 으로 만들어 HTTP 응답으로 보냅니다."""
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        """GET 요청 처리 (/api/extensions, /api/next)."""
        parsed = urlparse(self.path)
        path = parsed.path
        now = time.time()
        client_ip = self.client_address[0]       # 요청 보낸 기기의 IP

        if path == "/api/extensions":
            # 현재 '살아있는' 등록 번호만 목록으로
            with state_lock:
                live = sorted(e for e, r in registrar.items() if r["expires_at"] > now)
            self._send_json({"registered": live})

        elif path == "/api/next":
            q = parse_qs(parsed.query)            # ?start=101&device=xxx 파싱
            try:
                start = int(q.get("start", [str(AUTO_START)])[0])
            except ValueError:
                start = AUTO_START
            device = q.get("device", [""])[0]

            need_save = False
            with state_lock:
                # '살아있는' 등록만 사용중으로 봄 -> 죽은(만료) 등록이 번호를 막지 않음
                live_taken = {e for e, r in registrar.items() if r["expires_at"] > now}
                result = None
                # 같은 기기(=device_assignments 에 있음)는 '항상' 이전 번호를 재사용.
                # (HTTP 요청 IP 와 SIP 등록 IP 가 달라도 번호가 밀리지 않게 — 셀룰러/NAT 대비)
                if device and device in device_assignments:
                    result = device_assignments[device]
                if result is None:
                    # 빈 번호를 start 부터 오름차순으로 탐색
                    n = start
                    while str(n) in live_taken:
                        n += 1
                    result = str(n)
                if device and device_assignments.get(device) != result:
                    device_assignments[device] = result   # 기기-번호 배정 기록
                    need_save = True
            if need_save:
                save_devices()
            self._send_json({"next": result, "registered": sorted(live_taken)})

        else:
            self._send_json({"error": "not found"}, code=404)

    def do_POST(self):
        """POST 요청 처리 (/api/token: 앱이 자기 FCM 토큰을 등록)."""
        parsed = urlparse(self.path)
        if parsed.path == "/api/token":
            length = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(length) if length else b""
            try:
                data = json.loads(raw.decode("utf-8"))
                ext = str(data.get("ext", "")).strip()
                token = str(data.get("token", "")).strip()
            except Exception:
                ext, token = "", ""
            if ext and token:
                with state_lock:
                    changed = fcm_tokens.get(ext) != token   # 토큰이 바뀌었을 때만 저장
                    fcm_tokens[ext] = token
                if changed:
                    save_tokens()
                    log.info(f"[FCM] 토큰 등록: 내선 {ext} <- {token[:24]}…({len(token)}자)")
                self._send_json({"ok": True, "ext": ext})
            else:
                self._send_json({"ok": False, "error": "ext/token required"}, code=400)
        else:
            self._send_json({"error": "not found"}, code=404)

    def log_message(self, *args):
        pass     # 기본 HTTP 접근로그를 끔(콘솔이 지저분해지지 않게)


def start_http_api():
    """조회용 HTTP API 서버를 8088 포트에서 시작합니다(별도 스레드에서 호출)."""
    try:
        srv = HTTPServer(("0.0.0.0", HTTP_PORT), ApiHandler)
        log.info(f"[*] 조회 API on http://0.0.0.0:{HTTP_PORT}  (/api/extensions, /api/next)")
        srv.serve_forever()
    except Exception as e:
        log.info(f"[!] 조회 API 시작 실패: {e}")


# ---------------------------------------------------------------- 메인 수신 루프
def serve_forever(sock):
    """
    SIP 패킷을 끝없이 받아 처리하는 '심장' 루프.
    받은 메시지가 응답이면 forward_response, REGISTER 면 handle_register,
    통화 요청이면 forward_request 로 넘깁니다.
    (블로킹 함수라 GUI 는 이걸 별도 스레드에서 돌립니다.)
    """
    while True:
        # UDP 패킷 1개 수신. data=내용(bytes), addr=보낸 곳 (ip, port)
        data, addr = sock.recvfrom(65535)

        if not data.strip():
            # 내용이 빈 줄(CRLF)뿐이면 NAT 유지용 keep-alive '핑'입니다.
            # 표준대로 CRLF 한 줄로 조용히 '퐁' 응답하고 넘어갑니다.
            try:
                sock.sendto(b"\r\n", addr)
            except Exception:
                pass
            continue

        try:
            msg = sipmsg.parse(data)          # bytes -> SipMessage 로 해석
        except Exception as e:
            log.info(f"[!] parse error from {addr}: {e}")
            continue

        _record_source(addr, msg)             # "이 출발지가 통신했다" 기록

        if msg.is_response:
            forward_response(sock, msg)       # 응답 -> 역방향 중계
        elif msg.method == "REGISTER":
            handle_register(sock, msg, addr)  # 등록 처리
        elif msg.method in ("INVITE", "ACK", "BYE", "CANCEL", "REFER", "NOTIFY", "SUBSCRIBE"):
            # REFER/NOTIFY = '통화 넘겨주기(전환)'에 필요. 통화 도중 요청이므로 반대편으로 중계.
            forward_request(sock, msg, addr)  # 통화 요청 중계
        else:
            # 그 외 메서드(OPTIONS 등)는 일단 200 OK 로 응답
            log.info(f"[?] 미처리 {msg.method}, 200 OK 응답")
            sock.sendto(make_response(msg, 200, "OK").encode(), addr)


def run_server(with_console=False):
    """
    서버 전체를 시작합니다: 소켓 열기 -> 저장된 데이터 복구 -> 백그라운드 스레드들 -> 수신 루프.
    GUI 는 이 함수를 '백그라운드 스레드'에서 호출하고, 콘솔 실행(main)은 직접 호출합니다.
    """
    setup_logging()
    # UDP 소켓 생성
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    # 재시작 시 '주소 이미 사용중' 오류를 줄이는 옵션
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((HOST, PORT))               # 5060 포트 점유
    log.info(f"[*] PBX listening on UDP {HOST}:{PORT}  MY_IP={MY_IP}  "
             f"(max_expires={MAX_EXPIRES}s, log={LOG_FILE})")

    # 디스크에 저장돼 있던 등록부/기기배정/토큰 복구
    load_registrar()
    load_devices()
    load_tokens()

    # 청소부 스레드 + HTTP API 스레드 시작 (daemon=True: 메인 끝나면 같이 종료)
    threading.Thread(target=cleanup_loop, daemon=True).start()
    threading.Thread(target=start_http_api, daemon=True).start()
    if with_console:
        threading.Thread(target=console_loop, daemon=True).start()

    serve_forever(sock)                   # 여기서부터 무한 수신(이 줄에서 멈춰 계속 동작)


def main():
    """콘솔(검은 창)에서 직접 실행할 때의 진입점. 오류 나면 창이 바로 닫히지 않게 처리."""
    try:
        run_server(with_console=True)
    except OSError as e:
        # 보통 5060 포트가 이미 사용 중일 때
        print(f"\n[!] 서버 시작 실패: {e}")
        if "10048" in str(e) or "in use" in str(e).lower():
            print("    -> UDP 5060 포트가 이미 사용 중입니다.")
            print("       miniSIPServer 등 다른 SIP 서버나 PBX 인스턴스를 먼저 종료하세요.")
        try:
            input("\n엔터를 누르면 창을 닫습니다...")
        except EOFError:
            pass
    except KeyboardInterrupt:
        print("\n종료합니다.")            # Ctrl+C
    except Exception as e:
        import traceback
        print(f"\n[!] 예기치 못한 오류: {e}")
        traceback.print_exc()
        try:
            input("\n엔터를 누르면 창을 닫습니다...")
        except EOFError:
            pass


# 이 파일을 'python step3_proxy.py' 로 직접 실행했을 때만 main() 이 돌아갑니다.
# (gui_pbx.py 가 import 할 때는 main() 이 자동 실행되지 않음)
if __name__ == "__main__":
    main()
