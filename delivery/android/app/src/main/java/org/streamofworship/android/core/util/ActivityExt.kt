package org.streamofworship.android.core.util

import android.app.Activity
import android.content.Context
import android.content.ContextWrapper

/**
 * Walks the [ContextWrapper] chain to find the hosting [Activity]. Returns null when no
 * activity is reachable (e.g. in some embedded contexts), in which case immersive
 * behavior should fall back to its non-immersive inline layout.
 */
fun Context.findActivity(): Activity? {
    var ctx = this
    while (ctx is ContextWrapper) {
        if (ctx is Activity) return ctx
        ctx = ctx.baseContext
    }
    return null
}
