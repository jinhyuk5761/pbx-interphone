package com.mycompany.interphone

import android.content.Context
import org.linphone.core.Account
import org.linphone.core.AudioDevice
import org.linphone.core.Call
import org.linphone.core.Core
import org.linphone.core.CoreListenerStub
import org.linphone.core.Factory
import org.linphone.core.RegistrationState
import org.linphone.core.TransportType
import java.util.concurrent.CopyOnWriteArrayList

/**
 * SipManager — 이 앱의 'SIP 전화 엔진' (Linphone Core 를 감싼 싱글톤).
 *
 * [Linphone 이란]
 * 직접 만들기 매우 어려운 'SIP 신호 + 음성(RTP) 처리'를 대신 해주는 오픈소스 라이브러리.
 * 우리는 Core(핵심 객체)에게 "등록해줘 / 전화 걸어줘 / 받아줘" 만 시키면 됩니다.
 *
 * [하는 일]
 *  - 내선번호로 PBX 에 등록(register)
 *  - 전화 걸기(call) / 받기(acceptCall) / 끊기(hangup)
 *  - 통화 상태가 바뀌면(걸림/벨/연결/종료) 화면·서비스에 알려줌(리스너)
 *  - 전화가 오면(IncomingReceived) 수신 화면/벨을 직접 띄움(CallUi)
 *
 * object = 싱글톤. 앱 어디서든 SipManager.xxx 로 같은 엔진을 씁니다.
 */
object SipManager {

    var EXTENSION = "103"                      // 내 내선번호 (사용자가 설정, Prefs 에서 로드)
    const val SERVER = "192.168.0.112"         // PBX 서버 IP (다른 PC면 여기 변경)
    private const val PASSWORD = "linphone"    // 비밀번호 — PBX 가 인증 안 하므로 실제론 미사용

    private lateinit var core: Core            // Linphone 핵심 객체 (init 에서 생성)
    private lateinit var appCtx: Context       // 알림/벨 띄울 때 쓸 앱 컨텍스트

    // 등록/통화 상태가 바뀔 때 알려줄 '구독자(리스너)' 목록.
    // 화면(MainActivity)·수신화면(IncomingCallActivity) 등이 동시에 구독할 수 있어 목록 사용.
    // CopyOnWriteArrayList = 여러 스레드가 동시에 써도 안전한 리스트.
    private val regListeners = CopyOnWriteArrayList<(RegistrationState, String) -> Unit>()
    private val callListeners = CopyOnWriteArrayList<(Call.State, String) -> Unit>()

    fun addRegistrationListener(l: (RegistrationState, String) -> Unit) = regListeners.add(l)
    fun removeRegistrationListener(l: (RegistrationState, String) -> Unit) = regListeners.remove(l)
    fun addCallListener(l: (Call.State, String) -> Unit) = callListeners.add(l)
    fun removeCallListener(l: (Call.State, String) -> Unit) = callListeners.remove(l)

    // Linphone Core 가 상태 변화를 알려줄 때 호출하는 콜백 모음
    private val coreListener = object : CoreListenerStub() {
        // 등록 상태 변화 (등록중/등록됨/실패 등) -> 구독자들에게 전달
        override fun onAccountRegistrationStateChanged(
            core: Core, account: Account, state: RegistrationState, message: String
        ) {
            regListeners.forEach { it(state, message) }
        }

        // 통화 상태 변화 (수신/발신/연결/종료 등)
        override fun onCallStateChanged(
            core: Core, call: Call, state: Call.State, message: String
        ) {
            // 통화 음성이 흐르기 시작하면, 이어폰이 연결돼 있으면 이어폰으로 오디오 보냄
            if (state == Call.State.Connected || state == Call.State.StreamsRunning) {
                try { routeAudioToHeadset(call) } catch (_: Exception) {}
            }
            handleCallUi(state)                       // 수신화면/벨 처리 (서비스 없이 전역에서)
            callListeners.forEach { it(state, message) }  // 화면들에게도 알림
        }

        // 통화 중에 이어폰을 꽂거나 빼면 호출됨 -> 다시 라우팅
        override fun onAudioDevicesListUpdated(core: Core) {
            val call = core.currentCall ?: return
            try { routeAudioToHeadset(call) } catch (_: Exception) {}
        }
    }

    // 출력(소리) 우선순위: 블루투스/유선/USB 이어폰 종류
    private val HEADSET_OUT = setOf(
        AudioDevice.Type.Bluetooth, AudioDevice.Type.BluetoothA2DP,
        AudioDevice.Type.Headset, AudioDevice.Type.Headphones,
        AudioDevice.Type.HearingAid, AudioDevice.Type.GenericUsb,
    )
    // 입력(마이크) 우선순위: 마이크 있는 헤드셋 (유선은 마이크 없을 수 있어 폰 마이크로 폴백)
    private val HEADSET_IN = setOf(
        AudioDevice.Type.Bluetooth, AudioDevice.Type.Headset, AudioDevice.Type.GenericUsb,
    )

