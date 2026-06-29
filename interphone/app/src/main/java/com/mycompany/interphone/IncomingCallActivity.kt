package com.mycompany.interphone

import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.view.WindowManager
import androidx.appcompat.app.AppCompatActivity
import com.mycompany.interphone.databinding.ActivityIncomingBinding
import org.linphone.core.Call

/**
 * IncomingCallActivity — '전화 왔어요' 전체화면.
 *
 * CallUi 의 전체화면 알림이 이 화면을 띄웁니다.
 * 잠금화면 위에서도/화면이 꺼져 있어도 떠올라서, 일반 전화 앱처럼 발신자와 받기/거절 버튼을 보여줍니다.
 */
class IncomingCallActivity : AppCompatActivity() {

    // activity_incoming.xml 을 코드에서 쉽게 다루게 해주는 뷰바인딩 객체
    private lateinit var binding: ActivityIncomingBinding

    // 통화 상태가 바뀌면 호출되는 리스너:
    //  - 연결되면 -> 통화중 화면(MainActivity)으로 이동
    //  - 종료/실패 -> 이 화면 닫기
    private val callListener: (Call.State, String) -> Unit = { state, _ ->
        runOnUiThread {                          // UI 변경은 메인 스레드에서
            when (state) {
                Call.State.Connected, Call.State.StreamsRunning -> openInCallAndFinish()
                Call.State.End, Call.State.Released, Call.State.Error -> finish()
                else -> { /* no-op */ }
            }
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        // --- 이 화면을 '잠금화면 위'에 띄우고, 꺼진 화면을 켜기 ---
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O_MR1) {   // 안드 8.1+
            setShowWhenLocked(true)              // 잠금화면 위에 표시
            setTurnScreenOn(true)               // 화면 켜기
        } else {
            // 옛 버전은 윈도우 플래그로 같은 효과
            @Suppress("DEPRECATION")
            window.addFlags(
                WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED or
                    WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON or
                    WindowManager.LayoutParams.FLAG_DISMISS_KEYGUARD
            )
        }
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)  // 켜둔 채 유지

        binding = ActivityIncomingBinding.inflate(layoutInflater)
        setContentView(binding.root)

        // 발신자 번호 표시 (푸시/알림에서 받은 caller, 없으면 현재 통화 상대)
        val caller = intent.getStringExtra("caller")?.takeIf { it.isNotEmpty() }
            ?: SipManager.remoteUser()
        binding.callerText.text = "내선 $caller"

        SipManager.addCallListener(callListener)   // 통화 상태 변화 구독

        // 이 화면(포그라운드)에서 직접 벨소리 + 진동 시작 (확실히 울림)
        CallUi.startRing(this)

        // 받기 버튼: 통화 수락 후 통화중 화면으로 이동
        binding.acceptButton.setOnClickListener {
            SipManager.acceptCall()
            openInCallAndFinish()
        }
        // 거절 버튼: 통화 끊고 이 화면 닫기
        binding.rejectButton.setOnClickListener {
            SipManager.hangup()
            finish()
        }
    }

    /** 통화중 UI 가 있는 MainActivity 로 이동하면서 이 수신화면은 닫음. */
    private fun openInCallAndFinish() {
        startActivity(
            Intent(this, MainActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
        )
        finish()
    }

    override fun onDestroy() {
        CallUi.stopRing()                             // 벨소리/진동 정지
        SipManager.removeCallListener(callListener)   // 리스너 정리(메모리 누수 방지)
        super.onDestroy()
    }
}
