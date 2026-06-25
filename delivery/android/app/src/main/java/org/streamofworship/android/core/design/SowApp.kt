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
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.unit.dp
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import org.streamofworship.android.core.navigation.SowBottomNavDestinations
import org.streamofworship.android.core.navigation.SowNavGraph
import org.streamofworship.android.core.navigation.SowRoute
import org.streamofworship.android.feature.auth.AuthenticatedAppGate

@Composable
fun SowApp() {
    SowTheme {
        AuthenticatedAppGate {
            val navController = rememberNavController()
            val backStackEntry by navController.currentBackStackEntryAsState()
            SowShell(
                selectedRoutePattern = backStackEntry?.destination?.route ?: SowRoute.Songsets.pattern,
                onNavigate = { route ->
                    navController.navigate(route.pattern) {
                        popUpTo(SowRoute.Songsets.pattern) {
                            saveState = true
                        }
                        launchSingleTop = true
                        restoreState = true
                    }
                },
            ) {
                SowNavGraph(navController = navController, modifier = Modifier.fillMaxSize())
            }
        }
    }
}

@Composable
fun SowShell(
    modifier: Modifier = Modifier,
    selectedRoutePattern: String = "songsets",
    onNavigate: (SowRoute) -> Unit = {},
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
                        onClick = { onNavigate(destination.route) },
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
    onClick: () -> Unit,
) {
    NavigationBarItem(
        selected = selected,
        onClick = onClick,
        icon = icon,
        label = { Text(label) },
    )
}
