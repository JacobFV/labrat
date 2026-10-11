plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
    alias(libs.plugins.kotlin.serialization)
}

// Shared repo content packaged into the APK as assets:
//   ../../web    -> assets/web/...      (remote-control UI + touchpad.html, served by the web server)
//   ../../config -> assets/config/...   (robot.json, single source of truth for geometry/joints)
// We copy into a generated directory (instead of adding ../../web directly as an asset srcDir)
// so the files keep a "web/" and "config/" prefix inside the APK.
val sharedAssetsDir = layout.buildDirectory.dir("generated/tappetAssets")
val syncSharedAssets by tasks.registering(Sync::class) {
    from(rootProject.file("../web")) { into("web") }
    from(rootProject.file("../config")) {
        include("robot.json")
        into("config")
    }
    into(sharedAssetsDir)
}

android {
    namespace = "dev.tappet.head"
    compileSdk = 35

    defaultConfig {
        applicationId = "dev.tappet.head"
        minSdk = 26
        targetSdk = 35
        versionCode = 1
        versionName = "0.1.0"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
        }
    }

    sourceSets {
        getByName("main") {
            assets.srcDirs("src/main/assets", sharedAssetsDir)
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
        compose = true
        buildConfig = true
    }
    packaging {
        resources.excludes += setOf("/META-INF/{AL2.0,LGPL2.1}", "META-INF/DEPENDENCIES")
    }
    testOptions {
        unitTests.isReturnDefaultValues = true
    }
}

tasks.named("preBuild") { dependsOn(syncSharedAssets) }

dependencies {
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.lifecycle.runtime.ktx)
    implementation(libs.androidx.lifecycle.runtime.compose)
    implementation(libs.androidx.lifecycle.service)
    implementation(libs.androidx.activity.compose)
    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.compose.ui)
    implementation(libs.androidx.compose.ui.graphics)
    implementation(libs.androidx.compose.ui.tooling.preview)
    implementation(libs.androidx.compose.foundation)
    implementation(libs.androidx.compose.material3)
    implementation(libs.kotlinx.coroutines.android)
    implementation(libs.kotlinx.serialization.json)
    implementation(libs.okhttp)
    implementation(libs.nanohttpd)
    implementation(libs.zxing.core)
    implementation(libs.usb.serial)
    implementation(libs.camerax.core)
    implementation(libs.camerax.camera2)
    implementation(libs.camerax.lifecycle)

    testImplementation(libs.junit)
    testImplementation(libs.kotlinx.coroutines.test)
}
