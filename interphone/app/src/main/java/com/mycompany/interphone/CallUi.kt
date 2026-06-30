package com.mycompany.interphone

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.media.AudioAttributes
import android.media.AudioDeviceInfo
import android.media.AudioManager
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
    // 채널 ID. (채널 설정은 생성 후 못 바꾸므로, 중요도 등을 바꾸면 ID 도 새로 바꿔야 적용됨)
    const val CHAN_INCOMING = "interphone_call_v4"   // v4: 헤드업 없이 알림창에만 (전체화면은 액티비티가 직접)
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
                // IMPORTANCE_LOW = 화면 위로 튀어나오는 '헤드업'을 안 하고, 알림창(상단바)에만 조용히 표시.
                // 전체화면 수신 UI 는 IncomingCallActivity 를 '직접' 띄워서 처리(아래 showIncoming).
                // 벨소리/진동도 그 액티비티가 직접 울리므로 채널은 무음으로 둠.
                val ch = NotificationChannel(
                    CHAN_INCOMING, "수신 전화", NotificationManager.IMPORTANCE_LOW
                ).apply {
                    description = "전화 수신 알림 (알림창 표시, 화면은 앱이 직접 전체화면)"
                    setSound(null, null)                        // 벨소리 없음(액티비티가 울림)
                    lockscreenVisibility = Notification.VISIBILITY_PUBLIC  // 잠금화면에도 표시
                }
                nm.createNotificationChannel(ch)
            }
        }
    }

    /** 수신 화면(전체화면)을 즉시 띄우고, 알림은 알림창에만 표시합니다. */
    fun showIncoming(ctx: Context, caller: String) {
        ensureChannel(ctx)
        // 전체화면 수신 화면 = IncomingCallActivity
        val full = Intent(ctx, IncomingCallActivity::class.java).apply {
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
            putExtra("caller", caller)           // 발신자 번호 전달
        }
        // PendingIntent = '나중에 시스템이 대신 실행할 인텐트' (알림/전체화면 인텐트에 담아 보냄)
        val pi = PendingIntent.getActivity(
            ctx, 1, full,
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )
        // 알림은 '알림창에만' 조용히 (헤드업 X). 화면은 아래 startActivity 로 직접 띄움.
        // setFullScreenIntent 는 혹시 startActivity 가 막힐 때를 위한 안전장치로 남겨둠.
        val n = NotificationCompat.Builder(ctx, CHAN_INCOMING)
            .setSmallIcon(R.drawable.ic_call_notification)
            .setContentTitle("수신 전화")
            .setContentText("내선 $caller 님의 전화")
            .setPriority(NotificationCompat.PRIORITY_LOW)    // 헤드업 안 함 = 알림창에만
            .setCategory(NotificationCompat.CATEGORY_CALL)   // '전화' 종류 알림
            .setContentIntent(pi)                            // 알림 누르면 수신화면 열림
            .setOngoing(true)                                // 사용자가 못 쓸어 없애게
            .setAutoCancel(true)
            .setFullScreenIntent(pi, true)                   // 안전장치(직접 띄우기가 막힐 때만 동작)
            .build()
        ctx.getSystemService(NotificationManager::class.java).notify(INCOMING_ID, n)

        // 전체화면을 '바로' 띄움 (전화처럼).
        //  - '다른 앱 위에 표시(SYSTEM_ALERT_WINDOW)' 권한이 있으면 백그라운드/잠금화면에서도 즉시 뜸.
        //  - 권한이 없으면 위 setFullScreenIntent 가 잠금화면에서 대신 띄워줌.
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
        // 1) 폰 '기본 벨소리'를 반복 재생 (수신화면이 떠 있는 동안)
        try {
            val uri = RingtoneManager.getDefaultUri(RingtoneManager.TYPE_RINGTONE)
            // 블루투스 출력 장치가 연결돼 있으면 벨소리도 그쪽으로 보냄
            val am = ctx.getSystemService(AudioManager::class.java)
            val bt = am?.getDevices(AudioManager.GET_DEVICES_OUTPUTS)?.firstOrNull {
                it.type == AudioDeviceInfo.TYPE_BLUETOOTH_A2DP ||
                    it.type == AudioDeviceInfo.TYPE_BLUETOOTH_SCO
            }
            ringPlayer = MediaPlayer().apply {
                setDataSource(ctx, uri)
                // 블루투스가 있으면 USAGE_MEDIA(블루투스 라우팅이 잘 먹음), 없으면 벨 스트림으로 스피커 재생
                val usage = if (bt != null) AudioAttributes.USAGE_MEDIA
                            else AudioAttributes.USAGE_NOTIFICATION_RINGTONE
                setAudioAttributes(
                    AudioAttributes.Builder()
                        .setUsage(usage)
                        .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
                        .build()
                )
                isLooping = true
                prepare()
                // 벨소리 출력 장치를 블루투스 이어폰으로 강제 (안드 9+). 스피커로 새는 것 방지.
                if (bt != null && Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
                    try { setPreferredDevice(bt) } catch (_: Exception) {}
                }
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
