plugins {
    id("org.jetbrains.kotlin.jvm") version "2.4.10" apply false
    id("org.jetbrains.kotlin.plugin.compose") version "2.4.10" apply false
    id("com.android.application") version "9.4.0" apply false
    id("com.android.library") version "9.4.0" apply false
}

// Keep the historical root command working after separating the Kotlin core
// from the Android shell.
tasks.register("check") {
    dependsOn(":core:check")
}
