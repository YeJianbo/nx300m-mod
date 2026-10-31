// SPDX-License-Identifier: AGPL-3.0-or-later
package com.nxwa.nxconnect.ui

import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.ProgressBar
import android.widget.TextView
import androidx.recyclerview.widget.RecyclerView
import androidx.viewpager2.widget.ViewPager2
import com.nxwa.nxconnect.R
import com.nxwa.nxconnect.data.MediaGroup
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch

/** Full embedded previews share the bounded cache and EXIF handling with the grid. */
class ImagePagerAdapter(private val images: List<MediaGroup>, private val onInfo: (MediaGroup) -> Unit, private val onTap: () -> Unit, private val onDrag: (Float, Boolean, Float, Boolean) -> Unit, private val onReady: () -> Unit) : RecyclerView.Adapter<ImagePagerAdapter.VH>() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private var disposed = false

    inner class VH(view: View) : RecyclerView.ViewHolder(view) {
        val image: ZoomImageView = view.findViewById(R.id.image)
        val progress: ProgressBar = view.findViewById(R.id.progress)
        val error: TextView = view.findViewById(R.id.error)
        var job: Job? = null
        var boundKey: String? = null
        var bindGeneration = 0
    }

    override fun getItemCount(): Int = images.size

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): VH =
        VH(LayoutInflater.from(parent.context).inflate(R.layout.item_pager_image, parent, false))

    override fun onBindViewHolder(holder: VH, position: Int) {
        val group = images[position]
        holder.image.onSwipeUp = { onInfo(group) }
        holder.image.onSingleTap = onTap
        holder.image.onVerticalDrag = onDrag
        holder.job?.cancel()
        holder.image.animate().cancel()
        holder.image.alpha = 1f
        holder.boundKey = group.key
        val generation = ++holder.bindGeneration
        fun isCurrent() = !disposed && holder.boundKey == group.key && holder.bindGeneration == generation
        holder.image.setImageDrawable(null)
        holder.error.visibility = View.GONE
        val cached = ThumbLoader.cached(group, 2048, full = true)
        if (cached != null) {
            holder.image.setImageBitmap(cached)
            holder.progress.visibility = View.GONE
            holder.image.post { if (isCurrent()) onReady() }
            return
        }
        val thumbnail = ThumbLoader.peekAny(group)
        if (thumbnail != null) {
            holder.image.setImageBitmap(thumbnail)
            holder.progress.visibility = View.GONE
            holder.image.post { if (isCurrent()) onReady() }
        } else holder.progress.visibility = View.VISIBLE
        holder.job = scope.launch {
            val bitmap = ThumbLoader.load(group, 2048, full = true)
            if (!isCurrent()) return@launch
            holder.progress.visibility = View.GONE
            if (bitmap != null) {
                holder.image.setImageBitmap(bitmap)
                if (thumbnail == null && viewerAnimationsEnabled(holder.image.context)) {
                    holder.image.alpha = 0f
                    holder.image.animate().alpha(1f).setDuration(150).setInterpolator(viewerMotionInterpolator()).start()
                }
                holder.image.post { if (isCurrent()) onReady() }
            }
            else {
                holder.error.text = if (thumbnail != null) "正在显示缩略图，完整预览暂不可用，可下载原文件。"
                    else "暂无可用预览，可下载原文件。"
                holder.error.visibility = View.VISIBLE
            }
        }
    }

    override fun onViewRecycled(holder: VH) {
        holder.job?.cancel()
        holder.image.animate().cancel()
        holder.bindGeneration++
        holder.boundKey = null
        holder.image.setImageDrawable(null)
        super.onViewRecycled(holder)
    }

    fun dispose() {
        disposed = true
        scope.cancel()
    }

    private fun currentImage(pager: ViewPager2): ZoomImageView? =
        ((pager.getChildAt(0) as? RecyclerView)?.findViewHolderForAdapterPosition(pager.currentItem) as? VH)?.image

    fun resetCurrentZoom(pager: ViewPager2, animate: Boolean): Boolean = currentImage(pager)?.resetZoom(animate) == true

    fun cancelInteraction(pager: ViewPager2) = currentImage(pager)?.cancelInteraction()

    fun fittedPhotoHeight(pager: ViewPager2): Float? = currentImage(pager)?.fittedPhotoHeight

    fun resetZoomExcept(position: Int, pager: ViewPager2) {
        val recycler = pager.getChildAt(0) as? RecyclerView ?: return
        for (i in 0 until recycler.childCount) {
            val holder = recycler.getChildViewHolder(recycler.getChildAt(i)) as? VH ?: continue
            if (holder.bindingAdapterPosition != position) holder.image.resetZoom(false)
        }
    }
}
