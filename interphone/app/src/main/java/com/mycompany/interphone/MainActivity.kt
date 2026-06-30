package com.mycompany.interphone

import android.Manifest
import android.app.NotificationManager
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.PowerManager
import android.provider.Settings
import android.text.InputType
import android.util.Log
import android.view.View
import android.widget.EditText
import android.widget.ImageView
import android.widget.Toast
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import com.google.firebase.messaging.FirebaseMessaging
import com.mycompany.interphone.databinding.ActivityMainBinding
import org.linphone.core.Call
import org.linphone.core.RegistrationState

/**
 * MainActivity — 앱의 메인 화면.
 *
 * 하는 일:
 *  - 내 내선번호 표시 + 등록 상태 표시
 *  - 전화 걸기(상대 번호 입력 -> 통화)
 *  - 통화 중 화면(받기/끊기 버튼)
 *  - 첫 실행 시 내선번호 설정(서버에서 자동배정/중복확인)
 *  - 필요한 권한(마이크/알림) 요청 + 배터리 최적화 예외 요청
 *  - FCM 토큰 조회 후 서버에 등록
 *
 * 실제 SIP 동작은 전부 SipManager 가 합니다. 이 화면은 'SipManager 를 부르고, 상태를 표시'만.
 */
class MainActivity : AppCompatActivity() {

    // activity_main.xml 을 코드에서 쉽게 다루는 뷰바인딩
    private lateinit var binding: ActivityMainBinding

    // 통화 중 다이얼러에서 통화 버튼을 눌렀을 때 할 동작
    private enum class PendingAction { NONE, NEW_CALL, TRANSFER }
    private var pendingAction = PendingAction.NONE

    companion object {
        private const val PERM_REQ = 1001       // 권한 요청 식별 번호
    }

    // SipManager 에 등록할 리스너(상태 변화 콜백). 변수에 담아둬야 나중에 제거 가능.
    private val regL: (RegistrationState, String) -> Unit = { s, m ->
        runOnUiThread { updateRegistration(s, m) }     // 등록상태 -> 화면 갱신(메인 스레드)
    }
    private val callL: (Call.State, String) -> Unit = { s, _ ->
        runOnUiThread { updateCall(s) }                // 통화상태 -> 화면 갱신
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        requestRuntimePermissions()        // 마이크/알림 권한 요청
        requestBatteryExemption()          // 배터리 최적화 예외(백그라운드 수신 잘 되게)
        requestFullScreenIntentPermission()  // 전체화면 수신(잠금화면 위) 권한 (안드 14+)
        requestOverlayPermission()         // '다른 앱 위에 표시' — 전화 오면 즉시 전체화면 띄우기

        // 상태 변화 구독 시작
        SipManager.addRegistrationListener(regL)
        SipManager.addCallListener(callL)

        // 버튼 동작 연결
        binding.serverText.text = "${SipManager.SERVER}:5060"
        setupKeypad()                  // 숫자 키패드 입력 연결

        // 통화(발신) 버튼: 모드에 따라 새 전화 / 통화 넘겨주기 / 일반 발신
        binding.callButton.setOnClickListener {
            val target = binding.numval.text.toString().trim()
            if (target.isEmpty()) { toast("번호를 입력하세요"); return@setOnClickListener }
            when (pendingAction) {
                PendingAction.TRANSFER -> {        // 돌려주기: 현재 상대를 target 으로 넘김
                    SipManager.transfer(target)
                    toast("$target 으로 넘겨주는 중…")
                    pendingAction = PendingAction.NONE
                }
                PendingAction.NEW_CALL -> {        // 키패드: 현재 통화 끊고 새로 발신
                    SipManager.hangup()
                    pendingAction = PendingAction.NONE
                    binding.root.postDelayed({ SipManager.call(target) }, 400)
                }
                PendingAction.NONE -> SipManager.call(target)
            }
        }
        binding.changeNumberButton.setOnClickListener { openSetup(firstRun = false) }  // 번호 변경
        binding.historyButton.setOnClickListener { toast("최근 통화는 준비 중입니다") }
        binding.endButton.setOnClickListener { SipManager.hangup() }                  // 통화 종료
        setupInCallControls()          // 음소거/스피커 등 통화중 컨트롤 연결

        fetchAndSyncToken()            // FCM 토큰 가져와 서버에 등록

        // 저장된 내선번호가 있으면 그대로 등록, 없으면 첫 설정 화면을 띄움
        val saved = Prefs.getExtension(this)
        if (saved == null) {
            binding.dchipText.text = "내선 미설정"
            openSetup(firstRun = true)
        } else {
            SipManager.configure(saved)
            SipManager.register()
            SipManager.currentState()?.let { updateRegistration(it, "") }   // 현재 등록상태 즉시 반영
            // 통화 중에 화면을 다시 열었으면 통화중 UI 복원
            SipManager.currentCallState()?.let { updateCall(it) }
        }
    }

