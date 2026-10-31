// SPDX-License-Identifier: AGPL-3.0-or-later
package com.nxwa.nxconnect.ui

import android.content.Context
import android.util.AttributeSet
import com.google.android.material.appbar.AppBarLayout

/** XML scroll|snap flags let Material coordinate dragging, flinging and both resting positions. */
class OneUiAppBarBehavior : AppBarLayout.Behavior {
    constructor() : super()
    constructor(context: Context, attrs: AttributeSet?) : super(context, attrs)
}
