package com.nxwa.nxconnect.ui
object ExifLabels {
    private val labels = mapOf(
        "Make" to "相机制造商", "Model" to "相机型号", "Software" to "软件版本",
        "DateTime" to "修改时间", "DateTimeOriginal" to "拍摄时间", "DateTimeDigitized" to "数字化时间",
        "ExposureTime" to "曝光时间", "FNumber" to "光圈", "PhotographicSensitivity" to "ISO 感光度",
        "ISOSpeedRatings" to "ISO 感光度", "ExposureProgram" to "曝光程序", "ExposureMode" to "曝光模式",
        "ExposureBiasValue" to "曝光补偿", "MeteringMode" to "测光模式", "WhiteBalance" to "白平衡",
        "Flash" to "闪光灯", "FocalLength" to "实际焦距", "FocalLengthIn35mmFilm" to "35mm 等效焦距",
        "LensMake" to "镜头制造商", "LensModel" to "镜头型号", "LensSpecification" to "镜头规格",
        "Orientation" to "方向", "ColorSpace" to "色彩空间", "PixelXDimension" to "像素宽度",
        "PixelYDimension" to "像素高度", "Artist" to "作者", "Copyright" to "版权",
        "GPSLatitude" to "GPS 纬度", "GPSLongitude" to "GPS 经度", "GPSAltitude" to "GPS 海拔",
        "Sharpness" to "锐度", "Saturation" to "饱和度", "Contrast" to "对比度",
        "MakerNote" to "厂商数据", "UserComment" to "用户注释", "ImageDescription" to "照片说明")
    fun name(tag: String) = labels[tag]?.let { "$it · $tag" } ?: tag
}
