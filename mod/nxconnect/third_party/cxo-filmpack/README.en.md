<div align="center">

# CXO FilmPack

### Start with RAW. Finish with film.

An Android photo editor for RAW development, film color, grain and finishing.

[](https://github.com/Macchen666816/CXO-FilmPack/releases/latest)


[](LICENSE)

**[Download 1.0.1](https://github.com/Macchen666816/CXO-FilmPack/releases/tag/v1.0.1)** · [简体中文](README.md) · [Release notes](docs/releases/1.0.1.md) · [Report an issue](https://github.com/Macchen666816/CXO-FilmPack/issues)

</div>

Develop camera RAW files or edit JPEG, PNG and HEIC on your Android device. Processing stays on your device; photos do not need to be uploaded.

## RAW examples

These comparisons come from CXO's actual RAW processing pipeline. Each group shows the same RAW file as **Original, Kodak Portra 400 and Kodak Elite Color 400**, in that order. Film strength is 100, with no additional grain or post-processing.

### Panasonic RAW · P1176257.RW2



### Nikon RAW · DSC_3271.NEF



### Sony RAW · DSC07319.ARW



### The editor

<p align="center">
  
</p>

## Features

- **Film colors:** 153 films and styles, with search, favorites, quick switching and adjustable strength.
- **RAW development:** camera-linear data processed with camera calibration, white balance, color matrices and RAW film curves.
- **Apple ProRAW:** an independent linear development path using the embedded DNG profile, followed by the RAW film core.
- **Grain and adjustments:** 99 bundled grain types, exposure, contrast and other adjustments, plus edge effects.
- **Preview and comparison:** interactive adjustments, press-and-hold comparison, pinch zoom and double-tap reset.
- **Export:** JPEG at quality 1–100 or uncompressed RGB8 TIFF, at original or reduced dimensions.

Photos are processed on the device and do not need to be uploaded to a server.

## Install

Download the **1.0.1** APK from this repository's **Releases** page and allow installation from your chosen source. Users of the signed stable build can update in place and keep their app data.

High-resolution RAW files need free memory and storage. The first decode usually takes longer than subsequent film changes. Progress, timing and errors appear at the bottom of the editor.

## Format support

| Input | Support |
| --- | --- |
| JPEG / PNG | Direct photo editing |
| Camera RAW | Requires LibRaw support for the file mode and matching camera / film adaptation rules |
| Apple ProRAW | Validated with iPhone 15 Pro Max three-channel Linear DNG; JPEG XL, HueSat/Look tables and triple-illuminant profiles are currently unsupported |
| HEIC | Uses the system decoder on Android 9.0+, then converts internally to an 8-bit sRGB JPEG |

Actual import and export testing includes Panasonic S1RII / S9, Sony A7 IV and Nikon Z6 II. The calibration catalog contains 584 queryable camera profiles; compression mode, bit depth and adaptation rules still determine whether a particular file is supported. Missing adaptations produce an explicit unsupported message.

Exports are SDR sRGB. TIFF output is 8-bit RGB; 16-bit and HDR export are not available. Seven of the 153 film / style models are experimental.

## What's new in 1.0.1

- Fixed Apple ProRAW DNG import failures.
- Switched to linear ProRAW development and RAW film rendering, correcting visible color differences at import and after film selection.
- Removed redundant interface captions while retaining progress, timing and error messages.

Regression testing on a K40 and an ARM64 emulator covered import, films, grain, adjustments, JPEG / TIFF export and cancellation. For IMG_3046, Original and Portra 400 achieved channel RMSE values of 0.30 and 0.39 / 255 against the native DxO output at matching coordinates in a common sRGB space. See the [1.0.1 technical record](docs/1.0.1-Apple-ProRAW.md) for the method, sample scope and remaining differences.

## Build and source layout

The project uses Kotlin, Android NDK and CMake. Android Studio's bundled JBR is recommended; Gradle files specify the SDK and NDK versions.

Before the first build, download `native-grain8-v1.cxograinpack` from the Release and place it in `releases/` at the project root. This resource pack is not stored in Git.

```sh
# Development build; no stable signing key required
./gradlew :app:assembleDebug

# Core verification
./gradlew :core:check

# Stable build; configure your own signing key first
./gradlew :app:assembleStable
```

| Directory | Contents |
| --- | --- |
| `app` | Editor, import / export, RAW / HEIC bridges and native code |
| `core` | Film, RAW, grain, color management and adjustment algorithms |
| `android-adapter` | Android image and platform interfaces |
| `glass-catalog` | Glass interface components |
| `docs` | Technical records and demo images |

The stable APK is written to `app/build/outputs/apk/stable/app-stable.apk`. See the [release guide](release/README.md) for signing configuration. Keys and passwords are not included. `stable` is the only release line; `preview` and `publicBeta` are archived.

Some numerical checks depend on native reference evidence held on the development machine. Running the complete `:core:check` suite after cloning requires those research datasets; building the app does not require running that suite.

## License and acknowledgements

Original project code and materials are covered by the [non-commercial license](LICENSE). **Commercial use without permission is prohibited.** This is a source-available non-commercial license, not an OSI-approved open-source license. Third-party dependencies and resources retain their own terms; see [THIRD_PARTY_NOTICES](THIRD_PARTY_NOTICES).

CXO FilmPack is an independent Android project based on reverse-engineering research of DxO FilmPack 8. It is not affiliated with DxO. Film names, trademarks and third-party resources belong to their respective owners.

Built with projects including LibRaw, Adobe DNG SDK, libjpeg-turbo, Little CMS and Backdrop. This software is based in part on the work of the Independent JPEG Group.
