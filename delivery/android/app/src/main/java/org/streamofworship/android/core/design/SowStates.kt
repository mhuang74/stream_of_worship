package org.streamofworship.android.core.design

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.ErrorOutline
import androidx.compose.material.icons.outlined.Inbox
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp

@Composable
fun SowLoadingState(
    label: String = "Loading",
    modifier: Modifier = Modifier,
) {
    StatePanel(
        modifier = modifier.testTag("sow-loading-state"),
        icon = null,
        title = label,
        body = "Preparing the latest worship data.",
        action = null,
    ) {
        CircularProgressIndicator()
    }
}

@Composable
fun SowErrorState(
    title: String,
    message: String,
    modifier: Modifier = Modifier,
    actionLabel: String? = null,
    onAction: (() -> Unit)? = null,
) {
    StatePanel(
        modifier = modifier.testTag("sow-error-state"),
        icon = Icons.Outlined.ErrorOutline,
        title = title,
        body = message,
        action =
            if (actionLabel != null && onAction != null) {
                { OutlinedButton(onClick = onAction) { Text(actionLabel) } }
            } else {
                null
            },
    )
}

@Composable
fun SowEmptyState(
    title: String,
    message: String,
    modifier: Modifier = Modifier,
    actionLabel: String? = null,
    onAction: (() -> Unit)? = null,
) {
    StatePanel(
        modifier = modifier.testTag("sow-empty-state"),
        icon = Icons.Outlined.Inbox,
        title = title,
        body = message,
        action =
            if (actionLabel != null && onAction != null) {
                { Button(onClick = onAction) { Text(actionLabel) } }
            } else {
                null
            },
    )
}

@Composable
private fun StatePanel(
    title: String,
    body: String,
    icon: ImageVector?,
    modifier: Modifier = Modifier,
    action: (@Composable () -> Unit)?,
    leading: (@Composable () -> Unit)? = null,
) {
    Surface(
        modifier = modifier.fillMaxWidth(),
        color = MaterialTheme.colorScheme.surface,
        tonalElevation = 0.dp,
        shape = MaterialTheme.shapes.medium,
    ) {
        Column(
            modifier = Modifier.padding(PaddingValues(horizontal = 20.dp, vertical = 28.dp)),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            leading?.invoke()
            if (icon != null) {
                Icon(
                    imageVector = icon,
                    contentDescription = null,
                    tint = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            Text(
                text = title,
                style = MaterialTheme.typography.titleMedium,
                textAlign = TextAlign.Center,
            )
            Text(
                text = body,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                style = MaterialTheme.typography.bodyMedium,
                textAlign = TextAlign.Center,
            )
            action?.invoke()
        }
    }
}
