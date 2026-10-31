plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
}

import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.Properties

/**
 * 构建时间：写进 BuildConfig 与资源，启动页会显示。
 * 目的是让"装的是哪个包"一眼可辨 —— 之前调试时被旧包坑过。
 */
val buildStamp: String = SimpleDateFormat("yyyy-MM-dd HH:mm", Locale.US).format(Date())

android {
    namespace = "com.nxwa.nxconnect"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.nxwa.nxconnect"
        minSdk = 24
        targetSdk = 35
        testInstrumentationRunner = "com.nxwa.nxconnect.GalleryInteractionProbe"
        // 版本规则：<主>.<次>.<修订>；每加一次功能或修一批 bug 就把 versionCode +1
        versionCode = 77
        versionName = "0.20.29"

        buildConfigField("String", "BUILD_STAMP", "\"$buildStamp\"")
        resValue("string", "build_stamp", "\"$buildStamp\"")
    }

    // 侧载安装必须签名，Android 14+ 还拒装 debug 签名的包。
    //
    // ★ 签名凭据从 `keystore.properties` 读取，**不进仓库**。
    //   历史教训：早期版本把口令硬编码在这里、密钥库也放在仓库里。开源后那等于
    //   公开自己的签名身份 —— 任何人都能用同一密钥签 APK 冒充本应用。
    //   现在没有 keystore.properties 时回退到 debug 签名，让贡献者也能构建。
    //   生成密钥：tools/make-keystore.ps1
    val keystorePropsFile = rootProject.file("keystore.properties")
    val keystoreProps = Properties()
    if (keystorePropsFile.exists()) {
        // 用显式 reader，避免 Kotlin DSL 下对 InputStream.load 的解析歧义
        keystorePropsFile.reader(Charsets.UTF_8).use { keystoreProps.load(it) }
    }
    val hasReleaseKey = keystoreProps.getProperty("storeFile") != null &&
        rootProject.file(keystoreProps.getProperty("storeFile")).exists()

    signingConfigs {
        if (hasReleaseKey) {
            create("local") {
                storeFile = rootProject.file(keystoreProps.getProperty("storeFile"))
                storePassword = keystoreProps.getProperty("storePassword")
                keyAlias = keystoreProps.getProperty("keyAlias")
                keyPassword = keystoreProps.getProperty("keyPassword")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            // 有本地密钥就用它；没有就退回 debug 签名（贡献者构建不中断）
            signingConfig = if (hasReleaseKey) {
                signingConfigs.getByName("local")
            } else {
                signingConfigs.getByName("debug")
            }
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
        }
        debug {
            // 有本地密钥时与 release 同一签名，方便覆盖安装而无需先卸载
            signingConfig = if (hasReleaseKey) {
                signingConfigs.getByName("local")
            } else {
                signingConfigs.getByName("debug")
            }
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }

    buildFeatures {
        viewBinding = true
        buildConfig = true
    }

    testOptions {
        // 协议层刻意做成纯 JVM（不碰 Android 类），所以这里不需要 Android 资源，
        // 也不允许用 returnDefaultValues 掩盖“误用平台类”的问题。
        unitTests.isIncludeAndroidResources = false
        unitTests.all {
            it.testLogging {
                events("passed", "failed", "skipped")
                showStandardStreams = true
                exceptionFormat = org.gradle.api.tasks.testing.logging.TestExceptionFormat.FULL
            }
        }
    }
}

dependencies {
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.appcompat)
    implementation(libs.material)
    implementation(libs.androidx.recyclerview)
    implementation(libs.androidx.constraintlayout)
    implementation(libs.androidx.activity)
    implementation(libs.androidx.swiperefresh)
    implementation(libs.androidx.exifinterface)
    implementation(libs.kotlinx.coroutines.android)

    // 协议层是纯 JVM 的（不依赖 Android 类），所以能在本机直接跑单元测试。
    // 测试里用 JDK 内置 HttpServer 模拟相机端，被测对象是真实的 NxCamera 实现。
    testImplementation(libs.junit)
    testImplementation(libs.kotlinx.coroutines.android)
}
