import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.plugin.compose")
}

// Package only production render assets referenced by films.json. Research
// catalogs and golden fixtures stay out of the application APK.
val productFilmAssets = layout.buildDirectory.dir("generated/product-film-assets")
abstract class StageProductFilmAssets : Sync() {
    @get:OutputDirectory abstract val assetsDirectory: DirectoryProperty
}
val stageProductFilmAssets = tasks.register<StageProductFilmAssets>("stageProductFilmAssets") {
    assetsDirectory.set(productFilmAssets)
    from(project(":core").layout.projectDirectory.dir("local-data")) {
        include("shared-catalog/*.cxohsv")
        include("bw-catalog/*.cxohsv")
        include("threecurve-catalog/*.cxohsv")
        include("rgb-catalog/*.cxorgb")
        include("dynamic-direct-catalog/*.cxohsv")
        include("dynamic-provider-bw-catalog/*.cxohsv")
        include("srgb-entry-pix65.cxoentry")
        include("output-cmm/widegamut-to-srgb.lcmsbin")
        exclude("shared-catalog/ColorModeArgentiqueKodakEkta64T.cxohsv")
        into("films")
    }
    from(rootProject.layout.projectDirectory.dir("THIRD_PARTY_NOTICES")) {
        include("*.txt")
        into("licenses")
    }
    from(rootProject.layout.projectDirectory.file("release/dependency-license-review.csv")) {
        into("licenses")
    }
    from(rootProject.layout.projectDirectory.file("release/PRIVACY.md")) {
        into("legal")
    }
    from(project(":core").layout.projectDirectory.dir("local-data/grain")) {
        include("no-toning-context.cxoctx")
        into("films")
    }
    // Static camera calibration and provider resources; no captured photo bakes.
    from(project(":core").layout.projectDirectory.dir("local-data/raw-profile")) {
        include("cameras.json")
        include("s1rii-fidelity.json")
        include("s9-fidelity.json")
        include("sony-a7iv-fidelity.json")
        include("batch-generated/cameras.json")
        include("batch-generated/profiles/*.json")
        include("batch-generated/white-balance/*.cxowb")
        into("raw")
    }
    from(project(":core").layout.projectDirectory.dir("local-data/raw-wb")) {
        include("s1rii-grid.golden")
        include("s9-grid.cxowb")
        include("sony-a7iv-grid.cxowb")
        into("raw")
    }
    from(project(":core").layout.projectDirectory.dir("local-data/raw-films")) {
        include("curves/*.bin")
        into("raw-films")
    }
    from(project(":core").layout.projectDirectory.dir("local-data/raw-negative-providers")) {
        include("catalog.cxornp")
        into("raw-negative-providers")
    }
    from(project(":core").layout.projectDirectory.dir("local-data/raw-static-providers")) {
        include("catalog.cxornp")
        into("raw-static-providers")
    }
    from(project(":core").layout.projectDirectory.dir("local-data/raw-special-providers")) {
        include("catalog.cxornp")
        into("raw-special-providers")
    }
    from(project(":core").layout.projectDirectory.dir("local-data/raw-camera-bw")) {
        include("catalog.json")
        include("curves/*.bin")
        into("raw-camera-bw")
    }
    into(assetsDirectory)
}

// Stage the canonical verified resource pack once into generated APK assets.
val bundledGrainAssets = layout.buildDirectory.dir("generated/bundled-grain-assets")
abstract class StageBundledGrainAsset : Sync() {
    @get:OutputDirectory abstract val assetsDirectory: DirectoryProperty
}
val stageBundledGrainAsset = tasks.register<StageBundledGrainAsset>("stageBundledGrainAsset") {
    assetsDirectory.set(bundledGrainAssets)
    from(rootProject.layout.projectDirectory.file("releases/native-grain8-v1.cxograinpack"))
    into(assetsDirectory)
    doFirst {
        check(rootProject.file("releases/native-grain8-v1.cxograinpack").isFile) {
            "Missing canonical bundled grain pack"
        }
    }
}

// 本地签名材料（.cxo-release-signing.properties + keystore）不入版本库。
// 没有它也能构建，但产物是未签名包，装不上。
val publicSigningFile = rootProject.file(".cxo-release-signing.properties")
val publicSigning = Properties().apply {
    if (publicSigningFile.isFile) publicSigningFile.inputStream().use(::load)
}
val publicSigningReady = listOf("storeFile", "storePassword", "keyAlias", "keyPassword")
    .all { !publicSigning.getProperty(it).isNullOrBlank() }
val publicSigningStore = publicSigning.getProperty("storeFile")?.let(rootProject::file)

android {
    namespace = "dev.cxo.filmpack.app"
    buildFeatures { compose = true }
    compileSdk = 37
    ndkVersion = "30.0.16248370"
    externalNativeBuild {
        cmake {
            path = file("src/main/cpp/CMakeLists.txt")
            version = "3.31.6"
        }
    }

    defaultConfig {
        applicationId = "dev.cxo.filmpack.app"
        minSdk = 26
        targetSdk = 37
        versionCode = 30
        versionName = "0.7.3-preview"
        ndk { abiFilters += listOf("arm64-v8a","armeabi-v7a","x86","x86_64") }
        externalNativeBuild { cmake { arguments += "-DANDROID_STL=c++_static" } }
        testInstrumentationRunner = "android.test.InstrumentationTestRunner"
    }

    signingConfigs {
        // Single long-term release identity shared by the public-beta and the
        // closed-group stable build. Preview keeps the debug key so existing
        // test installs stay upgrade-compatible until the one-time migration.
        create("cxoRelease") {
            // v2 covers API 26+; v3 adds the key-rotation lineage record so a
            // future key change can still be delivered as an in-place update.
            enableV2Signing = true
            enableV3Signing = true
            if (publicSigningReady) {
                storeFile = publicSigningStore
                storePassword = publicSigning.getProperty("storePassword")
                keyAlias = publicSigning.getProperty("keyAlias")
                keyPassword = publicSigning.getProperty("keyPassword")
            }
        }
    }

    buildTypes {
        // 唯一的正式分发线：发布级运行 + 长期密钥。日常开发与发版都走这里。
        create("stable") {
            initWith(getByName("release"))
            isDebuggable = false
            isMinifyEnabled = false
            matchingFallbacks += "release"
            signingConfig = signingConfigs.getByName("cxoRelease")
        }
    }
    testBuildType = providers.gradleProperty("cxoTestBuildType").orElse("debug").get()

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    useLibrary("android.test.runner")
    useLibrary("android.test.base")
    useLibrary("android.test.mock")
}

androidComponents.onVariants { variant ->
    if (variant.buildType == "stable") {
        variant.outputs.forEach { output ->
            output.versionCode.set(33)
            output.versionName.set("1.0.1")
        }
    }
    variant.sources.assets?.addGeneratedSourceDirectory(stageProductFilmAssets) { it.assetsDirectory }
    variant.sources.assets?.addGeneratedSourceDirectory(stageBundledGrainAsset) { it.assetsDirectory }
}

dependencies {
    implementation(project(":glass-catalog"))
    implementation(project(":core"))
    implementation(project(":android-adapter"))
}
