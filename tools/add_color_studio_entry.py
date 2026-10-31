import sys
from pathlib import Path

# 1. Update GalleryActivity.kt
gallery_path = Path("mod/nxconnect/NXConnect/app/src/main/java/com/nxwa/nxconnect/ui/GalleryActivity.kt")
g_content = gallery_path.read_text(encoding="utf-8")

old_menu = """    private fun menu() {
        GalleryChrome.menu(this, b.tabMenu, listOf(
            "刷新照片" to { load() },
            "批量补充镜头源信息" to { photoPicker.launch(arrayOf("image/jpeg")) },
            "远程拍摄遥控" to { startActivity(Intent(this, RemoteActivity::class.java)) },
            "连接与外观设置" to { finish() }
        ))
    }"""

new_menu = """    private fun menu() {
        GalleryChrome.menu(this, b.tabMenu, listOf(
            "色彩工作台 & 机身设置" to { NxApp.camera?.let { cam -> ExposureSettingsDialog(this, cam) {}.show() } },
            "刷新照片" to { load() },
            "批量补充镜头源信息" to { photoPicker.launch(arrayOf("image/jpeg")) },
            "远程拍摄遥控" to { startActivity(Intent(this, RemoteActivity::class.java)) },
            "连接与外观设置" to { finish() }
        ))
    }"""

assert old_menu in g_content, "old_menu not found in GalleryActivity.kt"
g_content = g_content.replace(old_menu, new_menu)
gallery_path.write_text(g_content, encoding="utf-8")
print("GalleryActivity.kt successfully updated!")

# 2. Update ViewerActivity.kt
viewer_path = Path("mod/nxconnect/NXConnect/app/src/main/java/com/nxwa/nxconnect/ui/ViewerActivity.kt")
v_content = viewer_path.read_text(encoding="utf-8")

old_v_more = """        b.btnMore.setOnClickListener {
            GalleryChrome.menu(this, it, listOf(
                "详细信息" to { showDetails(true) },
                "编辑镜头信息" to { edit() },
                "还原信息修改" to { restoreEdits() },
                "下载原文件" to { download() }
            ))
        }"""

new_v_more = """        b.btnMore.setOnClickListener {
            GalleryChrome.menu(this, it, listOf(
                "详细信息" to { showDetails(true) },
                "色彩工作台 & 机身设置" to { NxApp.camera?.let { cam -> ExposureSettingsDialog(this, cam) {}.show() } },
                "编辑镜头信息" to { edit() },
                "还原信息修改" to { restoreEdits() },
                "下载原文件" to { download() }
            ))
        }"""

assert old_v_more in v_content, "old_v_more not found in ViewerActivity.kt"
v_content = v_content.replace(old_v_more, new_v_more)
viewer_path.write_text(v_content, encoding="utf-8")
print("ViewerActivity.kt successfully updated!")
