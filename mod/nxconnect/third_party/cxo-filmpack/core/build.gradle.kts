import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins { id("org.jetbrains.kotlin.jvm") }

kotlin { compilerOptions { jvmTarget.set(JvmTarget.JVM_17) } }
tasks.withType<JavaExec>().configureEach { workingDir(rootProject.projectDir) }

java {
    sourceCompatibility = JavaVersion.VERSION_17
    targetCompatibility = JavaVersion.VERSION_17
}

// Executable numerical verification has its own source set, independent of JUnit discovery.
val verification = sourceSets.create("verification") {
    compileClasspath += sourceSets.main.get().output
    runtimeClasspath += sourceSets.main.get().output
}
kotlin.target.compilations.named("verification") {
    associateWith(kotlin.target.compilations.getByName("main"))
}
configurations[verification.implementationConfigurationName].extendsFrom(configurations.implementation.get())
configurations[verification.runtimeOnlyConfigurationName].extendsFrom(configurations.runtimeOnly.get())

val verifyCore = tasks.register<JavaExec>("verifyCore") {
    dependsOn(verification.classesTaskName)
    classpath = verification.runtimeClasspath
    mainClass.set("dev.cxo.filmpack.core.CoreVerificationKt")
    args("core/local-data/portra400-49.fplut", "core/local-data/heldout.golden")
}
val verifyPrimitives = tasks.register<JavaExec>("verifyPrimitives") {
    dependsOn(verification.classesTaskName)
    classpath = verification.runtimeClasspath
    mainClass.set("dev.cxo.filmpack.core.PrimitiveVerificationKt")
    args("core/local-data/jpeg-primitives.golden")
}
val verifyCurves = tasks.register<JavaExec>("verifyCurves") {
    dependsOn(verification.classesTaskName)
    classpath = verification.runtimeClasspath
    mainClass.set("dev.cxo.filmpack.core.CurveVerificationKt")
    args("core/local-data/native-curves.golden")
}
val verifyPostCurves = tasks.register<JavaExec>("verifyPostCurves") {
    dependsOn(verification.classesTaskName)
    classpath = verification.runtimeClasspath
    mainClass.set("dev.cxo.filmpack.core.PostCurveVerificationKt")
    args("core/local-data/native-postcurves.golden")
}
val verifyHsv = tasks.register<JavaExec>("verifyHsv") {
    dependsOn(verification.classesTaskName)
    classpath = verification.runtimeClasspath
    mainClass.set("dev.cxo.filmpack.core.HsvVerificationKt")
    args("core/local-data/portra400-hsv-static.cxohsv", "core/local-data/hsv-static.golden")
}
val verifyEntryLut = tasks.register<JavaExec>("verifyEntryLut") {
    dependsOn(verification.classesTaskName)
    classpath = verification.runtimeClasspath
    mainClass.set("dev.cxo.filmpack.core.EntryLutVerificationKt")
    args("core/local-data/srgb-entry-pix65.cxoentry", "core/local-data/portra400-hsv-static.cxohsv", "core/local-data/entry-hsv.golden")
}
val verifyEntryExact = tasks.register<JavaExec>("verifyEntryExact") {
    dependsOn(verification.classesTaskName)
    classpath = verification.runtimeClasspath
    mainClass.set("dev.cxo.filmpack.core.EntryExactVerificationKt")
    args(
        "/Users/chenyixuan/ReverseWork/DxOFilmPack/evidence/eir-interface-20261101/native-render-widegamut-application.tif.after.actual-entry-grid.bin",
        "/Users/chenyixuan/ReverseWork/DxOFilmPack/evidence/eir-interface-20261101/native-render-widegamut-application.tif.after.root.q15-rgb.bin",
        "/Users/chenyixuan/ReverseWork/DxOFilmPack/evidence/eir-interface-20261101/native-render-widegamut-application.tif.after.entry.q15-rgb.bin",
        "1024",
    )
}
val verifyColorManagement = tasks.register<JavaExec>("verifyColorManagement") {
    dependsOn(verification.classesTaskName)
    classpath = verification.runtimeClasspath
    mainClass.set("dev.cxo.filmpack.core.ColorManagementVerificationKt")
    args("core/local-data/color-boundary-native.golden")
}
val verifyIccBridge = tasks.register<JavaExec>("verifyIccBridge") {
    dependsOn(verification.classesTaskName)
    classpath = verification.runtimeClasspath
    mainClass.set("dev.cxo.filmpack.core.IccBridgeVerificationKt")
    args("core/local-data/adobe-embedded.icc", "core/local-data/srgb-embedded.icc", "core/local-data/icc-bridge-analytic.golden")
}
val verifyLcms = tasks.register<JavaExec>("verifyLcms") {
    dependsOn(verification.classesTaskName)
    classpath = verification.runtimeClasspath
    mainClass.set("dev.cxo.filmpack.core.LcmsOptimizedVerificationKt")
    args("core/local-data/lcms-2170/adobe-to-srgb.lcmsbin", "core/local-data/lcms-2170/validation.golden",
        "core/local-data/lcms-2170/adobe-to-source-srgb.lcmsbin", "core/local-data/lcms-2170/source-validation.golden")
}
val verifySharedCatalog = tasks.register<JavaExec>("verifySharedCatalog") {
    dependsOn(verification.classesTaskName)
    classpath = verification.runtimeClasspath
    mainClass.set("dev.cxo.filmpack.core.SharedCatalogVerificationKt")
    args("core/local-data/shared-catalog", "core/local-data/srgb-entry-pix65.cxoentry", "core/local-data/shared-catalog/catalog.golden")
}
val verifyBwCatalog = tasks.register<JavaExec>("verifyBwCatalog") {
    dependsOn(verification.classesTaskName)
    classpath = verification.runtimeClasspath
    mainClass.set("dev.cxo.filmpack.core.SharedCatalogVerificationKt")
    args("core/local-data/bw-catalog", "core/local-data/srgb-entry-pix65.cxoentry", "core/local-data/bw-catalog/catalog.golden")
}
val verifyThreeCurveCatalog = tasks.register<JavaExec>("verifyThreeCurveCatalog") {
    dependsOn(verification.classesTaskName)
    classpath = verification.runtimeClasspath
    mainClass.set("dev.cxo.filmpack.core.SharedCatalogVerificationKt")
    args("core/local-data/threecurve-catalog", "core/local-data/srgb-entry-pix65.cxoentry", "core/local-data/threecurve-catalog/catalog.golden")
}
val verifyRgbCatalog = tasks.register<JavaExec>("verifyRgbCatalog") {
    dependsOn(verification.classesTaskName)
    classpath = verification.runtimeClasspath
    mainClass.set("dev.cxo.filmpack.core.RgbCatalogVerificationKt")
    args("core/local-data/rgb-catalog", "core/local-data/srgb-entry-pix65.cxoentry", "core/local-data/rgb-catalog/catalog.golden")
}
val verifyDynamicCatalog = tasks.register<JavaExec>("verifyDynamicCatalog") {
    dependsOn(verification.classesTaskName)
    classpath = verification.runtimeClasspath
    mainClass.set("dev.cxo.filmpack.core.DynamicCatalogVerificationKt")
    args("core/local-data/dynamic-catalog")
}
val verifyDynamicDirectCatalog = tasks.register<JavaExec>("verifyDynamicDirectCatalog") {
    dependsOn(verification.classesTaskName)
    classpath = verification.runtimeClasspath
    mainClass.set("dev.cxo.filmpack.core.DynamicDirectCatalogVerificationKt")
    args("core/local-data/dynamic-direct-catalog", "core/local-data/srgb-entry-pix65.cxoentry",
        "core/local-data/film-rgb-base/catalog.cxowbs")
}
val verifyWhiteBalance = tasks.register<JavaExec>("verifyWhiteBalance") {
    dependsOn(verification.classesTaskName)
    classpath = verification.runtimeClasspath
    mainClass.set("dev.cxo.filmpack.core.WhiteBalanceVerificationKt")
    args("core/local-data/lcms-2170/adobe-to-source-srgb.lcmsbin", "core/local-data/wb-factors/validation.golden",
        "core/local-data/film-rgb-base/catalog.cxowbs",
        "core/local-data/film-rgb-base/native-boundary.golden",
        "core/local-data/film-rgb-base/native-kernel.golden")
}
val verifyPrefixedCatalog = tasks.register<JavaExec>("verifyPrefixedCatalog") {
    dependsOn(verification.classesTaskName)
    classpath = verification.runtimeClasspath
    mainClass.set("dev.cxo.filmpack.core.PrefixedCatalogVerificationKt")
    args("core/local-data", "core/local-data/film-rgb-base/catalog.cxowbs",
        "core/local-data/srgb-entry-pix65.cxoentry", "core/local-data/shared-catalog/catalog.golden",
        "core/local-data/film-rgb-base/prefixed-catalog.golden")
}
val verifyTiff = tasks.register<JavaExec>("verifyTiff") {
    dependsOn(verification.classesTaskName)
    classpath = verification.runtimeClasspath
    mainClass.set("dev.cxo.filmpack.core.Rgb16TiffVerificationKt")
    if (project.hasProperty("tiffOutput")) args(project.property("tiffOutput").toString())
    if (project.hasProperty("tiffIcc")) {
        require(project.hasProperty("tiffOutput"))
        args(project.property("tiffIcc").toString())
    }
}
val verifyWideGamutStrength = tasks.register<JavaExec>("verifyWideGamutStrength") {
    dependsOn(verification.classesTaskName)
    classpath = verification.runtimeClasspath
    mainClass.set("dev.cxo.filmpack.core.WideGamutStrengthVerificationKt")
    args("core/local-data", project.findProperty("strengthOutput")?.toString()
        ?: "core/build/verification/widegamut-strength")
    if (project.hasProperty("providerBwDirectory")) args(project.property("providerBwDirectory").toString())
}
tasks.check { dependsOn(verifyCore, verifyPrimitives, verifyCurves, verifyPostCurves, verifyHsv, verifyEntryLut, verifyEntryExact, verifyColorManagement, verifyIccBridge, verifyLcms, verifySharedCatalog, verifyBwCatalog, verifyThreeCurveCatalog, verifyRgbCatalog, verifyDynamicCatalog, verifyDynamicDirectCatalog, verifyWhiteBalance, verifyPrefixedCatalog, verifyTiff) }
tasks.check { dependsOn(verifyWideGamutStrength) }

