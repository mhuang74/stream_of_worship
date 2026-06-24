package org.streamofworship.android.core.design

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.RowScope
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.unit.dp
import org.streamofworship.android.core.navigation.SowBottomNavDestinations
import org.streamofworship.android.core.navigation.SowNavGraph

@Composable
fun SowApp() {
    SowTheme {
        SowShell()
    }
}

@Composable
fun SowShell(
    modifier: Modifier = Modifier,
    selectedRoutePattern: String = "songsets",
    content: @Composable () -> Unit = { SowNavGraph(Modifier.fillMaxSize()) },
) {
    Scaffold(
        modifier = modifier.testTag("sow-shell"),
        containerColor = MaterialTheme.colorScheme.background,
        bottomBar = {
            NavigationBar(
                containerColor = MaterialTheme.colorScheme.surface,
                tonalElevation = 0.dp,
            ) {
                SowBottomNavDestinations.forEach { destination ->
                    SowNavigationBarItem(
                        selected = destination.route.pattern == selectedRoutePattern,
                        label = destination.route.title,
                        icon = { Icon(destination.icon, contentDescription = null) },
                    )
                }
            }
        },
    ) { paddingValues ->
        Box(
            modifier =
                Modifier
                    .fillMaxSize()
                    .padding(paddingValues),
        ) {
            content()
        }
    }
}

@Composable
private fun RowScope.SowNavigationBarItem(
    selected: Boolean,
    label: String,
    icon: @Composable () -> Unit,
) {
    NavigationBarItem(
        selected = selected,
        onClick = {},
        icon = icon,
        label = { Text(label) },
    )
}
