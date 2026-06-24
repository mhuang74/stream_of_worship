package org.streamofworship.android.feature.auth

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class AuthValidationTest {
    @Test
    fun `login validation requires valid email and eight character password`() {
        val errors = AuthValidation.validateLogin(email = "bad", password = "short")

        assertEquals("Enter a valid email address", errors.email)
        assertEquals("Password must be at least 8 characters", errors.password)
    }

    @Test
    fun `register validation requires matching password confirmation`() {
        val errors =
            AuthValidation.validateRegister(
                name = "User",
                email = "user@example.com",
                password = "password123",
                confirmPassword = "different",
            )

        assertEquals("Passwords do not match", errors.confirmPassword)
    }

    @Test
    fun `valid register input passes validation`() {
        val errors =
            AuthValidation.validateRegister(
                name = "User",
                email = "user@example.com",
                password = "password123",
                confirmPassword = "password123",
            )

        assertTrue(errors.isValid)
    }
}
