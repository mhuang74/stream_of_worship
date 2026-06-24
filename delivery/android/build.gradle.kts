plugins {
    id("com.android.application") version "8.7.3" apply false
    id("org.jetbrains.kotlin.android") version "2.0.21" apply false
    id("org.jetbrains.kotlin.plugin.compose") version "2.0.21" apply false
    id("org.jetbrains.kotlin.plugin.serialization") version "2.0.21" apply false
    id("org.jetbrains.kotlinx.kover") version "0.8.3" apply false
}

subprojects {
    tasks.withType<Test>().configureEach {
        maxParallelForks = 1
        systemProperty("user.language", "en")
        systemProperty("user.country", "US")
        systemProperty("user.timezone", "UTC")
        testLogging {
            events("failed", "skipped")
            exceptionFormat = org.gradle.api.tasks.testing.logging.TestExceptionFormat.FULL
        }
    }
}
