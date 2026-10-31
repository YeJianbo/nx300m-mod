import sys
from pathlib import Path

viewer_path = Path("mod/nxconnect/NXConnect/app/src/main/java/com/nxwa/nxconnect/ui/ViewerActivity.kt")
content = viewer_path.read_text(encoding="utf-8")

old_render_chrome = """    private fun renderChrome() {
        val alpha = chromeReveal * (1f - progress * 2.5f).coerceIn(0f, 1f)
        b.bottomChrome.translationY = dp(80) * (1f - chromeReveal) + dp(90) * progress
        b.backGlass.translationY = -dp(30) * (1f - chromeReveal) - dp(30) * progress
        listOf(b.bottomChrome, b.backGlass).forEach { view ->
            view.alpha = alpha
            view.visibility = if (alpha > .001f) View.VISIBLE else View.INVISIBLE
            view.importantForAccessibility = if (alpha > .5f) View.IMPORTANT_FOR_ACCESSIBILITY_AUTO
                else View.IMPORTANT_FOR_ACCESSIBILITY_NO_HIDE_DESCENDANTS
        }
        b.actionDock.invalidate()
        b.backGlass.invalidate()
    }"""

new_render_chrome = """    private fun renderChrome() {
        val alpha = chromeReveal * (1f - progress * 2.5f).coerceIn(0f, 1f)
        b.bottomChrome.translationY = dp(80) * (1f - chromeReveal) + dp(90) * progress
        b.backGlass.translationY = -dp(30) * (1f - chromeReveal) - dp(30) * progress
        b.filmstrip.alpha = (1f - progress * 3.0f).coerceIn(0f, 1f)
        b.filmstrip.translationY = dp(24) * progress
        listOf(b.bottomChrome, b.backGlass).forEach { view ->
            view.alpha = alpha
            view.visibility = if (alpha > .001f) View.VISIBLE else View.INVISIBLE
            view.importantForAccessibility = if (alpha > .5f) View.IMPORTANT_FOR_ACCESSIBILITY_AUTO
                else View.IMPORTANT_FOR_ACCESSIBILITY_NO_HIDE_DESCENDANTS
        }
        b.actionDock.invalidate()
        b.backGlass.invalidate()
    }"""

assert old_render_chrome in content, "old_render_chrome not found"
content = content.replace(old_render_chrome, new_render_chrome)

old_motion = """        motion = ValueAnimator.ofFloat(progress, target).apply {
            duration = (100 + kotlin.math.abs(target - progress) * 180).toLong()
            interpolator = PathInterpolator(0.22f, 0.25f, 0f, 1f)
            addUpdateListener { setProgress(it.animatedValue as Float) }
            start()
        }"""

new_motion = """        motion = ValueAnimator.ofFloat(progress, target).apply {
            duration = (140 + kotlin.math.abs(target - progress) * 200).toLong()
            interpolator = PathInterpolator(0.22f, 1f, 0.36f, 1f)
            addUpdateListener { setProgress(it.animatedValue as Float) }
            start()
        }"""

assert old_motion in content, "old_motion not found"
content = content.replace(old_motion, new_motion)

old_chrome_motion = """        chromeMotion = ValueAnimator.ofFloat(chromeReveal, target).apply {
            duration = (80 + kotlin.math.abs(target - chromeReveal) * 100).toLong()
            interpolator = PathInterpolator(.2f, 0f, 0f, 1f)
            addUpdateListener { chromeReveal = it.animatedValue as Float; renderChrome() }
            start()
        }"""

new_chrome_motion = """        chromeMotion = ValueAnimator.ofFloat(chromeReveal, target).apply {
            duration = (100 + kotlin.math.abs(target - chromeReveal) * 120).toLong()
            interpolator = PathInterpolator(0.22f, 1f, 0.36f, 1f)
            addUpdateListener { chromeReveal = it.animatedValue as Float; renderChrome() }
            start()
        }"""

assert old_chrome_motion in content, "old_chrome_motion not found"
content = content.replace(old_chrome_motion, new_chrome_motion)

viewer_path.write_text(content, encoding="utf-8")
print("ViewerActivity.kt successfully updated!")