val verifyStreamingJpeg = tasks.register<JavaExec>("verifyStreamingJpeg") {
    dependsOn(verification.classesTaskName)
    classpath = verification.runtimeClasspath
    mainClass.set("dev.cxo.filmpack.core.StreamingJpegVerificationKt")
    args(
        project.findProperty("jpegOutput")?.toString()
            ?: "/Users/chenyixuan/ReverseWork/DxOFilmPack/evidence/product-intensity-export-20261101/encoder-fixtures",
        "/Users/chenyixuan/ReverseWork/DxOFilmPack/evidence/product-delivery-20261101/photos/photo-mosaic.png",
        "/Users/chenyixuan/ReverseWork/DxOFilmPack/evidence/product-intensity-export-20261101/vendor/jpeg-js-encoder.js",
    )
}
tasks.check { dependsOn(verifyStreamingJpeg) }

val verifyNativeGrain = tasks.register<JavaExec>("verifyNativeGrain") {
    dependsOn(verification.classesTaskName)
    classpath = verification.runtimeClasspath
    mainClass.set("dev.cxo.filmpack.core.NativeGrainVerificationKt")
    args("core/local-data/grain/portra400-kernel.golden")
}

val verifyNativeGrainResample = tasks.register<JavaExec>("verifyNativeGrainResample") {
    dependsOn(verification.classesTaskName)
    classpath = verification.runtimeClasspath
    mainClass.set("dev.cxo.filmpack.core.ResampleVerificationKt")
    args("core/local-data/grain/resample.golden")
}

