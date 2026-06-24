package org.streamofworship.android.core.model

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

@Serializable
enum class RenderState {
    @SerialName("unrendered")
    Unrendered,

    @SerialName("rendering")
    Rendering,

    @SerialName("failed")
    Failed,

    @SerialName("stale")
    Stale,

    @SerialName("fresh")
    Fresh,
}

fun RenderState.label(): String =
    when (this) {
        RenderState.Unrendered -> "Not rendered"
        RenderState.Rendering -> "Rendering"
        RenderState.Failed -> "Failed"
        RenderState.Stale -> "Stale"
        RenderState.Fresh -> "Fresh"
    }