    // ---------------- 내선번호 설정 ----------------
    /** 번호 설정 시작: 서버에서 추천번호/사용중목록을 받아온 뒤 다이얼로그를 띄움. */
    private fun openSetup(firstRun: Boolean) {
        binding.dchipText.text = "서버에서 번호 조회 중…"
        // ANDROID_ID = 기기 고유 ID (재설치해도 같음) -> 같은 기기는 같은 번호 재사용에 사용
        @Suppress("HardwareIds")
        val deviceId = Settings.Secure.getString(contentResolver, Settings.Secure.ANDROID_ID) ?: ""
        Thread {                                  // 네트워크라 백그라운드 스레드
            val suggested = PbxApi.next(101, deviceId)    // 추천(자동배정) 번호
            val taken = PbxApi.registered()               // 이미 사용 중인 번호들
            runOnUiThread { showNumberDialog(suggested, taken, firstRun) }  // 결과로 창 띄움
        }.start()
    }

    /** 내선번호 입력 다이얼로그. 중복이면 거부, '자동배정' 버튼 제공. */
    private fun showNumberDialog(suggested: String?, taken: List<String>?, firstRun: Boolean) {
        val current = SipManager.EXTENSION
        val input = EditText(this).apply {
            inputType = InputType.TYPE_CLASS_NUMBER             // 숫자 키패드
            setText(if (firstRun) (suggested ?: "") else current)
            setSelection(text.length)
        }
        val msg = buildString {
            append("이 폰의 내선번호를 정하세요.\n")
            append(
                if (taken != null) "사용 중: " + taken.joinToString(", ").ifEmpty { "없음" }
                else "(서버 조회 실패 — 수동 입력)"
            )
        }
        val builder = AlertDialog.Builder(this)
            .setTitle("내 내선번호 설정")
            .setMessage(msg)
            .setView(input)
            .setPositiveButton("확인", null)                    // 동작은 아래에서 직접 연결
        if (suggested != null) builder.setNeutralButton("자동배정 ($suggested)", null)
        if (!firstRun) builder.setNegativeButton("취소", null)   // 첫 설정 땐 취소 불가

        val dialog = builder.create()
        dialog.setCancelable(!firstRun)
        // setOnShowListener: '확인' 버튼을 직접 제어해서, 중복이면 창을 안 닫고 토스트만.
        dialog.setOnShowListener {
            dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener {
                val ext = input.text.toString().trim()
                // 중복 검사 집합(번호 변경 시엔 '내 현재 번호'는 제외)
                val takenSet = (taken ?: emptyList()).toMutableSet()
                    .apply { if (!firstRun) remove(current) }
                when {
                    ext.isEmpty() -> toast("번호를 입력하세요")
                    taken != null && ext in takenSet -> toast("이미 사용 중인 번호입니다")
                    else -> { applyExtension(ext, firstRun); dialog.dismiss() }
                }
            }
            // '자동배정' 버튼: 추천 번호를 바로 적용
            if (suggested != null) {
                dialog.getButton(AlertDialog.BUTTON_NEUTRAL).setOnClickListener {
                    applyExtension(suggested, firstRun); dialog.dismiss()
                }
            }
        }
        dialog.show()
    }

    /** 정해진 내선번호를 저장하고 등록(또는 재등록), FCM 토큰도 갱신. */
    private fun applyExtension(ext: String, firstRun: Boolean) {
        Prefs.setExtension(this, ext)
        if (firstRun) {
            SipManager.configure(ext)
            SipManager.register()
        } else {
            SipManager.reconfigure(ext)     // 기존 등록 지우고 새 번호로 재등록
        }
        binding.serverText.text = "${SipManager.SERVER}:5060"
        binding.dchipText.text = "내선 $ext · 등록 중"
        toast("내선번호 설정: $ext")
        Tokens.push(this)                   // 바뀐 내선번호로 FCM 토큰 재등록
    }

    /** FCM 토큰을 비동기로 받아 저장하고 서버에 등록. */
    private fun fetchAndSyncToken() {
        FirebaseMessaging.getInstance().token.addOnCompleteListener { task ->
            if (task.isSuccessful && task.result != null) {
                Prefs.setFcmToken(this, task.result)
                Log.i(FcmService.TAG, "현재 FCM 토큰: ${task.result}")
                Tokens.push(this)           // 내선번호가 이미 있으면 서버로 전송
            } else {
                Log.w(FcmService.TAG, "FCM 토큰 조회 실패", task.exception)
            }
        }
    }