tasks.check { dependsOn(verifyNativeGrainResample) }

val verifyNativeGrainPeriodic = tasks.register<JavaExec>("verifyNativeGrainPeriodic") {
    dependsOn(verification.classesTaskName)
    classpath = verification.runtimeClasspath
    mainClass.set("dev.cxo.filmpack.core.PeriodicVerificationKt")
    args("core/local-data/grain/periodic-edge-v2.golden")
}

tasks.check { dependsOn(verifyNativeGrainPeriodic) }
tasks.check { dependsOn(verifyNativeGrain) }

val verifyNativeGrainSeedShift = tasks.register<JavaExec>("verifyNativeGrainSeedShift") {
    dependsOn(verification.classesTaskName)
    classpath = verification.runtimeClasspath
    mainClass.set("dev.cxo.filmpack.core.SeedShiftVerificationKt")
    args("core/local-data/grain/grain-seed.golden", "core/local-data/grain/grain-shift.golden")
}

tasks.check { dependsOn(verifyNativeGrainSeedShift) }

val verifyNativeGrainAmplitude = tasks.register<JavaExec>("verifyNativeGrainAmplitude") {
    dependsOn(verification.classesTaskName)
    classpath = verification.runtimeClasspath
    mainClass.set("dev.cxo.filmpack.core.AmplitudeVerificationKt")
    args("core/local-data/grain/grain-amplitude.golden")
}

tasks.check { dependsOn(verifyNativeGrainAmplitude) }

val verifyNativeGrainPreparation = tasks.register<JavaExec>("verifyNativeGrainPreparation") {
    dependsOn(verification.classesTaskName)
    classpath = verification.runtimeClasspath
    mainClass.set("dev.cxo.filmpack.core.GrainPreparationVerificationKt")
    args(
        "core/local-data/grain/adobe-to-widegamut.lcmsbin",
        "/Users/chenyixuan/ReverseWork/DxOFilmPack/evidence/grain-20261101/cmm-isolation/native-render-native-q15-grid-apply.tif.q15-lut-grid.bin",
        "core/local-data/grain/portra400-preparation.golden",
        "core/local-data/grain/portra400-end-to-end.golden",
        "core/local-data/grain/multifilm-preparation.golden",
    )
}

tasks.check { dependsOn(verifyNativeGrainPreparation) }

