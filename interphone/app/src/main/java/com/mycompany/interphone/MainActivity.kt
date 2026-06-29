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

        // 상태 변화 구독 시작
        SipManager.addRegistrationListener(regL)
        SipManager.addCallListener(callL)

        // 버튼 동작 연결
        binding.callButton.setOnClickListener {
            val target = binding.targetInput.text.toString().trim()
            if (target.isNotEmpty()) SipManager.call(target)        // 입력한 번호로 전화
        }
        binding.acceptButton.setOnClickListener { SipManager.acceptCall() }   // 받기
        binding.hangupButton.setOnClickListener { SipManager.hangup() }       // 끊기
        binding.changeNumberButton.setOnClickListener { openSetup(firstRun = false) }  // 번호 변경

        fetchAndSyncToken()            // FCM 토큰 가져와 서버에 등록

        // 저장된 내선번호가 있으면 그대로 등록, 없으면 첫 설정 화면을 띄움
        val saved = Prefs.getExtension(this)
        if (saved == null) {
            binding.extText.text = "내선 미설정"
            binding.statusText.text = "내선번호를 먼저 설정하세요"
            openSetup(firstRun = true)
        } else {
            SipManager.configure(saved)
            SipManager.register()
            binding.extText.text = "내선 $saved  →  ${SipManager.SERVER}:5060"
            SipManager.currentState()?.let { updateRegistration(it, "") }   // 현재 등록상태 즉시 반영
            // 통화 중에 화면을 다시 열었으면 통화 UI 복원(끊기 버튼이 안 보이던 버그 수정)
            SipManager.currentCallState()?.let { updateCall(it) }
        }
    }

    // ---------------- 내선번호 설정 ----------------
    /** 번호 설정 시작: 서버에서 추천번호/사용중목록을 받아온 뒤 다이얼로그를 띄움. */
    private fun openSetup(firstRun: Boolean) {
        binding.statusText.text = "서버에서 번호 조회 중..."
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
        binding.extText.text = "내선 $ext  →  ${SipManager.SERVER}:5060"
        binding.statusText.text = "내선 $ext  ·  등록 시도 중..."
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

    // ---------------- 화면 갱신 ----------------
    /** 등록 상태를 사람이 읽기 쉬운 문구로 바꿔 표시. */
    private fun updateRegistration(state: RegistrationState, message: String) {
        val line = when (state) {
            RegistrationState.Ok -> "● 등록됨 (Registered)"
            RegistrationState.Progress -> "○ 등록 중..."
            RegistrationState.Failed -> "✕ 등록 실패: $message"
            RegistrationState.Cleared -> "○ 등록 해제됨"
            else -> "상태: $state"
        }
        binding.statusText.text = "내선 ${SipManager.EXTENSION}  ·  $line"
    }

    /** 통화 상태에 따라 안내문구와 버튼 표시를 바꿈. */
    private fun updateCall(state: Call.State) {
        when (state) {
            Call.State.IncomingReceived, Call.State.PushIncomingReceived -> {
                binding.callStateText.text = "📲 ${SipManager.remoteUser()} 수신 중..."
                showCallControls(accept = true, hangup = true, canDial = false)    // 받기/끊기
            }
            Call.State.OutgoingInit, Call.State.OutgoingProgress -> {
                binding.callStateText.text = "발신 중..."
                showCallControls(accept = false, hangup = true, canDial = false)
            }
            Call.State.OutgoingRinging -> {
                binding.callStateText.text = "벨 울리는 중..."
                showCallControls(accept = false, hangup = true, canDial = false)
            }
            Call.State.Connected, Call.State.StreamsRunning -> {
                binding.callStateText.text = "🟢 통화 중 (${SipManager.remoteUser()})"
                showCallControls(accept = false, hangup = true, canDial = false)   // 끊기만
            }
            Call.State.End, Call.State.Released, Call.State.Error -> {
                binding.callStateText.text = "통화 종료"
                showCallControls(accept = false, hangup = false, canDial = true)   // 다시 걸 수 있게
            }
            else -> { /* Idle 등은 무시 */ }
        }
    }

    /** 받기/끊기 버튼 보이기 여부 + 발신 입력 가능 여부를 한 번에 설정. */
    private fun showCallControls(accept: Boolean, hangup: Boolean, canDial: Boolean) {
        binding.acceptButton.visibility = if (accept) View.VISIBLE else View.GONE
        binding.hangupButton.visibility = if (hangup) View.VISIBLE else View.GONE
        binding.callButton.isEnabled = canDial
        binding.targetInput.isEnabled = canDial
    }

    override fun onDestroy() {
        // 화면이 사라질 때 리스너 제거 (메모리 누수/오래된 화면 참조 방지)
        SipManager.removeRegistrationListener(regL)
        SipManager.removeCallListener(callL)
        super.onDestroy()
    }
}
