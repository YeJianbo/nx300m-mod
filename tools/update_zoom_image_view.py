import sys
from pathlib import Path

zoom_image_view_path = Path("mod/nxconnect/NXConnect/app/src/main/java/com/nxwa/nxconnect/ui/ZoomImageView.kt")
content = zoom_image_view_path.read_text(encoding="utf-8")

# 1. 优化双击 1:1 像素靶心验焦
old_double_tap = """            override fun onDoubleTap(e: MotionEvent): Boolean {
                val target = if (scale > baseScale * 1.05f) baseScale else baseScale * 2.5f
                animateZoom(target, e.x, e.y)
                return true
            }"""

new_double_tap = """            override fun onDoubleTap(e: MotionEvent): Boolean {
                // OneUI 9 风格双击验焦：若当前处于放大状态则平滑回弹适屏；若处于适屏则精准靶向 100% 物理像素验焦
                val d = drawable
                val oneToOne = if (d != null && baseScale > 0f) 1.0f else baseScale * 2.8f
                val target = if (scale > baseScale * 1.05f) baseScale else maxOf(baseScale * 2.8f, oneToOne).coerceIn(baseScale, maxScale)
                animateZoom(target, e.x, e.y)
                return true
            }"""

assert old_double_tap in content, "old_double_tap not found"
content = content.replace(old_double_tap, new_double_tap)

# 2. 优化缩放动效插值器为 OneUI 9 流体阻尼贝塞尔曲线
old_interpolator = "interpolator = android.view.animation.PathInterpolator(.2f, 0f, 0f, 1f)"
new_interpolator = "interpolator = android.view.animation.PathInterpolator(0.22f, 1f, 0.36f, 1f)\n            duration = 260"

assert old_interpolator in content, "old_interpolator not found"
# 替换 duration = 240 为 260
content = content.replace("duration = 240\n            " + old_interpolator, new_interpolator)

zoom_image_view_path.write_text(content, encoding="utf-8")
print("ZoomImageView.kt successfully updated!")
