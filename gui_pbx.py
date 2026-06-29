"""
gui_pbx.py — 미니 PBX 관리 콘솔 GUI (PySide6)
===============================================

[이게 뭔가요?]
step3_proxy.py(SIP 서버)를 '창(GUI)' 으로 감싼 관리 프로그램입니다.
miniSIPServer 처럼 등록 단말/통화/로그를 표와 패널로 실시간 보여줍니다.

[화면 구성]
  - 상단 상태줄: 서버 상태 / 내 IP / 포트 / 등록·통화 개수
  - 컨트롤줄: [만료시간 무한] 체크박스 / 이 PC 의 5060 점유 프로세스(충돌 감지)
  - 왼쪽 표: 등록 단말 (+ '선택 단말 삭제' 버튼)
  - 오른쪽 표: 현재 통화
  - 아래 탭: 실시간 로그 / 5060 트래픽 기기

[핵심: 스레드 분리]
  - SIP 서버는 네트워크를 계속 기다리는(블로킹) 코드라, '백그라운드 스레드'에서 돌립니다.
  - 화면(GUI)은 반드시 '메인 스레드'에서만 만져야 합니다(Qt 규칙).
  - 그래서 GUI 는 QTimer 로 1~3초마다 서버의 '스냅샷(복사본)'을 읽어 표를 갱신합니다.
    (서버의 내부 데이터를 직접 만지지 않음 -> 충돌/크래시 방지)
"""
import sys
import os                  # 번들된 아이콘 파일 경로 찾기
import subprocess          # netstat/tasklist 실행 (5060 점유 프로세스 확인)
import threading           # SIP 서버를 백그라운드로 돌리기
import logging             # 서버 로그를 GUI 패널로 받아오기
from collections import deque   # 로그를 잠깐 담아두는 thread-safe 큐

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QFont, QColor, QIcon
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView, QGroupBox, QSplitter,
    QPlainTextEdit, QPushButton, QCheckBox, QTabWidget, QMessageBox,
)

import step3_proxy as pbx   # 우리 SIP 서버를 'pbx' 라는 이름으로 가져와 사용

# 자동 갱신 주기(밀리초)
REFRESH_MS = 1500          # 표/상태 갱신
LOG_DRAIN_MS = 400         # 로그 패널 갱신
PORT_CHECK_MS = 3000       # 5060 점유 프로세스 확인 (netstat 는 무거우니 덜 자주)

CREATE_NO_WINDOW = 0x08000000   # netstat/tasklist 실행 시 검은 콘솔창이 깜빡이지 않게 하는 플래그


def local_port_holders(port: int = 5060):
    """
    이 PC 에서 해당 UDP 포트를 점유한 프로세스 목록 [(pid, 이름), ...] 을 반환.
    PBX 가 2개 떠서 충돌(둘 다 5060 점유)하는 상황을 화면에 보여주려고 사용합니다.

    방법: 'netstat -ano' 로 5060 을 쓰는 PID 들을 찾고, 'tasklist' 로 PID->이름 매핑.
    """
    try:
        out = subprocess.run(
            ["netstat", "-ano", "-p", "UDP"],
            capture_output=True, text=True, timeout=5,
            creationflags=CREATE_NO_WINDOW,
        ).stdout
    except Exception:
        return []
    pids = []
    for line in out.splitlines():
        parts = line.split()
        # 예: "UDP  0.0.0.0:5060  *:*  13836"  -> parts[1] 이 :5060 으로 끝나면 우리 포트
        if len(parts) >= 4 and parts[0] == "UDP" and parts[1].endswith(f":{port}"):
            pid = parts[-1]
            if pid.isdigit():
                pids.append(pid)
    pids = list(dict.fromkeys(pids))   # 중복 제거(순서 유지)
    if not pids:
        return []
    # PID -> 프로세스 이름 매핑 (tasklist CSV 파싱)
    names = {}
    try:
        tl = subprocess.run(
            ["tasklist", "/NH", "/FO", "CSV"],
            capture_output=True, text=True, timeout=5,
            creationflags=CREATE_NO_WINDOW,
        ).stdout
        for row in tl.splitlines():
            # CSV 한 줄: "이름","PID","세션",...  -> '","' 로 쪼개고 따옴표 제거
            cols = [c.strip().strip('"') for c in row.split('","')]
            if len(cols) >= 2:
                names[cols[1]] = cols[0]
    except Exception:
        pass
    return [(pid, names.get(pid, "?")) for pid in pids]


