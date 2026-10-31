// 依赖仓库配置
//
// ★ 这里刻意**只保留官方源**。
//   开发时曾把阿里云镜像放在最前面（国内网络下载快），但那对公开仓库是错的：
//   GitHub Actions 的 runner 在境外，会先逐个尝试这些镜像、超时后才回落到官方源，
//   明显拖慢 CI；境外贡献者也会遇到同样问题。
//
//   如果你在国内且下载慢，**不要改这个文件**，改用 Gradle 的用户级初始化脚本
//   （~/.gradle/init.gradle.kts）加镜像。那样只影响你自己的机器，不会推给别人。
//   参考：https://docs.gradle.org/current/userguide/init_scripts.html

pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
    }
}

rootProject.name = "NXConnect"
include(":app")
