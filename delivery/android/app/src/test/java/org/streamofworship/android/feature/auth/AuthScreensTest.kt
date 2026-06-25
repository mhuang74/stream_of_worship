package org.streamofworship.android.feature.auth

import androidx.compose.material3.Text
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.hasClickAction
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.test.ext.junit.runners.AndroidJUnit4
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.streamofworship.android.core.design.SowTheme
import org.streamofworship.android.core.network.ApiError
import org.streamofworship.android.core.network.ApiErrorKind
import org.streamofworship.android.core.network.AuthUser
import org.streamofworship.android.core.network.CurrentSession
import org.streamofworship.android.core.session.AuthController
import org.streamofworship.android.core.session.AuthState

@RunWith(AndroidJUnit4::class)
class AuthScreensTest {
    @get:Rule
    val composeRule = createComposeRule()

    @Test
    fun `login screen validates required fields before submit`() {
        var submitCount = 0
        composeRule.setContent {
            SowTheme {
                LoginScreen(
                    loading = false,
                    formError = null,
                    onSubmit = { _, _ -> submitCount += 1 },
                    onRegisterClick = {},
                )
            }
        }

        composeRule.onNode(hasClickAction() and hasText("Sign in")).performClick()

        composeRule.onNodeWithText("Email is required").assertIsDisplayed()
        composeRule.onNodeWithText("Password is required").assertIsDisplayed()
        assertEquals(0, submitCount)
    }

    @Test
    fun `auth gate restores session and renders protected content when authenticated`() {
        val controller =
            FakeAuthController(
                initialState =
                    AuthState.Authenticated(
                        CurrentSession(AuthUser(id = "42", email = "user@example.com")),
                    ),
            )
        composeRule.setContent {
            SowTheme {
                AuthenticatedAppGate(authController = controller) {
                    Text("Protected songsets")
                }
            }
        }

        composeRule.onNodeWithText("Protected songsets").assertIsDisplayed()
        assertEquals(1, controller.restoreCount)
    }

    @Test
    fun `auth gate renders login screen for unauthenticated state`() {
        val controller = FakeAuthController(initialState = AuthState.Unauthenticated)
        composeRule.setContent {
            SowTheme {
                AuthenticatedAppGate(authController = controller) {
                    Text("Protected songsets")
                }
            }
        }

        composeRule.onNodeWithTag("login-screen").assertIsDisplayed()
    }

    @Test
    fun `auth gate routes non-401 sign in failures back to login form`() {
        val controller =
            FakeAuthController(
                initialState =
                    AuthState.Error(
                        ApiError(
                            statusCode = 500,
                            message = "Stream of Worship is temporarily unavailable.",
                            kind = ApiErrorKind.Server,
                        ),
                    ),
            )
        composeRule.setContent {
            SowTheme {
                AuthenticatedAppGate(authController = controller) {
                    Text("Protected songsets")
                }
            }
        }

        composeRule.onNodeWithTag("login-screen").assertIsDisplayed()
        composeRule.onNodeWithText("Stream of Worship is temporarily unavailable.").assertIsDisplayed()
    }

    private class FakeAuthController(
        initialState: AuthState,
    ) : AuthController {
        private val mutableState = MutableStateFlow(initialState)
        var restoreCount = 0
        override val authState: StateFlow<AuthState> = mutableState

        override fun restoreSession() {
            restoreCount += 1
        }

        override fun signIn(
            email: String,
            password: String,
        ) = Unit

        override fun register(
            name: String,
            email: String,
            password: String,
        ) = Unit

        override fun signOut() = Unit

        override fun onSessionExpired() = Unit
    }
}
