package com.mycompany.interphone

import android.content.Context

/**
 * Prefs — 작은 설정값(내선번호, FCM 토큰)을 폰에 '영구 저장' 하는 곳.
 *
 * SharedPreferences 는 안드로이드가 제공하는 '키-값' 저장소입니다.
 * 앱을 껐다 켜도 값이 유지됩니다. (작은 값 저장에 적합)
 *
 * 'object' 는 코틀린에서 '싱글톤'(앱 전체에 인스턴스 1개) 을 만드는 키워드입니다.
 * 그래서 어디서든 Prefs.getExtension(...) 처럼 바로 부를 수 있습니다.
 */
object Prefs {
    private const val FILE = "interphone"     // 저장 파일 이름
    private const val KEY_EXT = "extension"    // 내선번호 키
    private const val KEY_FCM = "fcm_token"    // FCM 토큰 키

    // 매번 쓰는 SharedPreferences 핸들을 얻는 도우미
    private fun sp(ctx: Context) = ctx.getSharedPreferences(FILE, Context.MODE_PRIVATE)

    // 내선번호 읽기 (없으면 null) / 쓰기
    fun getExtension(ctx: Context): String? = sp(ctx).getString(KEY_EXT, null)
    fun setExtension(ctx: Context, ext: String) {
        sp(ctx).edit().putString(KEY_EXT, ext).apply()   // apply()=비동기 저장
    }

    // FCM 토큰 읽기/쓰기
    fun getFcmToken(ctx: Context): String? = sp(ctx).getString(KEY_FCM, null)
    fun setFcmToken(ctx: Context, token: String) {
        sp(ctx).edit().putString(KEY_FCM, token).apply()
    }
}
