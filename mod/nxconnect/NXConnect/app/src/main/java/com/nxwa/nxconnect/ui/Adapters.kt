// SPDX-License-Identifier: AGPL-3.0-or-later
package com.nxwa.nxconnect.ui

import android.animation.ValueAnimator
import android.graphics.Rect
import android.os.Build
import android.provider.Settings
import android.util.LruCache
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.view.animation.PathInterpolator
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView
import androidx.core.view.ViewCompat
import androidx.recyclerview.widget.DiffUtil
import androidx.recyclerview.widget.ListAdapter
import androidx.recyclerview.widget.RecyclerView
import com.nxwa.nxconnect.R
import com.nxwa.nxconnect.data.MediaGroup
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.text.ParsePosition
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Calendar
import java.util.Locale

class ThumbBinder(private val view: ImageView) {
    private var job: Job? = null
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)

    fun bind(entry: MediaGroup, width: Int, placeholderTransparent: Boolean = true, onLoaded: ((Boolean) -> Unit)? = null) {
        view.tag = entry.key
        job?.cancel()
        val cached = ThumbLoader.cached(entry, width)
        if (cached != null) {
            view.setImageBitmap(cached)
            onLoaded?.invoke(true)
            return
        }
        val preview = ThumbLoader.peekAny(entry)
        if (preview != null) {
            view.setImageBitmap(preview)
            onLoaded?.invoke(true)
        } else if (placeholderTransparent) view.setImageResource(android.R.color.transparent)
        if (entry.isVideo) return
        job = scope.launch {
            val bitmap = try { ThumbLoader.load(entry, width) }
            catch (cancelled: CancellationException) { throw cancelled }
            catch (_: Exception) { null }
            if (view.tag == entry.key) {
                if (bitmap != null) view.setImageBitmap(bitmap)
                onLoaded?.invoke(bitmap != null || preview != null)
            }
        }
    }

    fun clear() {
        job?.cancel()
        view.tag = null
        view.setImageDrawable(null)
    }
}

data class FolderRow(val name: String, val count: Int, val cover: MediaGroup?)

class FolderAdapter(private val onClick: (FolderRow) -> Unit) : ListAdapter<FolderRow, FolderAdapter.VH>(DIFF) {
    companion object {
        private val DIFF = object : DiffUtil.ItemCallback<FolderRow>() {
            override fun areItemsTheSame(a: FolderRow, b: FolderRow) = a.name == b.name
            override fun areContentsTheSame(a: FolderRow, b: FolderRow) = a == b
        }
    }
    class VH(view: View) : RecyclerView.ViewHolder(view) {
        val cover: ImageView = view.findViewById(R.id.cover)
        val name: TextView = view.findViewById(R.id.name)
        val sub: TextView = view.findViewById(R.id.sub)
        val binder = ThumbBinder(cover)
    }
    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): VH =
        VH(LayoutInflater.from(parent.context).inflate(R.layout.item_oneui_gallery_album, parent, false)).also { GalleryChrome.press(it.itemView) }
    override fun onBindViewHolder(holder: VH, position: Int) {
        val row = getItem(position)
        holder.name.text = row.name
        holder.sub.text = "${row.count}"
        holder.itemView.contentDescription = "${row.name}，${row.count} 项"
        holder.itemView.setOnClickListener { onClick(row) }
        row.cover?.let { holder.binder.bind(it, 320) } ?: holder.binder.clear()
    }
    override fun onViewRecycled(holder: VH) { holder.binder.clear(); super.onViewRecycled(holder) }
}

