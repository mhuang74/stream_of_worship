package org.streamofworship.android.core.design

import androidx.compose.material3.ColorScheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Typography
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp

private val SowLightColorScheme: ColorScheme =
    lightColorScheme(
        primary = Color(0xFF262626),
        onPrimary = Color.White,
        secondary = Color(0xFF525252),
        onSecondary = Color.White,
        background = Color(0xFFFCFCFC),
        onBackground = Color(0xFF171717),
        surface = Color.White,
        onSurface = Color(0xFF171717),
        surfaceVariant = Color(0xFFF5F5F5),
        onSurfaceVariant = Color(0xFF525252),
        outline = Color(0xFFE5E5E5),
        error = Color(0xFFB42318),
        onError = Color.White,
    )

val SowTypography =
    Typography(
        headlineSmall =
            TextStyle(
                fontFamily = FontFamily.SansSerif,
                fontSize = 22.sp,
                lineHeight = 28.sp,
                fontWeight = FontWeight.SemiBold,
            ),
        titleMedium =
            TextStyle(
                fontFamily = FontFamily.SansSerif,
                fontSize = 16.sp,
                lineHeight = 22.sp,
                fontWeight = FontWeight.SemiBold,
            ),
        bodyMedium =
            TextStyle(
                fontFamily = FontFamily.SansSerif,
                fontSize = 14.sp,
                lineHeight = 20.sp,
                fontWeight = FontWeight.Normal,
            ),
        labelMedium =
            TextStyle(
                fontFamily = FontFamily.SansSerif,
                fontSize = 12.sp,
                lineHeight = 16.sp,
                fontWeight = FontWeight.Medium,
            ),
    )

@Composable
fun SowTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = SowLightColorScheme,
        typography = SowTypography,
        content = content,
    )
}