    /** 짧은 안내 메시지(토스트) 띄우는 도우미. */
    private fun toast(m: String) = Toast.makeText(this, m, Toast.LENGTH_SHORT).show()

    // ---------------- 권한 ----------------
    /** 마이크(통화 필수) + 알림(안드 13+) 권한을 런타임에 요청. */
    private fun requestRuntimePermissions() {
        val perms = mutableListOf(Manifest.permission.RECORD_AUDIO)
        if (Build.VERSION.SDK_INT >= 33) perms.add(Manifest.permission.POST_NOTIFICATIONS)
        // 안드 12+ : 블루투스 이어폰으로 통화음 보내려면 BLUETOOTH_CONNECT 허용 필요
        if (Build.VERSION.SDK_INT >= 31) perms.add(Manifest.permission.BLUETOOTH_CONNECT)
        // 아직 허용 안 된 것만 골라서
        val need = perms.filter {
            ContextCompat.checkSelfPermission(this, it) != PackageManager.PERMISSION_GRANTED
        }
        if (need.isNotEmpty()) {
            ActivityCompat.requestPermissions(this, need.toTypedArray(), PERM_REQ)
        }
    }

    /** 배터리 최적화 예외 요청 (백그라운드에서 푸시/수신이 잘 동작하도록). */
    private fun requestBatteryExemption() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.M) return
        val pm = getSystemService(PowerManager::class.java)
        if (!pm.isIgnoringBatteryOptimizations(packageName)) {
            try {
                // "이 앱을 배터리 최적화에서 제외할까요?" 시스템 설정 화면을 띄움
                startActivity(
                    Intent(
                        Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS,
                        Uri.parse("package:$packageName")
                    )
                )
            } catch (_: Exception) {
            }
        }
    }

    /**
     * 전체화면 수신 권한 요청 (Android 14+).
     * 안드 14 부터는 '전화/알람 앱'이 아니면 전체화면 인텐트가 기본 차단되어 헤드업으로만 뜸.
     * 권한이 없으면 사용자에게 설정 화면을 열어 허용을 받게 함.
     */
    private fun requestFullScreenIntentPermission() {
        if (Build.VERSION.SDK_INT >= 34) {
            val nm = getSystemService(NotificationManager::class.java)
            if (!nm.canUseFullScreenIntent()) {
                try {
                    startActivity(
                        Intent(
                            Settings.ACTION_MANAGE_APP_USE_FULL_SCREEN_INTENT,
                            Uri.parse("package:$packageName")
                        )
                    )
                } catch (_: Exception) {
                }
            }
        }
    }

    /**
     * '다른 앱 위에 표시'(SYSTEM_ALERT_WINDOW) 권한 요청.
     * 안드 10+ 부터는 백그라운드에서 앱이 화면(액티비티)을 바로 띄우는 게 막혀 있어,
     * 전화가 와도 '헤드업 알림 → 탭해야 전체화면'으로만 떴습니다.
     * 이 권한이 있으면 백그라운드에서도 수신 화면을 '즉시' 전체화면으로 띄울 수 있습니다.
     */
    private fun requestOverlayPermission() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.M) return
        if (!Settings.canDrawOverlays(this)) {
            try {
                startActivity(
                    Intent(
                        Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                        Uri.parse("package:$packageName")
                    )
                )
            } catch (_: Exception) {
            }
        }
    }

    // ---------------- 키패드 / 통화중 컨트롤 ----------------
    /** 숫자 키패드(1~9,0) 입력과 삭제 버튼을 numval 에 연결. */
    private fun setupKeypad() {
        val keys = mapOf(
            binding.key1 to "1", binding.key2 to "2", binding.key3 to "3",
            binding.key4 to "4", binding.key5 to "5", binding.key6 to "6",
            binding.key7 to "7", binding.key8 to "8", binding.key9 to "9",
            binding.key0 to "0",
        )
        keys.forEach { (view, digit) -> view.setOnClickListener { appendDigit(digit) } }
        binding.keyDel.setOnClickListener {
            val t = binding.numval.text.toString()
            if (t.isNotEmpty()) binding.numval.text = t.dropLast(1)
        }
    }

    /** 다이얼 번호에 숫자 추가 (최대 7자리). */
    private fun appendDigit(d: String) {
        val cur = binding.numval.text.toString()
        if (cur.length < 7) binding.numval.text = cur + d
    }

    /** 통화중 컨트롤 버튼 동작 연결. */
    private fun setupInCallControls() {
        // 음소거: 마이크 끄기/켜기
        binding.ctlMuteIcon.setOnClickListener {
            val on = !it.isSelected; setCtlOn(binding.ctlMuteIcon, on); SipManager.setMicMuted(on)
        }
        // 스피커: 스피커폰 전환
        binding.ctlSpeakerIcon.setOnClickListener {
            val on = !it.isSelected; setCtlOn(binding.ctlSpeakerIcon, on); SipManager.setSpeaker(on)
        }
        // 키패드: 현재 통화 끊고 새 번호로 거는 모드로 다이얼러 열기
        binding.ctlKeypadIcon.setOnClickListener { openDialerFor(PendingAction.NEW_CALL) }
        // 돌려주기: 현재 통화를 다른 번호로 넘겨주는 모드로 다이얼러 열기
        binding.ctlTransferIcon.setOnClickListener { openDialerFor(PendingAction.TRANSFER) }
        // 녹음: 시각 토글만 (기능 추후)
        binding.ctlRecordIcon.setOnClickListener { setCtlOn(binding.ctlRecordIcon, !it.isSelected) }
    }

    /** 통화 중에 다이얼러를 열어 '새 통화' 또는 '넘겨주기' 대상 번호를 입력받음. */
    private fun openDialerFor(action: PendingAction) {
        pendingAction = action
        binding.numval.text = ""
        binding.numtgt.text = when (action) {
            PendingAction.NEW_CALL -> "새 통화 — 현재 통화는 종료됩니다"
            PendingAction.TRANSFER -> "돌려주기 — 넘길 번호 입력 후 통화"
            else -> "사무실 · 내선 통화"
        }
        showInCall(false)     // 다이얼러 표시 (통화는 계속 유지됨)
    }

    /** 컨트롤 켜짐/꺼짐 시각 처리(켜지면 밝은 원 + 어두운 아이콘으로 반전). */
    private fun setCtlOn(icon: ImageView, on: Boolean) {
        icon.isSelected = on
        icon.setColorFilter(ContextCompat.getColor(this, if (on) R.color.bg else R.color.textPrimary))
    }

    /** 통화 끝나면 모든 컨트롤 토글 초기화. */
    private fun resetControls() {
        listOf(binding.ctlMuteIcon, binding.ctlKeypadIcon, binding.ctlSpeakerIcon,
            binding.ctlTransferIcon, binding.ctlRecordIcon).forEach { setCtlOn(it, false) }
    }

    /** 다이얼러 ↔ 통화중 화면 전환. */
    private fun showInCall(inCall: Boolean) {
        binding.dialerView.visibility = if (inCall) View.GONE else View.VISIBLE
        binding.inCallView.visibility = if (inCall) View.VISIBLE else View.GONE
    }

    // ---------------- 화면 갱신 ----------------
    /** 등록 상태를 상태칩(점 + 문구)으로 표시. */
    private fun updateRegistration(state: RegistrationState, message: String) {
        val (label, dotColor) = when (state) {
            RegistrationState.Ok -> "등록됨" to R.color.accent
            RegistrationState.Progress -> "등록 중" to R.color.textMuted
            RegistrationState.Failed -> "등록 실패" to R.color.red
            RegistrationState.Cleared -> "등록 해제" to R.color.textMuted
            else -> "$state" to R.color.textMuted
        }
        binding.dchipText.text = "내선 ${SipManager.EXTENSION} · $label"
        binding.statusDot.backgroundTintList = ContextCompat.getColorStateList(this, dotColor)
    }

    /** 통화 상태에 따라 다이얼러/통화중 화면을 전환하고 정보를 갱신. */
    private fun updateCall(state: Call.State) {
        when (state) {
            Call.State.OutgoingInit, Call.State.OutgoingProgress,
            Call.State.OutgoingRinging, Call.State.Connected, Call.State.StreamsRunning -> {
                // 넘겨주기/새통화 입력 중이면 다이얼러를 유지(통화중 화면으로 덮어쓰지 않음)
                if (pendingAction == PendingAction.NONE) showInCall(true)
                binding.icName.text = "내선 ${SipManager.remoteUser()}"
                binding.icStatus.text = when (state) {
                    Call.State.OutgoingInit, Call.State.OutgoingProgress -> "발신 중…"
                    Call.State.OutgoingRinging -> "벨 울리는 중…"
                    else -> "통화 중"
                }
            }
            Call.State.End, Call.State.Released, Call.State.Error -> {
                showInCall(false)
                resetControls()
                pendingAction = PendingAction.NONE
                binding.numtgt.text = "사무실 · 내선 통화"
            }
            else -> { /* Idle/Incoming 등은 수신화면(IncomingCallActivity)이 처리 */ }
        }
    }

    override fun onDestroy() {
        // 화면이 사라질 때 리스너 제거 (메모리 누수/오래된 화면 참조 방지)
        SipManager.removeRegistrationListener(regL)
        SipManager.removeCallListener(callL)
        super.onDestroy()
    }
}
