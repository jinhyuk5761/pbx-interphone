package com.mycompany.interphone

import android.content.Context
import android.util.Log
import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage

/**
 * FcmService — Firebase 클라우드 메시징(FCM) 푸시를 받는 서비스.
 *
 * [왜 필요한가]
 * 앱이 완전히 꺼져 있거나 졸고 있으면 SIP 등록이 끊겨 전화를 못 받습니다.
 * 그래서 PBX 가 "전화 왔다" 푸시를 보내면, 안드로이드가 이 서비스를 깨워 onMessageReceived 를
 * 호출합니다. 그 안에서 SIP 엔진을 다시 켜고 등록하면 곧 도착하는 통화를 받을 수 있습니다.
 *
 * AndroidManifest.xml 에 <service ...MESSAGING_EVENT...> 로 등록돼 있어야 동작합니다.
 */
class FcmService : FirebaseMessagingService() {

    /**
     * FCM 토큰이 새로 발급/갱신될 때 호출됩니다.
     * 토큰은 '이 기기로 푸시를 보낼 주소' 같은 것. 저장하고 PBX 에 등록해 둡니다.
     */
    override fun onNewToken(token: String) {
        Log.i(TAG, "onNewToken: $token")
        Prefs.setFcmToken(applicationContext, token)
        Tokens.push(applicationContext)         // 내선번호와 함께 서버로 전송
    }

    /**
     * 푸시 메시지가 도착하면 호출됩니다 (앱이 백그라운드여도 'data' 메시지면 호출됨).
     */
    override fun onMessageReceived(message: RemoteMessage) {
        Log.i(TAG, "onMessageReceived: ${message.data}")
        if (message.data["type"] == "incoming_call") {
            val caller = message.data["caller"] ?: "?"
            Log.i(TAG, "수신 푸시 -> Core 깨워 등록 (발신 $caller)")
            // 푸시는 '깨우기 신호' 역할.
            // SIP 엔진을 켜고 등록하면, 발신자가 재전송하는 INVITE 가 곧 도착하고,
            // 그러면 SipManager 가 자동으로 수신 화면을 띄웁니다.
            SipManager.init(applicationContext)
            Prefs.getExtension(applicationContext)?.let { SipManager.configure(it) }
            SipManager.register()
        }
    }

    companion object {
        const val TAG = "Interphone-FCM"        // logcat 에서 필터링할 때 쓰는 태그
    }
}

/**
 * Tokens — '내선번호 + FCM 토큰' 이 둘 다 준비되면 서버로 보내는 도우미.
 * 네트워크 통신이라 백그라운드 스레드에서 실행합니다.
 */
object Tokens {
    fun push(ctx: Context) {
        val ext = Prefs.getExtension(ctx) ?: return     // 내선번호 없으면 보류
        val token = Prefs.getFcmToken(ctx) ?: return    // 토큰 없으면 보류
        Thread {
            val ok = PbxApi.registerToken(ext, token)
            Log.i(FcmService.TAG, "PBX 토큰 등록 ${if (ok) "성공" else "실패"} (내선 $ext)")
        }.start()
    }
}
