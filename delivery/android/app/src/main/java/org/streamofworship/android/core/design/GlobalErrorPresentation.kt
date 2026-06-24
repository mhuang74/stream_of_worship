package org.streamofworship.android.core.design

import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import org.streamofworship.android.core.network.ApiErrorKind
import org.streamofworship.android.core.network.ApiException

data class GlobalErrorPresentation(
    val title: String,
    val message: String,
    val actionLabel: String?,
)

fun Throwable.toGlobalErrorPresentation(): GlobalErrorPresentation =
    when (this) {
        is ApiException ->
            when (error.kind) {
                ApiErrorKind.Unauthorized ->
                    GlobalErrorPresentation(
                        title = "Session expired",
                        message = "Please sign in again to continue.",
                        actionLabel = "Sign in",
                    )
                ApiErrorKind.Network ->
                    GlobalErrorPresentation(
                        title = "Offline",
                        message = "Check your connection, then retry.",
                        actionLabel = "Retry",
                    )
                ApiErrorKind.Server ->
                    GlobalErrorPresentation(
                        title = "Maintenance",
                        message = "Stream of Worship is temporarily unavailable.",
                        actionLabel = "Retry",
                    )
                else ->
                    GlobalErrorPresentation(
                        title = "Request failed",
                        message = error.message,
                        actionLabel = "Retry",
                    )
            }
        else ->
            GlobalErrorPresentation(
                title = "Request failed",
                message = message ?: "Something went wrong.",
                actionLabel = "Retry",
            )
    }

@Composable
fun SowGlobalErrorState(
    error: Throwable,
    modifier: Modifier = Modifier,
    onAction: (() -> Unit)? = null,
) {
    val presentation = error.toGlobalErrorPresentation()
    SowErrorState(
        title = presentation.title,
        message = presentation.message,
        actionLabel = presentation.actionLabel.takeIf { onAction != null },
        onAction = onAction,
        modifier = modifier.testTag("sow-global-error-state"),
    )
}
