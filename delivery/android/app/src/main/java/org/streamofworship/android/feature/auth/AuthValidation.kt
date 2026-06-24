package org.streamofworship.android.feature.auth

data class LoginValidationErrors(
    val email: String? = null,
    val password: String? = null,
) {
    val isValid: Boolean = email == null && password == null
}

data class RegisterValidationErrors(
    val name: String? = null,
    val email: String? = null,
    val password: String? = null,
    val confirmPassword: String? = null,
) {
    val isValid: Boolean = name == null && email == null && password == null && confirmPassword == null
}

object AuthValidation {
    private val emailPattern = Regex("^[^\\s@]+@[^\\s@]+\\.[^\\s@]+$")

    fun validateLogin(
        email: String,
        password: String,
    ): LoginValidationErrors =
        LoginValidationErrors(
            email = validateEmail(email),
            password = validatePassword(password),
        )

    fun validateRegister(
        name: String,
        email: String,
        password: String,
        confirmPassword: String,
    ): RegisterValidationErrors =
        RegisterValidationErrors(
            name = if (name.isBlank()) "Name is required" else null,
            email = validateEmail(email),
            password = validatePassword(password),
            confirmPassword =
                when {
                    confirmPassword.isBlank() -> "Please confirm your password"
                    confirmPassword != password -> "Passwords do not match"
                    else -> null
                },
        )

    private fun validateEmail(email: String): String? =
        when {
            email.isBlank() -> "Email is required"
            !emailPattern.matches(email) -> "Enter a valid email address"
            else -> null
        }

    private fun validatePassword(password: String): String? =
        when {
            password.isBlank() -> "Password is required"
            password.length < 8 -> "Password must be at least 8 characters"
            else -> null
        }
}