class QtLogHandler(logging.Handler):
    """
    파이썬 logging 의 '핸들러'. 서버가 남기는 로그(log.info(...))를
    화면에 보여주려고, 로그 줄을 deque(임시 큐)에 차곡차곡 담습니다.
    (GUI 가 주기적으로 이 큐를 비우며 로그 패널에 출력)
    """
    def __init__(self, buffer):
        super().__init__()
        self.buffer = buffer

    def emit(self, record):
        # emit 은 로그가 발생할 때마다(=다른 스레드에서) 호출됨.
        # deque.append 는 thread-safe 라 여기서 바로 담아도 안전.
        try:
            self.buffer.append(self.format(record))
        except Exception:
            pass


def fmt_duration(sec):
    """초(sec) -> "분:초" 형식 문자열. 예: 75 -> "01:15"."""
    return f"{sec // 60:02d}:{sec % 60:02d}"


class PbxWindow(QMainWindow):
    """프로그램의 메인 창. 위젯들을 만들고, 서버를 띄우고, 주기적으로 화면을 갱신합니다."""

    def __init__(self):
        super().__init__()
        self.server_error = None     # 서버 시작 실패 시 에러 메시지 저장(상태줄에 표시)

        # --- 로그 연결: 서버 로그를 GUI 패널로 받아오기 ---
        pbx.setup_logging()                              # 파일/콘솔 로그 설정
        self.log_buffer = deque(maxlen=4000)            # 최근 4000줄만 보관
        gui_handler = QtLogHandler(self.log_buffer)
        gui_handler.setFormatter(
            logging.Formatter("%(asctime)s %(message)s", datefmt="%H:%M:%S"))
        logging.getLogger("pbx").addHandler(gui_handler)  # 'pbx' 로거에 우리 핸들러 추가

        self.setWindowTitle("Mini PBX (Python) — 관리 콘솔")
        self.resize(940, 700)

        self._build_menu()       # 상단 메뉴
        self._build_central()    # 본문(상태줄/표/탭)

        # --- SIP 서버를 백그라운드 스레드로 시작 ---
        # daemon=True: 창을 닫으면 이 스레드도 같이 종료됨
        self.server_thread = threading.Thread(
            target=self._run_server, daemon=True, name="sip-server")
        self.server_thread.start()

        # --- 자동 갱신 타이머들 (모두 메인 스레드에서 실행 -> 화면 갱신 안전) ---
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)         # 표/상태 갱신
        self.timer.start(REFRESH_MS)

        self.log_timer = QTimer(self)
        self.log_timer.timeout.connect(self.drain_logs)  # 로그 패널 갱신
        self.log_timer.start(LOG_DRAIN_MS)

        self.port_timer = QTimer(self)
        self.port_timer.timeout.connect(self.refresh_port_holders)  # 5060 점유 확인
        self.port_timer.start(PORT_CHECK_MS)

        self.refresh()                # 시작하자마자 1번 갱신
        self.refresh_port_holders()

    # ----- 서버 스레드 -----
    def _run_server(self):
        """백그라운드에서 SIP 서버 실행. 실패(예: 5060 점유)하면 에러를 저장."""
        try:
            pbx.run_server(with_console=False)
        except Exception as e:
            self.server_error = str(e)

    # ----- UI(화면) 구성 -----
    def _build_menu(self):
        """상단 '파일 > 종료' 메뉴."""
        menu = self.menuBar()
        file_menu = menu.addMenu("파일(&F)")
        quit_act = QAction("종료(&X)", self)
        quit_act.triggered.connect(self.close)
        file_menu.addAction(quit_act)

    def _build_central(self):
        """창 본문 전체 레이아웃 구성."""
        central = QWidget()
        outer = QVBoxLayout(central)   # 세로로 쌓는 레이아웃

        # (1) 상태줄: 서버상태/IP/포트/개수 (RichText 라 색·굵게 가능)
        self.status_label = QLabel()
        self.status_label.setTextFormat(Qt.RichText)
        self.status_label.setStyleSheet("padding:6px; font-size:13px;")
        outer.addWidget(self.status_label)

        # (2) 컨트롤줄: [만료무한] 체크박스 (왼쪽) + 5060 점유 표시 (오른쪽)
        ctrl = QHBoxLayout()
        self.infinite_check = QCheckBox("만료시간 무한 (등록 안 지움)")
        self.infinite_check.toggled.connect(self.on_infinite_toggled)
        ctrl.addWidget(self.infinite_check)
        ctrl.addStretch(1)            # 가운데 빈 공간(오른쪽으로 밀기)
        self.port_label = QLabel("5060 점유: 확인 중...")
        self.port_label.setTextFormat(Qt.RichText)
        ctrl.addWidget(self.port_label)
        outer.addLayout(ctrl)

        # (3) 위/아래로 크기 조절 가능한 분할(Splitter)
        vsplit = QSplitter(Qt.Vertical)

        # 위쪽: [등록 단말 표] | [현재 통화 표] 좌우 분할
        hsplit = QSplitter(Qt.Horizontal)
        hsplit.addWidget(self._build_reg_group())
        hsplit.addWidget(self._build_calls_group())
        hsplit.setSizes([500, 420])
        vsplit.addWidget(hsplit)

        # 아래쪽: 탭 (실시간 로그 / 5060 트래픽 기기)
        tabs = QTabWidget()
        tabs.addTab(self._build_log_tab(), "실시간 로그")
        tabs.addTab(self._build_sources_tab(), "5060 트래픽 기기")
        vsplit.addWidget(tabs)
        vsplit.setSizes([340, 300])

        outer.addWidget(vsplit)
        self.setCentralWidget(central)

    def _make_table(self, headers, stretch_col):
        """표(QTableWidget) 하나를 공통 설정으로 만들어 돌려주는 도우미."""
        t = QTableWidget(0, len(headers))
        t.setHorizontalHeaderLabels(headers)
        t.verticalHeader().setVisible(False)              # 왼쪽 행번호 숨김
        t.setEditTriggers(QTableWidget.NoEditTriggers)    # 셀 수정 불가(보기 전용)
        t.setSelectionBehavior(QTableWidget.SelectRows)   # 클릭하면 '행' 단위 선택
        t.setAlternatingRowColors(True)                   # 줄무늬 배경
        hdr = t.horizontalHeader()
        for i in range(len(headers)):
            # stretch_col 열만 남는 공간을 채우고, 나머지는 내용에 맞게
            mode = QHeaderView.Stretch if i == stretch_col else QHeaderView.ResizeToContents
            hdr.setSectionResizeMode(i, mode)
        return t

    def _build_reg_group(self):
        """'등록 단말' 표 + '선택 단말 삭제' 버튼."""
        group = QGroupBox("등록 단말 (Local users)")
        gl = QVBoxLayout(group)
        self.reg_table = self._make_table(
            ["내선번호", "IP주소:포트", "단말명 (User-Agent)", "만료까지"], stretch_col=2)
        gl.addWidget(self.reg_table)
        bar = QHBoxLayout()
        bar.addStretch(1)
        del_btn = QPushButton("선택 단말 삭제")
        del_btn.clicked.connect(self.delete_selected_registration)
        bar.addWidget(del_btn)
        gl.addLayout(bar)
        return group

    def delete_selected_registration(self):
        """표에서 선택한 내선의 등록을 강제 삭제(확인창 후)."""
        row = self.reg_table.currentRow()
        if row < 0 or self.reg_table.item(row, 0) is None:
            QMessageBox.information(self, "단말 삭제", "삭제할 단말을 표에서 먼저 선택하세요.")
            return
        ext = self.reg_table.item(row, 0).text()    # 0번 열 = 내선번호
        reply = QMessageBox.question(
            self, "단말 삭제",
            f"내선 {ext} 의 등록을 삭제할까요?\n"
            f"(단말이 켜져 있으면 다음 재등록 때 다시 나타납니다.)",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            pbx.unregister_ext(ext)     # 서버 등록부에서 제거
            self.refresh()

    def _build_calls_group(self):
        """'현재 통화' 표."""
        group = QGroupBox("현재 통화 (Active calls)")
        gl = QVBoxLayout(group)
        self.calls_table = self._make_table(
            ["발신", "수신", "상태", "경과시간"], stretch_col=2)
        gl.addWidget(self.calls_table)
        return group

    def _build_log_tab(self):
        """'실시간 로그' 탭 (다크 테마 텍스트 영역 + 로그 지우기 버튼)."""
        w = QWidget()
        gl = QVBoxLayout(w)
        bar = QHBoxLayout()
        bar.addStretch(1)
        clear_btn = QPushButton("로그 지우기")
        clear_btn.clicked.connect(lambda: self.log_view.clear())
        bar.addWidget(clear_btn)
        gl.addLayout(bar)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(5000)          # 너무 길어지면 오래된 줄 자동 삭제
        self.log_view.setFont(QFont("Consolas", 9))
        self.log_view.setStyleSheet("background:#1e1e1e; color:#d4d4d4; border:none;")
        gl.addWidget(self.log_view)
        return w

    def _build_sources_tab(self):
        """'5060 트래픽 기기' 탭 (등록 안 된 기기까지 포함, 우리 5060 에 패킷 보낸 모든 출발지)."""
        w = QWidget()
        gl = QVBoxLayout(w)
        hint = QLabel("이 PBX(5060)로 패킷을 보낸 모든 네트워크 기기 (등록 안 된 기기도 표시)")
        hint.setStyleSheet("color:#888; padding:2px;")
        gl.addWidget(hint)
        self.sources_table = self._make_table(
            ["IP주소:포트", "최근 메시지", "패킷수", "마지막활동", "등록내선"], stretch_col=1)
        gl.addWidget(self.sources_table)
        return w

    # ----- [만료시간 무한] 체크박스 -----
    def on_infinite_toggled(self, checked):
        """체크하면 서버의 INFINITE_EXPIRY 를 켜서 등록을 만료로 안 지우게 함."""
        pbx.INFINITE_EXPIRY = checked

    # ----- 주기 갱신 (QTimer 가 1.5초마다 호출) -----
    def refresh(self):
        """서버에서 스냅샷(복사본)을 읽어 상태줄/표 3개를 갱신."""
        regs = pbx.snapshot_registrar()
        calls = pbx.snapshot_calls()
        sources = pbx.snapshot_sources()
        self._refresh_status(len(regs), len(calls))
        self._refresh_registrar(regs)
        self._refresh_calls(calls)
        self._refresh_sources(sources)

    def _refresh_status(self, nreg, ncall):
        """상태줄 텍스트 갱신 (서버 실패면 빨간 경고)."""
        if self.server_error:
            msg = self.server_error
            if "10048" in msg or "in use" in msg.lower():
                msg += "  (UDP 5060 사용 중 — 다른 PBX/miniSIPServer 종료)"
            self.status_label.setText(
                f"<b style='color:#c0392b'>● 서버 시작 실패</b> &nbsp; {msg}")
            return
        running = self.server_thread.is_alive()
        dot = "#27ae60" if running else "#c0392b"      # 초록=실행중, 빨강=중지
        state = "실행 중" if running else "중지됨"
        self.status_label.setText(
            f"<b style='color:{dot}'>●</b> 서버 {state} &nbsp;|&nbsp; "
            f"내 IP <b>{pbx.MY_IP}</b> &nbsp;|&nbsp; 포트 <b>{pbx.PORT}</b> (UDP) "
            f"&nbsp;|&nbsp; 등록 <b>{nreg}</b> · 통화 <b>{ncall}</b>")

    def _refresh_registrar(self, rows):
        """등록 단말 표 갱신. (만료무한이면 ∞, 60초 미만이면 빨간색)"""
        infinite = pbx.INFINITE_EXPIRY
        self.reg_table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            left = r["expires_in"]
            disp = "∞" if infinite else f"{left}s"
            cells = [r["ext"], f"{r['ip']}:{r['port']}", r["user_agent"] or "-", disp]
            for j, text in enumerate(cells):
                item = QTableWidgetItem(str(text))
                if j in (0, 3):
                    item.setTextAlignment(Qt.AlignCenter)
                if j == 3 and not infinite and left < 60:    # 곧 만료 -> 빨간 글씨
                    item.setForeground(QColor("#c0392b"))
                self.reg_table.setItem(i, j, item)

    def _refresh_calls(self, rows):
        """현재 통화 표 갱신. (통화중=초록, 연결중=주황)"""
        self.calls_table.setRowCount(len(rows))
        for i, c in enumerate(rows):
            answered = c["answered"]
            state = "통화중" if answered else "연결중"
            cells = [c["caller_ext"], c["callee_ext"], state, fmt_duration(c["duration"])]
            for j, text in enumerate(cells):
                item = QTableWidgetItem(str(text))
                item.setTextAlignment(Qt.AlignCenter)
                if j == 2:
                    item.setForeground(QColor("#27ae60") if answered else QColor("#e67e22"))
                self.calls_table.setItem(i, j, item)

    def _refresh_sources(self, rows):
        """5060 트래픽 기기 표 갱신. (등록된 기기는 내선번호를 초록색으로)"""
        self.sources_table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            idle = r["idle"]
            last = "방금" if idle < 2 else f"{idle}s 전"
            cells = [f"{r['ip']}:{r['port']}", r["last_method"],
                     str(r["count"]), last, r["ext"] or "-"]
            for j, text in enumerate(cells):
                item = QTableWidgetItem(str(text))
                if j != 1:
                    item.setTextAlignment(Qt.AlignCenter)
                if j == 4 and r["ext"]:
                    item.setForeground(QColor("#27ae60"))
                self.sources_table.setItem(i, j, item)

    def refresh_port_holders(self):
        """이 PC 의 5060 점유 프로세스를 확인해 상태 표시 (2개 이상이면 충돌 경고)."""
        holders = local_port_holders(pbx.PORT)
        if not holders:
            self.port_label.setText("<span style='color:#888'>5060 점유: 없음</span>")
        elif len(holders) == 1:
            pid, name = holders[0]
            self.port_label.setText(
                f"<span style='color:#27ae60'>5060 점유: {name} ({pid})</span>")
        else:
            txt = ", ".join(f"{n}({p})" for p, n in holders)
            self.port_label.setText(
                f"<span style='color:#c0392b'>⚠ 5060 충돌! {len(holders)}개 — {txt}</span>")

    def drain_logs(self):
        """로그 큐(deque)에 쌓인 줄들을 로그 패널로 옮기고, 맨 아래로 스크롤."""
        appended = False
        while self.log_buffer:
            try:
                line = self.log_buffer.popleft()
            except IndexError:
                break
            self.log_view.appendPlainText(line)
            appended = True
        if appended:
            sb = self.log_view.verticalScrollBar()
            sb.setValue(sb.maximum())          # 항상 최신 줄이 보이게


def resource_path(name):
    """번들된 리소스(아이콘 등) 경로. exe(onefile)면 _MEIPASS, 아니면 스크립트 폴더."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def main():
    """프로그램 시작점: Qt 앱 생성 -> 창 표시 -> 이벤트 루프 실행."""
    app = QApplication(sys.argv)
    # 작업표시줄/제목표시줄 창 아이콘 (exe 파일 아이콘과 별개라 따로 지정해야 함)
    icon_path = resource_path("pbx_icon.ico")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))
    win = PbxWindow()
    win.show()
    sys.exit(app.exec())     # 창이 닫힐 때까지 여기서 대기(이벤트 처리)


if __name__ == "__main__":
    main()
