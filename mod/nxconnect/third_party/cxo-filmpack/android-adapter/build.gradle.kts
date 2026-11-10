plugins {
    id("com.android.library")
}

// Stage research fixtures only into the instrumentation APK, without duplicating
// the catalog in source control or putting it in the release library/app.
val fixedFilmTestAssets = layout.buildDirectory.dir("generated/fixed-film-test-assets")
abstract class StageFixedFilmTestAssets : Sync() {
    @get:OutputDirectory abstract val assetsDirectory: DirectoryProperty
}
val prepareFixedFilmTestAssets = tasks.register<StageFixedFilmTestAssets>("prepareFixedFilmTestAssets") {
    dependsOn(":core:verifyPrefixedCatalog", ":core:verifyWideGamutStrength")
    assetsDirectory.set(fixedFilmTestAssets)
    from(project(":core").layout.projectDirectory.dir("local-data")) {
        include("shared-catalog/catalog.cxocat", "shared-catalog/*.cxohsv")
        include("bw-catalog/catalog.cxocat", "bw-catalog/*.cxohsv")
        include("threecurve-catalog/catalog.cxocat", "threecurve-catalog/*.cxohsv")
        include("rgb-catalog/catalog.cxocat", "rgb-catalog/*.cxorgb")
        include("dynamic-direct-catalog/manifest.json", "dynamic-direct-catalog/*.cxohsv")
        include("dynamic-provider-bw-catalog/manifest.json", "dynamic-provider-bw-catalog/*.cxohsv")
        include("srgb-entry-pix65.cxoentry")
        include("film-rgb-base/catalog.cxowbs", "film-rgb-base/prefixed-catalog.golden")
        into("fixed-film")
    }
    from(project(":core").layout.buildDirectory.dir("verification/widegamut-strength")) {
        include("*.f32")
        into("fixed-film/strength")
    }
    into(assetsDirectory)
}

android {
    namespace = "dev.cxo.filmpack.android"
    compileSdk = 37
    useLibrary("android.test.runner")
    useLibrary("android.test.base")
    useLibrary("android.test.mock")

    defaultConfig {
        minSdk = 26
        testInstrumentationRunner = "android.test.InstrumentationTestRunner"
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

androidComponents.onVariants { variant ->
    variant.androidTest?.sources?.assets?.addGeneratedSourceDirectory(prepareFixedFilmTestAssets) { it.assetsDirectory }
}

dependencies {
    implementation(project(":core"))
}