val verifyNativeGrainColorLut = tasks.register<JavaExec>("verifyNativeGrainColorLut") {
    dependsOn(verification.classesTaskName)
    classpath = verification.runtimeClasspath
    mainClass.set("dev.cxo.filmpack.core.ColorLutVerificationKt")
    args("core/local-data/grain/color-lut.golden", "core/local-data/grain/finite-fma.golden")
}

tasks.check { dependsOn(verifyNativeGrainColorLut) }

val verifyNativeGrainCatalog = tasks.register<JavaExec>("verifyNativeGrainCatalog") {
    dependsOn(verification.classesTaskName)
    classpath = verification.runtimeClasspath
    mainClass.set("dev.cxo.filmpack.core.GrainCatalogVerificationKt")
    args(
        "core/local-data/grain",
        "core/local-data/grain/adobe-to-widegamut.lcmsbin",
        "core/local-data/grain/catalog-validation.golden",
        "/Users/chenyixuan/ReverseWork/DxOFilmPack/evidence/grain-20261101/catalog-validation",
    )
}

tasks.check { dependsOn(verifyNativeGrainCatalog) }

val verifyNativeGrainUpscale = tasks.register<JavaExec>("verifyNativeGrainUpscale") {
    dependsOn(verification.classesTaskName)
    classpath = verification.runtimeClasspath
    mainClass.set("dev.cxo.filmpack.core.GrainUpscaleVerificationKt")
    args("core/local-data/grain")
}

tasks.check { dependsOn(verifyNativeGrainUpscale) }

val verifyNativeGrainCubic = tasks.register<JavaExec>("verifyNativeGrainCubic") {
    dependsOn(verification.classesTaskName)
    classpath = verification.runtimeClasspath
    mainClass.set("dev.cxo.filmpack.core.CubicVerificationKt")
    args("core/local-data/grain/grain-cubic.golden")
}

tasks.check { dependsOn(verifyNativeGrainCubic) }

val verifyNativeGrainGeometry = tasks.register<JavaExec>("verifyNativeGrainGeometry") {
    dependsOn(verification.classesTaskName)
    classpath = verification.runtimeClasspath
    mainClass.set("dev.cxo.filmpack.core.GrainGeometryVerificationKt")
    args("core/local-data/grain", "/Users/chenyixuan/ReverseWork/DxOFilmPack/evidence/grain-20261101")
}

tasks.check { dependsOn(verifyNativeGrainGeometry) }

val verifyNativeGrainOrientation = tasks.register<JavaExec>("verifyNativeGrainOrientation") {
    dependsOn(verification.classesTaskName)
    classpath = verification.runtimeClasspath
    mainClass.set("dev.cxo.filmpack.core.OrientationVerificationKt")
    args("core/local-data/grain/grain-orientation.golden")
}
val verifyNativeGrainOrientedPreparation = tasks.register<JavaExec>("verifyNativeGrainOrientedPreparation") {
    dependsOn(verification.classesTaskName)
    classpath = verification.runtimeClasspath
    mainClass.set("dev.cxo.filmpack.core.GrainOrientedPreparationVerificationKt")
    args("core/local-data/grain")
}
tasks.check { dependsOn(verifyNativeGrainOrientation, verifyNativeGrainOrientedPreparation) }

val verifyGrainPhotoBoundary = tasks.register<JavaExec>("verifyGrainPhotoBoundary") {
    dependsOn(":app:stageProductFilmAssets")
    dependsOn(verification.classesTaskName)
    classpath = verification.runtimeClasspath
    mainClass.set("dev.cxo.filmpack.core.GrainPhotoBoundaryVerificationKt")
    args("core/local-data/grain", "app/build/generated/product-film-assets")
}
tasks.named("check") { dependsOn(verifyGrainPhotoBoundary) }

val verifyOutputCmm = tasks.register<JavaExec>("verifyOutputCmm") {
    dependsOn(verification.classesTaskName)
    classpath = verification.runtimeClasspath
    mainClass.set("dev.cxo.filmpack.core.OutputCmmVerificationKt")
    args("core/local-data/output-cmm/widegamut-to-srgb.lcmsbin", "core/local-data/output-cmm/output-rgb16.golden")
}
tasks.named("check") { dependsOn(verifyOutputCmm) }

val verifyGrainPreviewScale = tasks.register<JavaExec>("verifyGrainPreviewScale") {
    dependsOn(verification.classesTaskName)
    classpath = verification.runtimeClasspath
    mainClass.set("dev.cxo.filmpack.core.GrainPreviewScaleVerificationKt")
    args("core/local-data/grain")
}
tasks.named("check") { dependsOn(verifyGrainPreviewScale) }

