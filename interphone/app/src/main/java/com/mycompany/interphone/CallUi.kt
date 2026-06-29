package com.mycompany.interphone

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.media.AudioAttributes
import android.media.MediaPlayer
import android.media.RingtoneManager
import android.os.Build
import android.os.VibrationEffect
import android.os.Vibrator
import androidx.core.app.NotificationCompat

/**
 * CallUi — 전화가 왔을 때의 '수신 알림 + 벨소리 + 진동' 담당.
 *
 * SipManager 가 통화 상태(IncomingReceived 등)를 보고 이 함수들을 호출합니다.
 * 포그라운드 서비스 없이도 동작하도록 별도 object 로 분리했습니다.
 *
 * 핵심: '전체화면 인텐트(full-screen intent)' 알림.
 *  - 일반 알림과 달리, 잠금화면 위나 화면이 꺼진 상태에서도 IncomingCallActivity 를
 *    '전화 앱처럼' 전체 화면으로 띄울 수 있게 해주는 안드로이드 기능입니다.
 */
object CallUi {
    // 채널 ID. (채널 설정은 생성 후 못 바꾸므로, 벨/진동 설정을 바꾸면 ID 도 새로 바꿔야 적용됨)
    const val CHAN_INCOMING = "interphone_call_v2"
    const val INCOMING_ID = 2                          // 이 알림의 고유 번호

    private var ringPlayer: MediaPlayer? = null        // 벨소리 재생기
    private var vibrator: Vibrator? = null             // 진동기

    /**
     * 알림 '채널' 을 1번만 만듭니다 (안드로이드 8.0+ 는 채널이 있어야 알림 표시 가능).
     * 중요도 HIGH + 전체화면이라 잠금화면 위로 떠오릅니다.
     */
    private fun ensureChannel(ctx: Context) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val nm = ctx.getSystemService(NotificationManager::class.java)
            if (nm.getNotificationChannel(CHAN_INCOMING) == null) {   // 아직 없으면 생성
                // 채널 자체에 '기기 기본 벨소리 + 진동'을 설정 -> 전체화면 액티비티가 안 떠도
                // (헤드업/백그라운드) 시스템이 벨/진동을 보장. (액티비티가 뜨면 looping 벨이 더해짐)
                val ring = RingtoneManager.getDefaultUri(RingtoneManager.TYPE_RINGTONE)
                val attrs = AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_NOTIFICATION_RINGTONE)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
                    .build()
                val ch = NotificationChannel(
                    CHAN_INCOMING, "수신 전화", NotificationManager.IMPORTANCE_HIGH
                ).apply {
                    description = "전화 수신 알림 (전체화면)"
                    setSound(ring, attrs)                       // 기기 기본 벨소리
                    enableVibration(true)
                    vibrationPattern = longArrayOf(0, 1000, 1000)
                    lockscreenVisibility = Notification.VISIBILITY_PUBLIC  // 잠금화면에도 표시
                }
                nm.createNotificationChannel(ch)
            }
        }
    }

    /** 전체화면 수신 알림을 띄웁니다 -> 누르거나 잠금화면이면 IncomingCallActivity 가 뜸. */
    fun showIncoming(ctx: Context, caller: String) {
        ensureChannel(ctx)
        // 알림을 누르면(또는 잠금화면이면 자동으로) 열릴 화면 = IncomingCallActivity
        val full = Intent(ctx, IncomingCallActivity::class.java).apply {
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
            putExtra("caller", caller)           // 발신자 번호 전달
        }
        // PendingIntent = '나중에 시스템이 대신 실행할 인텐트' (알림에 담아 보냄)
        val pi = PendingIntent.getActivity(
            ctx, 1, full,
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )
        val n = NotificationCompat.Builder(ctx, CHAN_INCOMING)
            .setSmallIcon(R.drawable.ic_call_notification)
            .setContentTitle("수신 전화")
            .setContentText("내선 $caller 님의 전화")
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setCategory(NotificationCompat.CATEGORY_CALL)   // '전화' 종류 알림
            .setOngoing(true)                                // 사용자가 못 쓸어 없애게
            .setAutoCancel(true)
            .setFullScreenIntent(pi, true)                   // ★ 전체화면으로 띄우기
            .build()
        ctx.getSystemService(NotificationManager::class.java).notify(INCOMING_ID, n)

        // 가능하면 전체화면을 '바로' 띄움.
        //  - 앱이 포그라운드이거나, FCM 푸시로 막 깨어난 직후엔 백그라운드 액티비티 실행이 허용됨.
        //  - 잠금화면/화면 꺼짐 상태는 위의 setFullScreenIntent 가 대신 띄워줌.
        //  - 둘 다 막히면(드문 경우) 헤드업 알림으로 표시됨.
        try {
            ctx.startActivity(full)
        } catch (_: Exception) {
        }
    }

    /** 수신 알림 제거 (받거나 끊겼을 때). */
    fun cancelIncoming(ctx: Context) {
        ctx.getSystemService(NotificationManager::class.java).cancel(INCOMING_ID)
    }

    /** 벨소리 + 진동 시작 (반복). */
    fun startRing(ctx: Context) {
        stopRing()       // 혹시 울리고 있으면 먼저 정지
        // 1) 벨소리: 기기의 기본 전화벨을 반복 재생
        try {
            val uri = RingtoneManager.getDefaultUri(RingtoneManager.TYPE_RINGTONE)
            ringPlayer = MediaPlayer().apply {
                setDataSource(ctx, uri)
                setAudioAttributes(
                    AudioAttributes.Builder()
                        .setUsage(AudioAttributes.USAGE_NOTIFICATION_RINGTONE)   // '전화벨' 용도
                        .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
                        .build()
                )
                isLooping = true     // 받을 때까지 반복
                prepare()
                start()
            }
        } catch (_: Exception) {
        }
        // 2) 진동: 1초 진동 / 1초 쉼 패턴을 반복(인덱스 0 부터 반복)
        try {
            val vib = ctx.getSystemService(Vibrator::class.java)
            vibrator = vib
            val pattern = longArrayOf(0, 1000, 1000)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                vib?.vibrate(VibrationEffect.createWaveform(pattern, 0))
            } else {
                @Suppress("DEPRECATION")
                vib?.vibrate(pattern, 0)
            }
        } catch (_: Exception) {
        }
    }

    /** 벨소리 + 진동 정지. */
    fun stopRing() {
        ringPlayer?.let { try { it.stop() } catch (_: Exception) {}; it.release() }
        ringPlayer = null
        vibrator?.let { try { it.cancel() } catch (_: Exception) {} }
        vibrator = null
    }
}
