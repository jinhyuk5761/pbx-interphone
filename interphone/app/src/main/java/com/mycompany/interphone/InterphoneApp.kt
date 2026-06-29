package com.mycompany.interphone

import android.app.Application

/**
 * InterphoneApp — 앱이 시작될 때 '가장 먼저' 1번 실행되는 곳.
 *
 * 안드로이드에서 Application 클래스의 onCreate() 는, 사용자가 앱을 열든
 * FCM 푸시로 백그라운드에서 깨어나든, '프로세스가 생길 때' 항상 먼저 호출됩니다.
 * 그래서 여기서 SIP Core 를 준비(init)하고, 저장된 내선번호가 있으면 바로 등록합니다.
 *
 * AndroidManifest.xml 의 <application android:name=".InterphoneApp"> 로 연결돼 있습니다.
 */
class InterphoneApp : Application() {
    override fun onCreate() {
        super.onCreate()
        SipManager.init(this)        // Linphone Core 생성/시작 (전화 엔진 준비)

        // 이전에 내선번호를 정해뒀다면 즉시 SIP 등록을 시도합니다.
        // (프로세스가 살아있는 동안 전화를 받을 수 있게)
        // 상시 떠 있는 포그라운드 서비스는 없습니다 — 앱이 꺼져 있으면 FCM 푸시가 깨웁니다.
        Prefs.getExtension(this)?.let { ext ->
            SipManager.configure(ext)
            SipManager.register()
        }
    }
}
