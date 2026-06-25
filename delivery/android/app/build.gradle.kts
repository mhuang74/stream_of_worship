plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
    id("org.jetbrains.kotlin.plugin.serialization")
    id("org.jetbrains.kotlinx.kover")
}

android {
    namespace = "org.streamofworship.android"
    compileSdk = 35

    defaultConfig {
        applicationId = "org.streamofworship.android"
        minSdk = 26
        targetSdk = 35
        versionCode = 1
        versionName = "0.1.0"

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }

    buildTypes {
        debug {
            applicationIdSuffix = ".debug"
            versionNameSuffix = "-debug"
            buildConfigField("String", "API_BASE_URL", quotedProperty("sow.apiBaseUrl.debug"))
            buildConfigField("String", "BUILD_VARIANT", "\"debug\"")
        }
        create("staging") {
            initWith(getByName("debug"))
            matchingFallbacks += listOf("debug")
            applicationIdSuffix = ".staging"
            versionNameSuffix = "-staging"
            buildConfigField("String", "API_BASE_URL", quotedProperty("sow.apiBaseUrl.staging"))
            buildConfigField("String", "BUILD_VARIANT", "\"staging\"")
        }
        release {
            isMinifyEnabled = false
            buildConfigField("String", "API_BASE_URL", quotedProperty("sow.apiBaseUrl.release"))
            buildConfigField("String", "BUILD_VARIANT", "\"release\"")
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro",
            )
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlin {
        jvmToolchain(17)
    }

    buildFeatures {
        buildConfig = true
        compose = true
    }

    testOptions {
        unitTests {
            isIncludeAndroidResources = true
            all {
                it.maxParallelForks = 1
                it.systemProperty("user.language", "en")
                it.systemProperty("user.country", "US")
                it.systemProperty("user.timezone", "UTC")
            }
        }
    }
}

kover {
    reports {
        filters {
            excludes {
                classes(
                    "*.*\$*",
                    "*.BuildConfig",
                    "*.MainActivity",
                    "*.SowApplication",
                    "*.core.design.SowAppKt",
                    "*.core.design.SowStatesKt",
                    "*.core.navigation.*Dependencies",
                    "*.core.navigation.SowNavGraphKt",
                    "*.core.session.AndroidSecureSessionCookieStore",
                    "*.feature.*.*ScreenKt",
                    "*.feature.player.Media3PlayerController",
                    "*.feature.share.AndroidShareIntentsKt",
                )
            }
        }
        total {
            xml {
                onCheck = false
            }
        }
    }
}

dependencies {
    val composeBom = platform("androidx.compose:compose-bom:2024.12.01")

    implementation(composeBom)
    androidTestImplementation(composeBom)
    testImplementation(composeBom)

    implementation("androidx.activity:activity-compose:1.9.3")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.material:material-icons-extended")
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.core:core-ktx:1.15.0")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.8.7")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.7")
    implementation("androidx.navigation:navigation-compose:2.8.5")
    implementation("androidx.security:security-crypto:1.1.0-alpha06")
    implementation("androidx.work:work-runtime-ktx:2.10.0")
    implementation("androidx.media3:media3-exoplayer:1.5.1")
    implementation("androidx.media3:media3-session:1.5.1")
    implementation("androidx.media3:media3-ui:1.5.1")
    implementation("com.jakewharton.retrofit:retrofit2-kotlinx-serialization-converter:1.0.0")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("com.squareup.retrofit2:retrofit:2.11.0")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.9.0")
    implementation("org.jetbrains.kotlinx:kotlinx-serialization-json:1.7.3")

    debugImplementation("androidx.compose.ui:ui-tooling")

    testImplementation("androidx.compose.ui:ui-test-junit4")
    testImplementation("androidx.test:core:1.6.1")
    testImplementation("androidx.test.ext:junit:1.2.1")
    testImplementation("junit:junit:4.13.2")
    testImplementation("com.squareup.okhttp3:mockwebserver:4.12.0")
    testImplementation("org.jetbrains.kotlinx:kotlinx-coroutines-test:1.9.0")
    testImplementation("org.robolectric:robolectric:4.14.1")
}

fun Project.quotedProperty(name: String): String {
    val value = providers.gradleProperty(name).get()
    return "\"${value.trimEnd('/')}\""
}