// An external resource pack is optional and deliberately not copied into the APK.
val verifyGrainResources = tasks.register<JavaExec>("verifyGrainResources") {
    dependsOn(verification.classesTaskName)
    classpath = verification.runtimeClasspath
    mainClass.set("dev.cxo.filmpack.core.GrainResourcePackVerificationKt")
    doFirst {
        args(requireNotNull(providers.gradleProperty("grainResourcePack").orNull) {
            "Supply -PgrainResourcePack=/absolute/path/to/native-grain8-v1.cxograinpack"
        },"core/local-data/grain")
    }
}

val verifyGrainContext = tasks.register<JavaExec>("verifyGrainContext") {
    dependsOn(verification.classesTaskName)
    classpath = verification.runtimeClasspath
    mainClass.set("dev.cxo.filmpack.core.GrainContextVerificationKt")
    args("core/local-data/grain")
}
tasks.named("check") { dependsOn(verifyGrainContext) }

val verifyRawSensor = tasks.register<JavaExec>("verifyRawSensor") {
    dependsOn(verification.classesTaskName)
    classpath = verification.runtimeClasspath
    mainClass.set("dev.cxo.filmpack.core.RawSensorVerificationKt")
    args(fileTree("local-data/raw-sensor") { include("*.golden") }.files.sortedBy { it.name }.map { it.path })
}
tasks.named("check") { dependsOn(verifyRawSensor) }

val verifyRawRender = tasks.register<JavaExec>("verifyRawRender") {
    dependsOn(verification.classesTaskName)
    classpath = verification.runtimeClasspath
    mainClass.set("dev.cxo.filmpack.core.RawHsvFilmVerificationKt")
    args("core/local-data/raw-render")
}
tasks.named("check") { dependsOn(verifyRawRender) }

val verifyRawWhiteBalance = tasks.register<JavaExec>("verifyRawWhiteBalance") {
    dependsOn(verification.classesTaskName)
    classpath = verification.runtimeClasspath
    mainClass.set("dev.cxo.filmpack.core.RawWhiteBalanceVerificationKt")
    args("core/local-data/raw-wb")
}
tasks.named("check") { dependsOn(verifyRawWhiteBalance) }

val verifyRawCameraNormalization = tasks.register<JavaExec>("verifyRawCameraNormalization") {
    dependsOn(verification.classesTaskName)
    classpath = verification.runtimeClasspath
    mainClass.set("dev.cxo.filmpack.core.RawCameraNormalizationVerificationKt")
    args("core/local-data/raw-normalization/p1040758-512.golden")
}
tasks.named("check") { dependsOn(verifyRawCameraNormalization) }

val verifyRawFidelityProfile = tasks.register<JavaExec>("verifyRawFidelityProfile") {
    dependsOn(verification.classesTaskName)
    classpath = verification.runtimeClasspath
    mainClass.set("dev.cxo.filmpack.core.RawFidelityProfileVerificationKt")
    args("/Users/chenyixuan/ReverseWork/DxOFilmPack/evidence/raw-fidelity-20261103")
}
tasks.named("check") { dependsOn(verifyRawFidelityProfile) }

val verifyRawColorIntegration = tasks.register<JavaExec>("verifyRawColorIntegration") {
    dependsOn(verification.classesTaskName)
    classpath = verification.runtimeClasspath
    mainClass.set("dev.cxo.filmpack.core.RawColorIntegrationVerificationKt")
    args("core/local-data/raw-integration/p1040758.golden")
}
tasks.named("check") { dependsOn(verifyRawColorIntegration) }

val verifyRawHsvBake = tasks.register<JavaExec>("verifyRawHsvBake") {
    dependsOn(verification.classesTaskName)
    classpath = verification.runtimeClasspath
    mainClass.set("dev.cxo.filmpack.core.RawHsvGridBakerVerificationKt")
    args(fileTree("local-data/raw-bake") { include("*.cxorbake") }.files.sortedBy { it.name }.map { it.path })
}
tasks.named("check") { dependsOn(verifyRawHsvBake) }

val verifyRawRuntimeBake = tasks.register<JavaExec>("verifyRawRuntimeBake") {
    dependsOn(verification.classesTaskName)
    classpath = verification.runtimeClasspath
    mainClass.set("dev.cxo.filmpack.core.RawRuntimeBakeVerificationKt")
    args(
        "core/local-data/raw-films/catalog.json",
        "core/local-data/raw-bake/raw-bake-s1rii-p1055542-elite-v1.cxorbake",
        "core/local-data/raw-films/curves",
        layout.buildDirectory.file("verification/${name}-safety.json").get().asFile.path,
    )
}
tasks.named("check") { dependsOn(verifyRawRuntimeBake) }

