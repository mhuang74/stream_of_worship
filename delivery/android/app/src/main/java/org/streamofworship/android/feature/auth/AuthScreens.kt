package org.streamofworship.android.feature.auth

import android.content.Context
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import org.streamofworship.android.core.config.AppConfig
import org.streamofworship.android.core.design.SowErrorState
import org.streamofworship.android.core.design.SowLoadingState
import org.streamofworship.android.core.network.AuthApi
import org.streamofworship.android.core.network.SowApiClientFactory
import org.streamofworship.android.core.session.AndroidSecureSessionCookieStore
import org.streamofworship.android.core.session.AuthController
import org.streamofworship.android.core.session.AuthRepository
import org.streamofworship.android.core.session.AuthSessionManager
import org.streamofworship.android.core.session.AuthState

@Composable
fun rememberAuthController(
    config: AppConfig = AppConfig.fromBuildConfig(),
    context: Context = LocalContext.current.applicationContext,
): AuthController {
    val scope = rememberCoroutineScope()
    return remember(config, context, scope) {
        val cookieStore = AndroidSecureSessionCookieStore(context)
        val apiClient = SowApiClientFactory.create(config = config, cookieStore = cookieStore)
        AuthSessionManager(
            repository =
                AuthRepository(
                    api = apiClient.create<AuthApi>(),
                    cookieStore = cookieStore,
                ),
            scope = scope,
        )
    }
}

@Composable
fun AuthenticatedAppGate(
    authController: AuthController = rememberAuthController(),
    protectedContent: @Composable () -> Unit,
) {
    val authState by authController.authState.collectAsState()
    var mode by remember { mutableStateOf(AuthScreenMode.Login) }

    LaunchedEffect(authController) {
        authController.restoreSession()
    }

    when (val state = authState) {
        AuthState.Restoring ->
            SowLoadingState(
                label = "Restoring session",
                modifier =
                    Modifier
                        .fillMaxSize()
                        .padding(24.dp),
            )

        AuthState.Unauthenticated ->
            if (mode == AuthScreenMode.Login) {
                LoginScreen(
                    loading = false,
                    formError = null,
                    onSubmit = authController::signIn,
                    onRegisterClick = { mode = AuthScreenMode.Register },
                )
            } else {
                RegisterScreen(
                    loading = false,
                    formError = null,
                    onSubmit = authController::register,
                    onLoginClick = { mode = AuthScreenMode.Login },
                )
            }

        is AuthState.Error ->
            if (state.error.statusCode == 401) {
                LoginScreen(
                    loading = false,
                    formError = state.error.message,
                    onSubmit = authController::signIn,
                    onRegisterClick = { mode = AuthScreenMode.Register },
                )
            } else {
                SowErrorState(
                    title = "Authentication unavailable",
                    message = state.error.message,
                    actionLabel = "Try again",
                    onAction = authController::restoreSession,
                    modifier =
                        Modifier
                            .fillMaxSize()
                            .padding(24.dp),
                )
            }

        is AuthState.Authenticated -> protectedContent()
    }
}

@Composable
fun LoginScreen(
    loading: Boolean,
    formError: String?,
    onSubmit: (email: String, password: String) -> Unit,
    onRegisterClick: () -> Unit,
    modifier: Modifier = Modifier,
) {
    var email by remember { mutableStateOf("") }
    var password by remember { mutableStateOf("") }
    var errors by remember { mutableStateOf(LoginValidationErrors()) }

    AuthFormScaffold(
        title = "Sign in",
        subtitle = "Access Stream of Worship",
        modifier = modifier.testTag("login-screen"),
    ) {
        OutlinedTextField(
            value = email,
            onValueChange = {
                email = it
                errors = errors.copy(email = null)
            },
            label = { Text("Email") },
            isError = errors.email != null,
            supportingText = { errors.email?.let { Text(it) } },
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Email),
            singleLine = true,
            modifier = Modifier.fillMaxWidth(),
        )
        OutlinedTextField(
            value = password,
            onValueChange = {
                password = it
                errors = errors.copy(password = null)
            },
            label = { Text("Password") },
            isError = errors.password != null,
            supportingText = { errors.password?.let { Text(it) } },
            visualTransformation = PasswordVisualTransformation(),
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
            singleLine = true,
            modifier = Modifier.fillMaxWidth(),
        )
        formError?.let {
            Text(
                text = it,
                color = MaterialTheme.colorScheme.error,
                style = MaterialTheme.typography.bodyMedium,
                modifier = Modifier.testTag("auth-form-error"),
            )
        }
        Button(
            onClick = {
                val nextErrors = AuthValidation.validateLogin(email = email, password = password)
                errors = nextErrors
                if (nextErrors.isValid) {
                    onSubmit(email.trim(), password)
                }
            },
            enabled = !loading,
            modifier = Modifier.fillMaxWidth(),
        ) {
            Text(if (loading) "Signing in..." else "Sign in")
        }
        OutlinedButton(
            onClick = onRegisterClick,
            enabled = !loading,
            modifier = Modifier.fillMaxWidth(),
        ) {
            Text("Create account")
        }
    }
}

