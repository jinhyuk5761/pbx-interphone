// 루트 빌드 스크립트 — 플러그인 버전만 선언(apply false), 실제 적용은 app 모듈에서.
plugins {
    id("com.android.application") version "8.7.2" apply false
    id("org.jetbrains.kotlin.android") version "2.0.21" apply false
    id("com.google.gms.google-services") version "4.5.0" apply false
}