val verifyRawThreeCurveBake = tasks.register<JavaExec>("verifyRawThreeCurveBake") {
    dependsOn(verification.classesTaskName)
    classpath = verification.runtimeClasspath
    mainClass.set("dev.cxo.filmpack.core.RawThreeCurveBakeVerificationKt")
    args(
        "core/local-data/raw-films/catalog.json",
        "core/local-data/raw-bake/raw-bake-s1rii-p1055542-elite-v1.cxorbake",
        "core/local-data/raw-films/curves",
        layout.buildDirectory.file("verification/${name}-safety.json").get().asFile.path,
    )
}
tasks.named("check") { dependsOn(verifyRawThreeCurveBake) }

val verifyRawPostCurveConstruction = tasks.register<JavaExec>("verifyRawPostCurveConstruction") {
    dependsOn(verification.classesTaskName)
    classpath = verification.runtimeClasspath
    mainClass.set("dev.cxo.filmpack.core.RawPostCurveConstructionVerificationKt")
    args("/Users/chenyixuan/ReverseWork/DxOFilmPack/evidence/raw-fidelity-20261103")
}
tasks.named("check") { dependsOn(verifyRawPostCurveConstruction) }

val verifyRawBw = tasks.register<JavaExec>("verifyRawBw") {
    dependsOn(verification.classesTaskName)
    classpath = verification.runtimeClasspath
    mainClass.set("dev.cxo.filmpack.core.RawBwVerificationKt")
    args("core/local-data/raw-bw", "core/local-data")
}
tasks.named("check") { dependsOn(verifyRawBw) }

val verifyRawFixedNative = tasks.register<JavaExec>("verifyRawFixedNative") {
    dependsOn(verification.classesTaskName)
    classpath = verification.runtimeClasspath
    mainClass.set("dev.cxo.filmpack.core.RawFixedNativeVerificationKt")
    args("core/local-data", "/Users/chenyixuan/ReverseWork/DxOFilmPack/evidence/raw-fidelity-20261103/native")
}
tasks.named("check") { dependsOn(verifyRawFixedNative) }

val verifyRawRgb = tasks.register<JavaExec>("verifyRawRgb") {
    dependsOn(verification.classesTaskName)
    classpath = verification.runtimeClasspath
    mainClass.set("dev.cxo.filmpack.core.RawRgbFilmVerificationKt")
    val nativeDir = "/Users/chenyixuan/ReverseWork/DxOFilmPack/evidence/raw-fidelity-20261103/native"
    args(listOf("eir", "hie", "tealandpink").flatMap { film ->
        listOf("p1040758", "p1176257").map { photo -> "$nativeDir/small-grid-s1rii-$photo-$film-raw-grid17-v1" }
    })
}
tasks.named("check") { dependsOn(verifyRawRgb) }

val verifyRawRgbContext = tasks.register<JavaExec>("verifyRawRgbContext") {
    dependsOn(verification.classesTaskName)
    classpath = verification.runtimeClasspath
    mainClass.set("dev.cxo.filmpack.core.RawRgbContextVerificationKt")
    args("core/local-data/raw-rgb-contexts",
        "/Users/chenyixuan/ReverseWork/DxOFilmPack/evidence/raw-fidelity-20261103/native")
}
tasks.named("check") { dependsOn(verifyRawRgbContext) }

val verifyRawProviderBw = tasks.register<JavaExec>("verifyRawProviderBw") {
    dependsOn(verification.classesTaskName)
    classpath = verification.runtimeClasspath
    mainClass.set("dev.cxo.filmpack.core.RawProviderBwVerificationKt")
    args("/Users/chenyixuan/ReverseWork/DxOFilmPack/evidence/raw-fidelity-20261103/native",
        "core/local-data/raw-provider-bw-staging")
}
tasks.named("check") { dependsOn(verifyRawProviderBw) }

val verifyRawNegativeProvider = tasks.register<JavaExec>("verifyRawNegativeProvider") {
    dependsOn(verification.classesTaskName)
    classpath = verification.runtimeClasspath
    mainClass.set("dev.cxo.filmpack.core.RawNegativeProviderVerificationKt")
    args("core/local-data", "/Users/chenyixuan/ReverseWork/DxOFilmPack/evidence/raw-fidelity-20261103/native",
        "/Users/chenyixuan/ReverseWork/DxOFilmPack/evidence/raw-fidelity-20261103/reference/raw-negative-provider-index.tsv",
        "core/build/verification/raw-negative-provider-batch.json",
        "/Users/chenyixuan/ReverseWork/DxOFilmPack/evidence/raw-fidelity-20261103/reference/raw-provider-static-curves")
}

val verifyRawRenderingParameters = tasks.register<JavaExec>("verifyRawRenderingParameters") {
    dependsOn(verification.classesTaskName)
    classpath = verification.runtimeClasspath
    mainClass.set("dev.cxo.filmpack.core.RawRenderingParametersVerificationKt")
    args("core/local-data/raw-provider-parameters/negative-provider.cxorp")
}
tasks.named("check") { dependsOn(verifyRawRenderingParameters) }