    /**
     * 이어폰이 연결돼 있으면 통화 오디오(소리/마이크)를 이어폰으로 보냄.
     * 이어폰이 없으면 기본(귀에 대는 수화부 Earpiece / 폰 마이크)으로.
     */
    private fun routeAudioToHeadset(call: Call) {
        if (!this::core.isInitialized) return
        val devices = core.audioDevices

        // 출력: 이어폰 우선, 없으면 수화부(Earpiece)
        val out = devices.firstOrNull {
            it.hasCapability(AudioDevice.Capabilities.CapabilityPlay) && it.type in HEADSET_OUT
        } ?: devices.firstOrNull {
            it.hasCapability(AudioDevice.Capabilities.CapabilityPlay) && it.type == AudioDevice.Type.Earpiece
        }
        if (out != null) call.outputAudioDevice = out

        // 입력(마이크): 이어폰 마이크 우선, 없으면 폰 기본 마이크
        val mic = devices.firstOrNull {
            it.hasCapability(AudioDevice.Capabilities.CapabilityRecord) && it.type in HEADSET_IN
        } ?: devices.firstOrNull {
            it.hasCapability(AudioDevice.Capabilities.CapabilityRecord) && it.type == AudioDevice.Type.Microphone
        }
        if (mic != null) call.inputAudioDevice = mic
    }

    /**
     * Core 통화 상태에 따라 수신 화면과 벨을 켜고 끕니다.
     * (포그라운드 서비스를 없앴기 때문에, 여기 SipManager 가 전역에서 직접 담당)
     */
    private fun handleCallUi(state: Call.State) {
        if (!this::appCtx.isInitialized) return
        when (state) {
            // 전화가 옴 -> 전체화면 수신 알림 표시. (벨/진동은 Linphone 네이티브 링잉이 처리)
            Call.State.IncomingReceived, Call.State.PushIncomingReceived -> {
                CallUi.showIncoming(appCtx, remoteUser())
            }
            // 연결됨 또는 종료 -> 수신 알림 제거
            Call.State.Connected, Call.State.StreamsRunning,
            Call.State.End, Call.State.Released, Call.State.Error -> {
                CallUi.cancelIncoming(appCtx)
            }
            else -> { /* 그 외 상태는 무시 */ }
        }
    }

    /** Linphone Core 생성/시작. 앱 시작 시 1번만 (두 번 호출해도 안전). */
    fun init(context: Context) {
        if (this::core.isInitialized) return      // 이미 만들었으면 그냥 종료
        appCtx = context.applicationContext
        val factory = Factory.instance()          // Linphone 객체 공장
        core = factory.createCore(null, null, context)
        core.addListener(coreListener)            // 상태 변화 콜백 연결
        // 네이티브 링잉 OFF: Linphone 이 직접 울리는 벨은 소리가 작고 진동도 없어서 끔.
        // 대신 우리가 IncomingCallActivity(포그라운드)에서 큰 벨소리 + 진동을 직접 울림.
        try { core.isNativeRingingEnabled = false } catch (_: Exception) {}
        core.start()                              // 엔진 가동
    }

    /** 현재 EXTENSION 번호로 PBX 에 등록. 이미 계정이 있으면 아무 것도 안 함. */
    fun register() {
        if (!this::core.isInitialized) return
        if (core.accountList.isNotEmpty()) return    // 이미 등록 계정 있음 -> 중복 방지

        val factory = Factory.instance()
        // 인증정보 (PBX 가 비번 검사를 안 해서 실제론 안 쓰이지만, 형식상 등록)
        val authInfo = factory.createAuthInfo(EXTENSION, null, PASSWORD, null, null, SERVER, null)
        core.addAuthInfo(authInfo)

        // 계정 파라미터: 내 주소(sip:내선@서버), 서버 주소(UDP), 등록 켜기
        val params = core.createAccountParams()
        // 참고: setIdentityAddress 등은 int 를 반환해서 '프로퍼티 대입(=)'이 안 됨 -> 메서드로 호출
        params.setIdentityAddress(factory.createAddress("sip:$EXTENSION@$SERVER"))
        val serverAddr = factory.createAddress("sip:$SERVER")
        serverAddr?.setTransport(TransportType.Udp)
        params.setServerAddress(serverAddr)
        params.isRegisterEnabled = true

        val account = core.createAccount(params)
        core.addAccount(account)
        core.defaultAccount = account            // 기본 계정으로 지정
    }

    /** 현재 등록 상태 (없으면 null). 화면에서 "등록됨/등록중" 표시에 사용. */
    fun currentState(): RegistrationState? =
        if (this::core.isInitialized) core.defaultAccount?.state else null

    fun isInitialized(): Boolean = this::core.isInitialized

    /** 내선번호 설정(등록하기 전 단계). */
    fun configure(ext: String) { EXTENSION = ext }

    /** 내선번호 변경(이미 등록된 상태에서): 기존 계정/인증 지우고 새 번호로 다시 등록. */
    fun reconfigure(ext: String) {
        if (!this::core.isInitialized) { EXTENSION = ext; return }
        EXTENSION = ext
        core.clearAccounts()
        core.clearAllAuthInfo()
        register()
    }

    /** 현재 통화의 상태 (없으면 null). 화면 복귀 시 '통화 중' UI 복원에 사용. */
    fun currentCallState(): Call.State? =
        if (this::core.isInitialized) activeCall()?.state else null

    // ---------------- 통화 동작 ----------------

    /** 전화 걸기: sip:상대내선@서버 로 INVITE 발신. */
    fun call(ext: String) {
        if (!this::core.isInitialized || ext.isBlank()) return
        core.invite("sip:${ext.trim()}@$SERVER")
    }

    /** 걸려온 전화 받기. */
    fun acceptCall() {
        activeCall()?.accept()
    }

    /** 통화 끊기 / 수신 거절. */
    fun hangup() {
        activeCall()?.terminate()
    }

    /** 지금 진행 중인 통화 객체 (없으면 null). */
    private fun activeCall(): Call? =
        if (this::core.isInitialized) core.currentCall ?: core.calls.firstOrNull() else null

    /** 통화 상대의 내선번호 (수신화면에 "내선 101" 표시할 때 사용). */
    fun remoteUser(): String = activeCall()?.remoteAddress?.username ?: ""
}
