package org.streamofworship.android.core.session

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import org.streamofworship.android.core.network.ApiError
import org.streamofworship.android.core.network.ApiErrorKind
import org.streamofworship.android.core.network.ApiException
import org.streamofworship.android.core.network.CurrentSession

sealed interface AuthState {
    data object Restoring : AuthState

    data object Unauthenticated : AuthState

    data class Authenticated(
        val session: CurrentSession,
    ) : AuthState

    data class Error(
        val error: ApiError,
    ) : AuthState
}

interface AuthController {
    val authState: StateFlow<AuthState>

    fun restoreSession()

    fun signIn(
        email: String,
        password: String,
    )

    fun register(
        name: String,
        email: String,
        password: String,
    )

    fun signOut()
}

class AuthSessionManager(
    private val repository: AuthRepository,
    private val scope: CoroutineScope,
) : AuthController {
    private val mutableAuthState = MutableStateFlow<AuthState>(AuthState.Restoring)
    override val authState: StateFlow<AuthState> = mutableAuthState.asStateFlow()

    override fun restoreSession() {
        mutableAuthState.value = AuthState.Restoring
        scope.launch {
            runCatching { repository.restoreSession() }
                .onSuccess { session ->
                    mutableAuthState.value =
                        if (session == null) {
                            AuthState.Unauthenticated
                        } else {
                            AuthState.Authenticated(session)
                        }
                }.onFailure { error ->
                    mutableAuthState.value = AuthState.Error(error.toApiError())
                }
        }
    }

    override fun signIn(
        email: String,
        password: String,
    ) {
        mutableAuthState.value = AuthState.Restoring
        scope.launch {
            runCatching { repository.signIn(email = email, password = password) }
                .onSuccess { mutableAuthState.value = AuthState.Authenticated(it) }
                .onFailure { mutableAuthState.value = AuthState.Error(it.toApiError()) }
        }
    }

    override fun register(
        name: String,
        email: String,
        password: String,
    ) {
        mutableAuthState.value = AuthState.Restoring
        scope.launch {
            runCatching { repository.register(name = name, email = email, password = password) }
                .onSuccess { mutableAuthState.value = AuthState.Authenticated(it) }
                .onFailure { mutableAuthState.value = AuthState.Error(it.toApiError()) }
        }
    }

    override fun signOut() {
        mutableAuthState.value = AuthState.Restoring
        scope.launch {
            runCatching { repository.signOut() }
            mutableAuthState.value = AuthState.Unauthenticated
        }
    }

    private fun Throwable.toApiError(): ApiError =
        when (this) {
            is ApiException -> error
            else ->
                ApiError(
                    message = message ?: "Authentication failed.",
                    kind = ApiErrorKind.Unknown,
                )
        }
}