val verifyRawTargetFidelity = tasks.register<JavaExec>("verifyRawTargetFidelity") {
    dependsOn(verification.classesTaskName)
    classpath = verification.runtimeClasspath
    mainClass.set("dev.cxo.filmpack.core.RawTargetFidelityVerificationKt")
    args("core/local-data/raw-target-fidelity.golden", "core/local-data/raw-negative-providers/catalog.cxornp",
        "/Users/chenyixuan/ReverseWork/DxOFilmPack/evidence/raw-fidelity-20261103/reference/raw-negative-provider-index.tsv")
}
tasks.named("check") { dependsOn(verifyRawTargetFidelity) }

val verifyRawNegativeProviderLifecycle = tasks.register<JavaExec>("verifyRawNegativeProviderLifecycle") {
    dependsOn(verification.classesTaskName)
    classpath = verification.runtimeClasspath
    mainClass.set("dev.cxo.filmpack.core.RawNegativeProviderVerificationKt")
    args("core/local-data", "/Users/chenyixuan/ReverseWork/DxOFilmPack/evidence/raw-fidelity-20261103/native",
        "/Users/chenyixuan/ReverseWork/DxOFilmPack/evidence/raw-fidelity-20261103/reference/raw-negative-provider-lifecycle-index.tsv",
        "core/build/verification/raw-negative-provider-lifecycle.json",
        "/Users/chenyixuan/ReverseWork/DxOFilmPack/evidence/raw-fidelity-20261103/reference/raw-provider-static-curves", "136")
}

val verifyRawScaleFilterStage = tasks.register<JavaExec>("verifyRawScaleFilterStage") {
    dependsOn(verification.classesTaskName)
    classpath = verification.runtimeClasspath
    mainClass.set("dev.cxo.filmpack.core.RawScaleFilterStageVerificationKt")
    args("/Users/chenyixuan/ReverseWork/DxOFilmPack/evidence/raw-fidelity-20261103/native",
        "/Users/chenyixuan/ReverseWork/DxOFilmPack/evidence/raw-fidelity-20261103/reference/negative-stage-oracle-index.tsv",
        "core/build/verification/raw-scale-filter-stage.json")
}
tasks.named("check") { dependsOn(verifyRawScaleFilterStage) }

val verifyRawRenderingScales = tasks.register<JavaExec>("verifyRawRenderingScales") {
    dependsOn(verification.classesTaskName)
    classpath = verification.runtimeClasspath
    mainClass.set("dev.cxo.filmpack.core.RawRenderingScalesVerificationKt")
    args("/Users/chenyixuan/ReverseWork/DxOFilmPack/evidence/raw-fidelity-20261103/reference/raw-rendering-scales-oracle.tsv",
        "core/build/verification/raw-rendering-scales.json")
}
tasks.named("check") { dependsOn(verifyRawRenderingScales) }

val verifyRawColorIntentEstimator = tasks.register<JavaExec>("verifyRawColorIntentEstimator") {
    dependsOn(verification.classesTaskName)
    classpath = verification.runtimeClasspath
    mainClass.set("dev.cxo.filmpack.core.RawColorIntentEstimatorVerificationKt")
    val reference = "/Users/chenyixuan/ReverseWork/DxOFilmPack/evidence/raw-fidelity-20261103/reference"
    args("/Users/chenyixuan/ReverseWork/DxOFilmPack/evidence/raw-fidelity-20261103/native",
        "$reference/negative-intent-general-oracle-index.tsv",
        "$reference/negative-subsample-oracle-index.tsv",
        "core/build/verification/raw-color-intent.json",
        "$reference/negative-intent-scene-oracle-index.tsv")
}
tasks.named("check") {
    dependsOn(verifyRawColorIntentEstimator)
    // All 136 full Apply oracles now pass, including the 21 optional-stage films.
    dependsOn(verifyRawNegativeProviderLifecycle)
}

val verifyRawS9Profile = tasks.register<JavaExec>("verifyRawS9Profile") {
    dependsOn(verification.classesTaskName)
    classpath = verification.runtimeClasspath
    mainClass.set("dev.cxo.filmpack.core.RawS9ProfileVerificationKt")
    val reference = "/Users/chenyixuan/ReverseWork/DxOFilmPack/evidence/raw-fidelity-20261103/reference"
    args("core/local-data", "$reference/s9-native-fidelity-oracle.tsv", "$reference/s9-native-wb-oracle.tsv",
        "core/build/verification/raw-s9-profile.json")
}
tasks.named("check") { dependsOn(verifyRawS9Profile) }

