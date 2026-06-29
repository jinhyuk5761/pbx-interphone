"""
sipmsg.py — SIP 메시지를 "읽고(parse) / 만드는(encode)" 공용 모듈
===================================================================

[SIP 이 뭔가요?]
SIP(Session Initiation Protocol)는 인터넷 전화(VoIP)에서 "전화를 걸고/받고/끊는"
신호를 주고받는 규칙입니다. 실제 목소리(RTP)는 따로 흐르고, SIP 는 그 "신호"만 담당해요.

[중요: SIP 는 HTTP 처럼 '사람이 읽을 수 있는 텍스트' 입니다]
한 개의 SIP 메시지는 아래 모양의 '글자 덩어리' 입니다:

    INVITE sip:102@192.168.0.112 SIP/2.0   <- 첫 줄(start-line): 무슨 요청인지
    Via: SIP/2.0/UDP 192.168.0.50...        <- 헤더들 (이름: 값)
    From: <sip:101@...>
    To: <sip:102@...>
    Call-ID: abc123
    CSeq: 1 INVITE
    (빈 줄)                                   <- 헤더 끝을 알리는 빈 줄
    v=0 ...                                   <- 바디(body, 보통 SDP=음성 협상 정보)

- 줄 바꿈은 항상 '\r\n' (캐리지리턴+라인피드) 두 글자입니다.
- 헤더와 바디는 '빈 줄'(\r\n\r\n) 로 구분합니다.

이 모듈은 그 '글자 덩어리'를 다루기 쉬운 파이썬 객체(SipMessage)로 바꾸고,
반대로 객체를 다시 '글자 덩어리(bytes)'로 만들어 네트워크로 보낼 수 있게 합니다.
"""

# 줄바꿈 문자. SIP 규격상 반드시 \r\n 두 글자를 써야 합니다.
CRLF = "\r\n"


class SipMessage:
    """
    SIP 메시지 하나를 표현하는 객체.
    '요청'(전화기→서버, 예: INVITE)일 수도, '응답'(서버→전화기, 예: 200 OK)일 수도 있어서
    is_response 값으로 구분합니다.
    """

    def __init__(self):
        self.is_response = False     # True = 응답(200 OK 등), False = 요청(INVITE 등)

        # --- 요청일 때 채워지는 값 ---
        self.method = None           # 요청 종류: REGISTER / INVITE / ACK / BYE / CANCEL ...
        self.uri = None              # 요청 대상 주소(Request-URI). 예: sip:102@192.168.0.112

        # --- 응답일 때 채워지는 값 ---
        self.status_code = None      # 응답 코드(숫자): 100, 180, 200, 404 ...
        self.reason = None           # 응답 설명: OK, Trying, Ringing, Not Found ...

        self.version = "SIP/2.0"     # 프로토콜 버전 (거의 항상 SIP/2.0)

        # --- 요청/응답 공통 ---
        # 헤더를 (이름, 값) 튜플의 '리스트'로 보관합니다.
        # dict(딕셔너리)가 아니라 리스트인 이유: Via 처럼 같은 이름의 헤더가
        # 여러 개 올 수 있고, '순서'도 중요하기 때문입니다.
        self.headers = []
        self.body = ""               # 바디(보통 SDP). 없을 수도 있음.

    # ---------------- 헤더를 다루는 편의 함수들 ----------------

    def get(self, name):
        """
        주어진 이름의 헤더 '값'을 돌려줍니다 (대소문자 구분 안 함).
        같은 이름이 여러 개면 '마지막' 것을 돌려줍니다. 없으면 None.
        예: msg.get("Call-ID")  ->  "abc123"
        """
        low = name.lower()
        result = None
        for n, v in self.headers:
            if n.lower() == low:
                result = v
        return result

    def get_all(self, name):
        """
        같은 이름의 헤더 값을 '전부' 리스트로 돌려줍니다.
        Via 처럼 여러 개가 쌓이는 헤더에 사용합니다.
        예: msg.get_all("Via")  ->  ["SIP/2.0/UDP ...", "SIP/2.0/UDP ..."]
        """
        low = name.lower()
        return [v for n, v in self.headers if n.lower() == low]

    def set(self, name, value):
        """
        같은 이름의 기존 헤더를 '모두 지우고' 새 값 하나로 설정합니다.
        예: msg.set("Content-Length", "0")
        """
        low = name.lower()
        # 같은 이름이 아닌 헤더만 남기고(=같은 이름 제거),
        self.headers = [(n, v) for n, v in self.headers if n.lower() != low]
        # 새 값을 맨 뒤에 추가합니다.
        self.headers.append((name, value))

    def encode(self):
        """
        SipMessage 객체 -> 실제로 네트워크로 보낼 수 있는 bytes 로 변환.
        (위 [중요] 설명의 '글자 덩어리' 모양으로 다시 조립)
        """
        # 1) 첫 줄(start-line) 만들기
        if self.is_response:
            start = f"{self.version} {self.status_code} {self.reason}"   # 예: SIP/2.0 200 OK
        else:
            start = f"{self.method} {self.uri} {self.version}"           # 예: INVITE sip:.. SIP/2.0

        # 2) 첫 줄 + 헤더들을 줄 단위로 모으기
        lines = [start]
        for n, v in self.headers:
            lines.append(f"{n}: {v}")                                    # 예: From: <sip:...>

        # 3) 줄들을 \r\n 으로 잇고, 헤더 끝에 빈 줄(\r\n\r\n) 추가
        head = CRLF.join(lines) + CRLF + CRLF
        # 4) 헤더 + 바디를 합쳐 UTF-8 bytes 로 변환 (socket 은 bytes 만 보낼 수 있음)
        return (head + self.body).encode("utf-8")


