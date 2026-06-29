package com.mycompany.interphone

import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

/**
 * PbxApi — PBX 서버의 'HTTP 조회 API(8088 포트)' 와 통신하는 클라이언트.
 *
 * SIP(5060) 와는 별개로, PBX 는 8088 포트에 작은 HTTP API 를 제공합니다.
 * 이 클래스는 그 API 를 호출해서:
 *   - 현재 등록된 내선 목록 조회 (중복 확인용)
 *   - 비어있는 다음 내선번호 자동 배정
 *   - 내 FCM 토큰을 서버에 등록
 * 합니다.
 *
 * ⚠️ 주의: 아래 함수들은 '네트워크 통신' 이라 시간이 걸릴 수 있습니다.
 *         메인 스레드(UI)에서 직접 부르면 앱이 멈추므로, 반드시 백그라운드 스레드에서 호출하세요.
 */
object PbxApi {
    // 서버 주소. SipManager.SERVER(PBX IP) 를 그대로 사용.
    private val base get() = "http://${SipManager.SERVER}:8088"

    /** 현재 등록된 내선 목록을 가져옴. 서버에 못 닿으면 null. */
    fun registered(): List<String>? = try {
        val arr = getJson("$base/api/extensions").getJSONArray("registered")
        // JSONArray -> 코틀린 List<String> 로 변환
        (0 until arr.length()).map { arr.getString(it) }
    } catch (e: Exception) {
        null
    }

    /**
     * 비어있는 다음 내선번호를 받아옴 (start 부터 오름차순).
     * device(기기 고유 ID)를 주면, 같은 기기는 '이전에 배정받은 번호'를 그대로 돌려줍니다.
     * 실패 시 null.
     */
    fun next(start: Int = 101, device: String = ""): String? = try {
        // device 값에 특수문자가 있을 수 있으니 URL 인코딩
        val dev = if (device.isNotEmpty())
            "&device=" + java.net.URLEncoder.encode(device, "UTF-8") else ""
        getJson("$base/api/next?start=$start$dev").getString("next")
    } catch (e: Exception) {
        null
    }

    /** 내 내선번호 + FCM 토큰을 서버에 등록. 성공하면 true. */
    fun registerToken(ext: String, token: String): Boolean = try {
        val body = JSONObject().put("ext", ext).put("token", token).toString()
        postJson("$base/api/token", body)
        true
    } catch (e: Exception) {
        false
    }

    /** 주어진 URL 로 GET 요청을 보내고, 응답(JSON)을 파싱해서 돌려줌. */
    private fun getJson(url: String): JSONObject {
        val conn = (URL(url).openConnection() as HttpURLConnection).apply {
            connectTimeout = 3000        // 연결 3초 안에 안 되면 포기
            readTimeout = 3000
            requestMethod = "GET"
        }
        try {
            val text = conn.inputStream.bufferedReader().use { it.readText() }
            return JSONObject(text)
        } finally {
            conn.disconnect()            // 연결 정리
        }
    }

    /** 주어진 URL 로 JSON 본문을 POST 전송. */
    private fun postJson(url: String, body: String) {
        val conn = (URL(url).openConnection() as HttpURLConnection).apply {
            connectTimeout = 3000
            readTimeout = 3000
            requestMethod = "POST"
            doOutput = true              // 본문을 보낼 것이라고 표시
            setRequestProperty("Content-Type", "application/json; charset=utf-8")
        }
        try {
            conn.outputStream.use { it.write(body.toByteArray(Charsets.UTF_8)) }  // 본문 전송
            conn.inputStream.use { it.readBytes() }   // 응답을 읽어 소비(연결 마무리)
        } finally {
            conn.disconnect()
        }
    }
}