val verifyRawStaticProviders = tasks.register<JavaExec>("verifyRawStaticProviders") {
    dependsOn(verification.classesTaskName)
    classpath = verification.runtimeClasspath
    mainClass.set("dev.cxo.filmpack.core.RawStaticProviderVerificationKt")
    val evidence = "/Users/chenyixuan/ReverseWork/DxOFilmPack/evidence/raw-fidelity-20261103"
    args("core/local-data", "$evidence/native", "$evidence/reference/raw-static-provider-oracle-index.tsv",
        "core/build/verification/raw-static-providers.json")
}
tasks.named("check") { dependsOn(verifyRawStaticProviders) }

val verifyRawRenderingIntent = tasks.register<JavaExec>("verifyRawRenderingIntent") {
    dependsOn(verification.classesTaskName)
    classpath = verification.runtimeClasspath
    mainClass.set("dev.cxo.filmpack.core.RawRenderingIntentVerificationKt")
    val evidence = "/Users/chenyixuan/ReverseWork/DxOFilmPack/evidence/raw-fidelity-20261103"
    args("$evidence/native", "$evidence/reference/rendering-intent-oracle-index.tsv",
        "core/build/verification/raw-rendering-intent.json")
}
tasks.named("check") { dependsOn(verifyRawRenderingIntent) }

val verifyRawCameraBwProviders = tasks.register<JavaExec>("verifyRawCameraBwProviders") {
    dependsOn(verification.classesTaskName)
    classpath = verification.runtimeClasspath
    mainClass.set("dev.cxo.filmpack.core.RawCameraBwProviderVerificationKt")
    val evidence = "/Users/chenyixuan/ReverseWork/DxOFilmPack/evidence/raw-fidelity-20261103"
    args("$evidence/reference/camera-bw-static-fixture-v1.bin", "$evidence/native",
        "core/build/verification/raw-camera-bw-providers.json")
}
tasks.named("check") { dependsOn(verifyRawCameraBwProviders) }

val verifyRawSpecialStaticProviders = tasks.register<JavaExec>("verifyRawSpecialStaticProviders") {
    dependsOn(verification.classesTaskName)
    classpath = verification.runtimeClasspath
    mainClass.set("dev.cxo.filmpack.core.RawSpecialStaticProviderVerificationKt")
    val evidence = "/Users/chenyixuan/ReverseWork/DxOFilmPack/evidence/raw-fidelity-20261103"
    args("core/local-data", "$evidence/native", "$evidence/reference/specialized-hsv-bw-native-oracle-index.tsv",
        "$evidence/reference/rgb-specialized-native-oracle-index.tsv",
        "core/build/verification/raw-special-static-providers.json")
}
tasks.named("check") { dependsOn(verifyRawSpecialStaticProviders) }

val verifyRawStrength = tasks.register<JavaExec>("verifyRawStrength") {
    dependsOn(verification.classesTaskName)
    classpath = verification.runtimeClasspath
    mainClass.set("dev.cxo.filmpack.core.RawStrengthVerificationKt")
    args("core/local-data", "/Users/chenyixuan/ReverseWork/DxOFilmPack/evidence/raw-fidelity-20261103/native",
        "core/build/verification/raw-strength.json")
}
tasks.named("check") { dependsOn(verifyRawStrength) }

val verifyPostProcess = tasks.register<JavaExec>("verifyPostProcess") {
    dependsOn(verification.classesTaskName)
    classpath = verification.runtimeClasspath
    mainClass.set("dev.cxo.filmpack.core.PostProcessVerificationKt")
}
tasks.named("check") { dependsOn(verifyPostProcess) }

val verifyNativeEdgeBlend = tasks.register<JavaExec>("verifyNativeEdgeBlend") {
    dependsOn(verification.classesTaskName)
    classpath = verification.runtimeClasspath
    mainClass.set("dev.cxo.filmpack.core.NativeEdgeBlendVerificationKt")
    args("core/local-data/native-edge-blend.golden", "core/local-data/native-edge-bilinear.golden")
}
tasks.named("check") { dependsOn(verifyNativeEdgeBlend) }

val verifyNativeEdgePlacement = tasks.register<JavaExec>("verifyNativeEdgePlacement") {
    dependsOn(verification.classesTaskName)
    classpath = verification.runtimeClasspath
    mainClass.set("dev.cxo.filmpack.core.NativeEdgePlacementVerificationKt")
}
tasks.named("check") { dependsOn(verifyNativeEdgePlacement) }

val verifyDngColorStage = tasks.register<JavaExec>("verifyDngColorStage") {
    dependsOn(verification.classesTaskName)
    classpath = verification.runtimeClasspath
    mainClass.set("dev.cxo.filmpack.core.DngColorStageVerificationKt")
    args("/Users/chenyixuan/ReverseWork/DxOFilmPack/evidence/apple-proraw-native-20261106")
}
tasks.named("check") { dependsOn(verifyDngColorStage) }