def parse(data: bytes) -> SipMessage:
    """
    네트워크에서 받은 bytes -> SipMessage 객체로 변환 (encode 의 반대).
    """
    # 받은 bytes 를 글자(str)로 변환. 깨진 글자가 있어도 죽지 않도록 errors="replace".
    text = data.decode("utf-8", errors="replace")

    # '빈 줄(\r\n\r\n)' 을 기준으로 [헤더부분] 과 [바디부분] 을 나눕니다.
    # partition 은 (앞부분, 구분자, 뒷부분) 3개를 돌려줍니다.
    head, _, body = text.partition(CRLF + CRLF)

    lines = head.split(CRLF)   # 헤더부분을 줄 단위로 쪼갬
    start = lines[0]           # 첫 줄(start-line)

    msg = SipMessage()
    msg.body = body

    # 첫 줄이 "SIP/2.0" 으로 시작하면 '응답', 아니면 '요청' 입니다.
    if start.startswith("SIP/2.0"):
        # 응답 예: "SIP/2.0 200 OK"  ->  ["SIP/2.0", "200", "OK"]
        msg.is_response = True
        parts = start.split(" ", 2)          # 최대 3조각으로만 나눔(reason 에 공백 있을 수 있어서)
        msg.version = parts[0]
        msg.status_code = int(parts[1])      # "200" -> 숫자 200
        msg.reason = parts[2] if len(parts) > 2 else ""
    else:
        # 요청 예: "INVITE sip:102@... SIP/2.0"  ->  ["INVITE", "sip:102@...", "SIP/2.0"]
        msg.is_response = False
        parts = start.split(" ", 2)
        msg.method = parts[0]
        msg.uri = parts[1] if len(parts) > 1 else ""
        msg.version = parts[2] if len(parts) > 2 else "SIP/2.0"

    # 둘째 줄부터는 헤더들. 한 줄씩 (이름, 값) 으로 분리해 저장합니다.
    for line in lines[1:]:
        if not line:
            continue                          # 빈 줄은 건너뜀
        if line[0] in " \t":
            # 줄 맨 앞이 공백/탭이면 '앞 헤더의 연속(folding)' 입니다.
            # (긴 헤더를 여러 줄로 나눠 쓰는 옛 방식) -> 앞 헤더 값에 이어 붙입니다.
            if msg.headers:
                n, v = msg.headers[-1]
                msg.headers[-1] = (n, v + " " + line.strip())
            continue
        # "이름: 값" 을 첫 번째 ':' 기준으로 나눕니다.
        name, _, value = line.partition(":")
        msg.headers.append((name.strip(), value.strip()))   # 앞뒤 공백 제거 후 저장

    return msg


def aor_user(header_value: str) -> str:
    """
    From/To 같은 헤더 값에서 '내선번호(user 부분)' 만 뽑아냅니다.

    예) 입력: '"홍길동" <sip:101@192.168.0.112:5060>;tag=abc'
        출력: '101'

    SIP 주소(URI)는 'sip:사용자@호스트:포트;파라미터' 모양이라,
    여기서 '사용자(=내선번호)' 부분만 골라냅니다.
    """
    if not header_value:
        return None
    s = header_value

    # 1) <...> 안에 진짜 주소가 들어있으면 그 안쪽만 꺼냅니다.
    #    '"홍길동" <sip:101@...>;tag=abc'  ->  'sip:101@192.168.0.112:5060'
    if "<" in s and ">" in s:
        s = s[s.index("<") + 1: s.index(">")]

    # 2) 'sip:' 접두사 제거.  'sip:101@host...'  ->  '101@host...'
    if ":" in s:
        s = s.split(":", 1)[1]

    # 3) '@' 앞부분이 사용자.  '101@host...'  ->  '101'
    if "@" in s:
        s = s.split("@", 1)[0]

    # 4) 혹시 남아있을 ';파라미터' 제거
    s = s.split(";")[0]
    return s
