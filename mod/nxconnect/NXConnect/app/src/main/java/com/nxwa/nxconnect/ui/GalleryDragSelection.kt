// SPDX-License-Identifier: AGPL-3.0-or-later
package com.nxwa.nxconnect.ui

import android.view.MotionEvent
import android.view.View
import androidx.core.view.ViewCompat
import androidx.recyclerview.widget.RecyclerView

/** A reversible range from the long-pressed tile; date rows do not count as photos. */
internal class GalleryDragSelection(
    private val list: RecyclerView,
    private val adapter: MediaAdapter,
) : RecyclerView.SimpleOnItemTouchListener(), View.OnAttachStateChangeListener {
    private var pointerDown = false
    private var active = false
    private var anchor = RecyclerView.NO_POSITION
    private var endpoint = RecyclerView.NO_POSITION
    private var version = 0
    private var before = emptySet<String>()
    private var selecting = true
    private var x = 0f
    private var y = 0f
    private val density = list.resources.displayMetrics.density
    private val scroll = object : Runnable {
        override fun run() {
            if (!active) return
            if (adapter.dataVersion != version || list.adapter !== adapter) { stop(); return }
            val edge = 56f * density
            val top = list.paddingTop.toFloat()
            val bottom = (list.height - list.paddingBottom).toFloat().coerceAtLeast(top + edge)
            val amount = when {
                y < top + edge -> -((top + edge - y) / edge).coerceIn(0f, 1f)
                y > bottom - edge -> ((y - bottom + edge) / edge).coerceIn(0f, 1f)
                else -> 0f
            }
            val delta = (24f * density * amount * kotlin.math.abs(amount)).toInt()
            if (delta != 0 && list.canScrollVertically(if (delta < 0) -1 else 1)) {
                list.scrollBy(0, delta)
                extend()
            }
            ViewCompat.postOnAnimation(list, this)
        }
    }

    init {
        list.addOnItemTouchListener(this)
        list.addOnAttachStateChangeListener(this)
    }

    /** Called by the tile's native long-click, which also preserves accessibility long-click. */
    fun start(position: Int): Boolean {
        if (!pointerDown || list.adapter !== adapter || adapter.mediaAt(position) == null) return false
        list.stopScroll()
        before = adapter.selected.toSet()
        selecting = adapter.mediaAt(position)!!.key !in before
        anchor = position
        endpoint = position
        version = adapter.dataVersion
        active = true
        list.parent?.requestDisallowInterceptTouchEvent(true)
        adapter.applyDragSelection(anchor, endpoint, before, selecting)
        list.removeCallbacks(scroll)
        ViewCompat.postOnAnimation(list, scroll)
        return true
    }

    override fun onInterceptTouchEvent(rv: RecyclerView, event: MotionEvent): Boolean {
        if (event.actionMasked == MotionEvent.ACTION_DOWN) {
            stop()
            pointerDown = true
            x = event.x; y = event.y
        }
        if (active) {
            // RecyclerView may deliver the takeover event only to interception.
            // Apply it now so the first move, or a direct release, is not lost.
            handleTouch(event)
            return true
        }
        if (event.actionMasked == MotionEvent.ACTION_UP || event.actionMasked == MotionEvent.ACTION_CANCEL) pointerDown = false
        return false
    }

    override fun onTouchEvent(rv: RecyclerView, event: MotionEvent) {
        handleTouch(event)
    }

    private fun handleTouch(event: MotionEvent) {
        if (!active) return
        x = event.x; y = event.y
        when (event.actionMasked) {
            MotionEvent.ACTION_MOVE -> extend()
            MotionEvent.ACTION_UP -> { extend(); stop() }
            MotionEvent.ACTION_CANCEL, MotionEvent.ACTION_POINTER_DOWN -> stop()
        }
    }

    private fun extend() {
        if (!active) return
        if (adapter.dataVersion != version || list.adapter !== adapter) { stop(); return }
        val safeX = x.coerceIn(0f, (list.width - 1).coerceAtLeast(0).toFloat())
        val safeY = y.coerceIn(list.paddingTop.toFloat(), (list.height - list.paddingBottom - 1).coerceAtLeast(list.paddingTop).toFloat())
        val child = list.findChildViewUnder(safeX, safeY) ?: return
        val position = list.getChildAdapterPosition(child)
        if (position == endpoint || adapter.mediaAt(position) == null) return
        endpoint = position
        adapter.applyDragSelection(anchor, endpoint, before, selecting)
    }

    fun stop() {
        active = false
        pointerDown = false
        before = emptySet()
        list.removeCallbacks(scroll)
        list.parent?.requestDisallowInterceptTouchEvent(false)
    }

    override fun onViewAttachedToWindow(view: View) = Unit
    override fun onViewDetachedFromWindow(view: View) = stop()
}