/** Headers are adapter rows, so every date starts on a complete grid row. */
class MediaAdapter(
    private val onClick: (MediaGroup) -> Unit,
    private val onLongClick: (MediaGroup) -> Unit,
    private val onSelectionChanged: () -> Unit,
) : RecyclerView.Adapter<RecyclerView.ViewHolder>() {
    private data class Row(val id: String, val title: String? = null, val entry: MediaGroup? = null, val members: List<MediaGroup> = emptyList())
    private data class ParsedDate(val millis: Long?)
    private data class PreparedRows(val rows: List<Row>, val media: List<MediaGroup>, val keys: Set<String>)
    companion object {
        private const val SELECTION = "selection"
        private val DIFF = object : DiffUtil.ItemCallback<Row>() {
            override fun areItemsTheSame(a: Row, b: Row) = a.id == b.id
            override fun areContentsTheSame(a: Row, b: Row) = a == b
        }
    }
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private var prepareJob: Job? = null
    private var generation = 0
    private var rows = emptyList<Row>()
    private var media = emptyList<MediaGroup>()
    private var mediaKeys = emptySet<String>()
    private var mediaPositions = emptyMap<String, Int>()
    private var groupPositions = emptyMap<String, Int>()
    var dataVersion = 0
        private set
    private val parsedDates = LruCache<String, ParsedDate>(4096)
    val currentList: List<MediaGroup> get() = media
    val mediaCount: Int get() = media.size
    val selected = linkedSetOf<String>()
    val selectedCount: Int get() = selected.size
    var lastOpenedKey: String? = null
        private set
    private var lastOpenedBounds: IntArray? = null
    var selectionMode = false
        private set

    fun sourceBoundsFor(key: String): IntArray? =
        if (key == lastOpenedKey) lastOpenedBounds?.copyOf() else null

    private fun rememberSource(entry: MediaGroup, thumb: View) {
        lastOpenedKey = entry.key
        lastOpenedBounds = null
        val bounds = Rect()
        if (!thumb.isAttachedToWindow || !thumb.isShown || !thumb.getGlobalVisibleRect(bounds) || bounds.isEmpty) return
        // Global visible rectangles use root coordinates; the viewer needs physical screen coordinates.
        val origin = IntArray(2)
        thumb.rootView.getLocationOnScreen(origin)
        bounds.offset(origin[0], origin[1])
        lastOpenedBounds = intArrayOf(bounds.left, bounds.top, bounds.right, bounds.bottom)
    }

    fun submitList(items: List<MediaGroup>, commit: (() -> Unit)? = null) {
        val requested = ++generation
        prepareJob?.cancel()
        val snapshot = items.toList()
        val previousRows = rows
        prepareJob = scope.launch {
            val (prepared, changes) = withContext(Dispatchers.Default) {
                val prepared = prepareRows(snapshot)
                ensureActive()
                val changes = DiffUtil.calculateDiff(object : DiffUtil.Callback() {
                    override fun getOldListSize() = previousRows.size
                    override fun getNewListSize() = prepared.rows.size
                    override fun areItemsTheSame(old: Int, new: Int) =
                        DIFF.areItemsTheSame(previousRows[old], prepared.rows[new])
                    override fun areContentsTheSame(old: Int, new: Int) =
                        DIFF.areContentsTheSame(previousRows[old], prepared.rows[new])
                })
                ensureActive()
                prepared to changes
            }
            if (requested != generation) return@launch
            rows = prepared.rows
            media = prepared.media
            mediaKeys = prepared.keys
            dataVersion++
            mediaPositions = rows.mapIndexedNotNull { index, row -> row.entry?.key?.let { it to index } }.toMap()
            groupPositions = buildMap { rows.forEachIndexed { index, row -> row.members.forEach { put(it.key, index) } } }
            selected.retainAll(mediaKeys)
            changes.dispatchUpdatesTo(this@MediaAdapter)
            onSelectionChanged()
            commit?.invoke()
        }
    }

    private suspend fun prepareRows(items: List<MediaGroup>): PreparedRows {
        val inputFormats = listOf("dd-MMM-yyyy HH:mm", "yyyy-MM-dd HH:mm:ss", "yyyy-MM-dd'T'HH:mm:ss", "yyyy-MM-dd", "dd-MMM-yyyy")
            .map { SimpleDateFormat(it, Locale.US).apply { isLenient = false } }
        val keyFormat = SimpleDateFormat("yyyy-MM-dd", Locale.US)
        val titleFormat = SimpleDateFormat("M月d日", Locale.SIMPLIFIED_CHINESE)
        val fullTitleFormat = SimpleDateFormat("yyyy年M月d日", Locale.SIMPLIFIED_CHINESE)
        val calendar = Calendar.getInstance()
        val currentYear = calendar.get(Calendar.YEAR)
        val today = keyFormat.format(calendar.time)
        calendar.add(Calendar.DAY_OF_YEAR, -1)
        val yesterday = keyFormat.format(calendar.time)
        val dated = items.map { entry ->
            kotlinx.coroutines.currentCoroutineContext().ensureActive()
            val raw = entry.primary.modified
            val date = if (raw.isNullOrBlank()) null else {
                val cacheKey = "${keyFormat.timeZone.id}|$raw"
                val parsed = parsedDates.get(cacheKey) ?: ParsedDate(inputFormats.firstNotNullOfOrNull { format ->
                    format.parse(raw, ParsePosition(0))?.time
                }).also { parsedDates.put(cacheKey, it) }
                parsed.millis?.let(::Date)
            }
            Triple(entry, date?.let(keyFormat::format) ?: "unknown", date)
        }.sortedWith(compareByDescending<Triple<MediaGroup, String, Date?>> { it.third?.time ?: Long.MIN_VALUE }.thenByDescending { it.first.key })
        val preparedRows = buildList {
            dated.groupBy { it.second }.forEach { (day, entries) ->
                kotlinx.coroutines.currentCoroutineContext().ensureActive()
                val date = entries.first().third
                val title = when {
                    date == null -> "日期未知"
                    day == today -> "今天"
                    day == yesterday -> "昨天"
                    day.startsWith("$currentYear-") -> titleFormat.format(date)
                    else -> fullTitleFormat.format(date)
                }
                add(Row("date:$day", title, members = entries.map { it.first }))
                entries.forEach { add(Row("media:${it.first.key}", entry = it.first)) }
            }
        }
        return PreparedRows(preparedRows, dated.map { it.first }, items.mapTo(hashSetOf()) { it.key })
    }

    fun isHeader(position: Int) = rows.getOrNull(position)?.entry == null
    fun mediaPosition(key: String) = mediaPositions[key] ?: RecyclerView.NO_POSITION
    fun mediaAt(position: Int) = rows.getOrNull(position)?.entry
    fun applyDragSelection(anchor: Int, end: Int, before: Set<String>, select: Boolean) {
        if (mediaAt(anchor) == null || mediaAt(end) == null) return
        val next = before.toMutableSet()
        for (position in minOf(anchor, end)..maxOf(anchor, end)) {
            rows[position].entry?.key?.let { key -> if (select) next.add(key) else next.remove(key) }
        }
        val entering = !selectionMode
        val changed = (selected - next) + (next - selected)
        selected.clear()
        selected.addAll(next)
        selectionMode = true
        if (entering) updateSelection() else if (changed.isNotEmpty()) updateSelectionKeys(changed)
    }
    override fun getItemCount() = rows.size
    override fun getItemViewType(position: Int) = if (isHeader(position)) 1 else 0
    fun beginSelection() { selectionMode = true; updateSelection() }
    fun clearSelectedItems() { selected.clear(); updateSelection() }
    fun enterSelection(entry: MediaGroup) {
        val entering = !selectionMode
        selected.add(entry.key)
        selectionMode = true
        if (entering) updateSelection() else updateSelection(entry)
    }
    fun toggle(entry: MediaGroup) {
        val entering = !selectionMode
        if (!selected.remove(entry.key)) selected.add(entry.key)
        selectionMode = true
        if (entering) updateSelection() else updateSelection(entry)
    }
    fun selectAll() { currentList.forEach { selected.add(it.key) }; selectionMode = true; updateSelection() }
    fun clearSelection() { selected.clear(); selectionMode = false; updateSelection() }
    fun selectedEntries() = currentList.filter { it.key in selected }
    fun restoreSelection(keys: Collection<String>, active: Boolean) {
        selected.clear()
        selected.addAll(keys)
        selected.retainAll(mediaKeys)
        selectionMode = active
        updateSelection()
    }
    private fun updateSelection(entry: MediaGroup? = null) {
        if (entry == null) notifyItemRangeChanged(0, itemCount, SELECTION)
        else {
            mediaPositions[entry.key]?.let { notifyItemChanged(it, SELECTION) }
            groupPositions[entry.key]?.let { notifyItemChanged(it, SELECTION) }
        }
        onSelectionChanged()
    }
    private fun updateSelectionKeys(keys: Collection<String>) {
        val positions = linkedSetOf<Int>()
        keys.forEach { key -> mediaPositions[key]?.let(positions::add); groupPositions[key]?.let(positions::add) }
        positions.forEach { notifyItemChanged(it, SELECTION) }
        onSelectionChanged()
    }

    class HeaderVH(view: LinearLayout, val label: TextView, val check: ImageView) : RecyclerView.ViewHolder(view)
    class VH(view: View) : RecyclerView.ViewHolder(view) {
        val frame: View = view.findViewById(R.id.mediaFrame)
        val thumb: ImageView = view.findViewById(R.id.thumb)
        val name: TextView = view.findViewById(R.id.name)
        val badge: ImageView = view.findViewById(R.id.videoBadge)
        val formatBadge: TextView = view.findViewById(R.id.formatBadge)
        val check: ImageView = view.findViewById(R.id.check)
        val shade: View = view.findViewById(R.id.selectionShade)
        val placeholder: View = view.findViewById(R.id.placeholder)
        val kindLabel: TextView = view.findViewById(R.id.kindLabel)
        val sizeLabel: TextView = view.findViewById(R.id.sizeLabel)
        val binder = ThumbBinder(thumb)
    }
    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): RecyclerView.ViewHolder {
        if (viewType == 1) {
            val context = parent.context
            val density = context.resources.displayMetrics.density
            fun dp(value: Int) = (value * density).toInt()
            val row = LinearLayout(context).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = android.view.Gravity.CENTER_VERTICAL
                layoutParams = RecyclerView.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
                setPadding(dp(12), dp(8), dp(12), dp(6))
                minimumHeight = dp(42)
            }
            val check = ImageView(context).apply {
                setBackgroundResource(R.drawable.bg_gallery_group_check)
                setImageResource(R.drawable.ic_check)
                imageTintList = android.content.res.ColorStateList.valueOf(androidx.core.content.ContextCompat.getColor(context, R.color.nx_surface))
                setPadding(dp(4), dp(4), dp(4), dp(4))
                importantForAccessibility = View.IMPORTANT_FOR_ACCESSIBILITY_NO
            }
            val label = TextView(context).apply {
            textSize = 14f
            typeface = android.graphics.Typeface.create("sans-serif", android.graphics.Typeface.BOLD)
            setTextColor(androidx.core.content.ContextCompat.getColor(context, R.color.nx_on_surface))
            ViewCompat.setAccessibilityHeading(this, true)
            }
            row.addView(check, LinearLayout.LayoutParams(dp(24), dp(24)).apply { marginEnd = dp(12) })
            row.addView(label, LinearLayout.LayoutParams(0, -2, 1f))
            return HeaderVH(row, label, check)
        }
        return VH(LayoutInflater.from(parent.context).inflate(R.layout.item_media, parent, false))
    }
    override fun onBindViewHolder(holder: RecyclerView.ViewHolder, position: Int, payloads: MutableList<Any>) {
        if (holder is HeaderVH && payloads.isNotEmpty() && payloads.all { it == SELECTION }) bindHeader(holder, rows[position])
        else if (holder is VH && payloads.isNotEmpty() && payloads.all { it == SELECTION }) {
            bindSelection(holder, requireNotNull(rows[position].entry), true)
        } else onBindViewHolder(holder, position)
    }
    override fun onBindViewHolder(holder: RecyclerView.ViewHolder, position: Int) {
        val row = rows[position]
        if (holder is HeaderVH) { bindHeader(holder, row); return }
        holder as VH
        val entry = requireNotNull(row.entry)
        holder.name.text = listOfNotNull(entry.displayName, entry.sizeText).joinToString(" · ")
        holder.formatBadge.text = if (entry.paired) "RAW+JPG" else "RAW"
        holder.badge.visibility = if (entry.isVideo) View.VISIBLE else View.GONE
        holder.kindLabel.text = entry.kindLabel
        holder.sizeLabel.text = "正在读取预览"
        holder.placeholder.visibility = View.VISIBLE
        bindSelection(holder, entry, false)
        if (entry.isVideo) {
            holder.binder.clear()
            holder.thumb.visibility = View.GONE
            holder.sizeLabel.text = listOfNotNull(entry.sizeText, "点击下载").joinToString(" · ")
        } else {
            holder.thumb.visibility = View.VISIBLE
            holder.binder.bind(entry, 320, onLoaded = { loaded ->
                holder.placeholder.visibility = if (loaded) View.GONE else View.VISIBLE
                if (!loaded) holder.sizeLabel.text = "无可用预览\n可下载原文件"
            })
        }
        holder.itemView.setOnClickListener {
            if (selectionMode) { toggle(entry) }
            else {
                rememberSource(entry, holder.thumb)
                onClick(entry)
            }
        }
        holder.itemView.setOnLongClickListener { onLongClick(entry); true }
    }
    private fun bindHeader(holder: HeaderVH, row: Row) {
        val allSelected = row.members.isNotEmpty() && row.members.all { it.key in selected }
        val count = row.members.count { it.key in selected }
        holder.label.text = row.title
        holder.check.visibility = if (selectionMode) View.VISIBLE else View.GONE
        holder.check.isSelected = allSelected
        holder.check.imageAlpha = if (allSelected) 255 else 0
        holder.itemView.isClickable = selectionMode
        holder.itemView.isFocusable = selectionMode
        holder.itemView.contentDescription = if (selectionMode) "${row.title}，已选 $count/${row.members.size} 项，${if (allSelected) "取消选择这组" else "选择这组"}" else row.title
        holder.itemView.setOnClickListener(if (!selectionMode) null else View.OnClickListener {
            if (allSelected) row.members.forEach { selected.remove(it.key) }
            else row.members.forEach { selected.add(it.key) }
            updateSelectionKeys(row.members.map { it.key })
        })
    }
    private fun bindSelection(holder: VH, entry: MediaGroup, animate: Boolean) {
        val checked = entry.key in selected
        holder.itemView.isSelected = checked
        holder.itemView.contentDescription = "${entry.displayName}，${entry.kindLabel}" + if (selectionMode) if (checked) "，已选择" else "，未选择" else ""
        holder.check.visibility = if (selectionMode) View.VISIBLE else View.GONE
        holder.check.isSelected = checked
        holder.check.imageAlpha = if (checked) 255 else 0
        holder.shade.visibility = if (checked) View.VISIBLE else View.GONE
        holder.formatBadge.visibility = if (entry.raw != null && !entry.isVideo) View.VISIBLE else View.GONE
        val scale = if (checked) .9f else 1f
        holder.frame.animate().cancel()
        holder.check.animate().cancel()
        val enabled = if (Build.VERSION.SDK_INT >= 26) ValueAnimator.areAnimatorsEnabled()
            else Settings.Global.getFloat(holder.itemView.context.contentResolver, Settings.Global.ANIMATOR_DURATION_SCALE, 1f) > 0f
        if (animate && enabled) {
            holder.frame.animate().scaleX(scale).scaleY(scale).setDuration(180).setInterpolator(PathInterpolator(.22f, .25f, 0f, 1f)).start()
            if (checked) {
                holder.check.scaleX = .7f; holder.check.scaleY = .7f
                holder.check.animate().scaleX(1f).scaleY(1f).setDuration(180).setInterpolator(PathInterpolator(.22f, .25f, 0f, 1f)).start()
            } else { holder.check.scaleX = 1f; holder.check.scaleY = 1f }
        } else {
            holder.frame.scaleX = scale; holder.frame.scaleY = scale
            holder.check.scaleX = 1f; holder.check.scaleY = 1f
        }
    }
    override fun onViewRecycled(holder: RecyclerView.ViewHolder) {
        if (holder is VH) { holder.binder.clear(); holder.frame.animate().cancel(); holder.check.animate().cancel() }
        super.onViewRecycled(holder)
    }

    override fun onDetachedFromRecyclerView(recyclerView: RecyclerView) {
        generation++
        prepareJob?.cancel()
        super.onDetachedFromRecyclerView(recyclerView)
    }
}
