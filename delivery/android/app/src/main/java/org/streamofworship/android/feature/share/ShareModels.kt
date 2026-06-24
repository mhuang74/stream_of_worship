package org.streamofworship.android.feature.share

import kotlinx.serialization.Serializable

@Serializable
data class CreateShareRequest(
    val songsetId: String? = null,
    val renderJobId: String? = null,
    val allowDownload: Boolean = false,
)

@Serializable
data class ShareToken(
    val token: String,
    val shareUrl: String? = null,
    val songsetId: String,
    val renderJobId: String? = null,
    val allowDownload: Boolean = false,
)

@Serializable
data class ShareListResponse(
    val shares: List<ShareToken> = emptyList(),
)