@Composable
fun RegisterScreen(
    loading: Boolean,
    formError: String?,
    onSubmit: (name: String, email: String, password: String) -> Unit,
    onLoginClick: () -> Unit,
    modifier: Modifier = Modifier,
) {
    var name by remember { mutableStateOf("") }
    var email by remember { mutableStateOf("") }
    var password by remember { mutableStateOf("") }
    var confirmPassword by remember { mutableStateOf("") }
    var errors by remember { mutableStateOf(RegisterValidationErrors()) }

    AuthFormScaffold(
        title = "Create account",
        subtitle = "Set up your worship planning workspace",
        modifier = modifier.testTag("register-screen"),
    ) {
        OutlinedTextField(
            value = name,
            onValueChange = {
                name = it
                errors = errors.copy(name = null)
            },
            label = { Text("Name") },
            isError = errors.name != null,
            supportingText = { errors.name?.let { Text(it) } },
            singleLine = true,
            modifier = Modifier.fillMaxWidth(),
        )
        OutlinedTextField(
            value = email,
            onValueChange = {
                email = it
                errors = errors.copy(email = null)
            },
            label = { Text("Email") },
            isError = errors.email != null,
            supportingText = { errors.email?.let { Text(it) } },
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Email),
            singleLine = true,
            modifier = Modifier.fillMaxWidth(),
        )
        PasswordField(
            value = password,
            label = "Password",
            error = errors.password,
            onValueChange = {
                password = it
                errors = errors.copy(password = null)
            },
        )
        PasswordField(
            value = confirmPassword,
            label = "Confirm password",
            error = errors.confirmPassword,
            onValueChange = {
                confirmPassword = it
                errors = errors.copy(confirmPassword = null)
            },
        )
        formError?.let {
            Text(
                text = it,
                color = MaterialTheme.colorScheme.error,
                style = MaterialTheme.typography.bodyMedium,
                modifier = Modifier.testTag("auth-form-error"),
            )
        }
        Button(
            onClick = {
                val nextErrors =
                    AuthValidation.validateRegister(
                        name = name,
                        email = email,
                        password = password,
                        confirmPassword = confirmPassword,
                    )
                errors = nextErrors
                if (nextErrors.isValid) {
                    onSubmit(name.trim(), email.trim(), password)
                }
            },
            enabled = !loading,
            modifier = Modifier.fillMaxWidth(),
        ) {
            Text(if (loading) "Creating account..." else "Create account")
        }
        OutlinedButton(
            onClick = onLoginClick,
            enabled = !loading,
            modifier = Modifier.fillMaxWidth(),
        ) {
            Text("Sign in")
        }
    }
}

@Composable
private fun PasswordField(
    value: String,
    label: String,
    error: String?,
    onValueChange: (String) -> Unit,
) {
    OutlinedTextField(
        value = value,
        onValueChange = onValueChange,
        label = { Text(label) },
        isError = error != null,
        supportingText = { error?.let { Text(it) } },
        visualTransformation = PasswordVisualTransformation(),
        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
        singleLine = true,
        modifier = Modifier.fillMaxWidth(),
    )
}

@Composable
private fun AuthFormScaffold(
    title: String,
    subtitle: String,
    modifier: Modifier = Modifier,
    content: @Composable () -> Unit,
) {
    Column(
        modifier =
            modifier
                .fillMaxSize()
                .verticalScroll(rememberScrollState())
                .padding(PaddingValues(horizontal = 24.dp, vertical = 32.dp)),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        Text(
            text = title,
            style = MaterialTheme.typography.headlineSmall,
        )
        Text(
            text = subtitle,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            style = MaterialTheme.typography.bodyMedium,
        )
        content()
    }
}

private enum class AuthScreenMode {
    Login,
    Register,
}
