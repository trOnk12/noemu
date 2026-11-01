plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}
android {
    namespace = "dev.noemu.fixture.views"
    compileSdk = 35
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    defaultConfig {
        applicationId = "dev.noemu.fixture.views"
        minSdk = 26
        targetSdk = 35
        versionCode = 1
        versionName = "1"
    }
    sourceSets["main"].java.srcDir("../shared-main")
    sourceSets["test"].java.srcDir("../shared-test")
}
kotlin {
    jvmToolchain(21)
    compilerOptions { jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17) }
}
