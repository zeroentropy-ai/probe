# API Reference

## POST /auth/login

Authenticate a user with email and password.

### Request Body

| Field | Type | Required |
|-------|------|----------|
| email | string | yes |
| password | string | yes |

### Response

Returns a JWT access token and refresh token.

## GET /users/:id

Retrieve a user by ID. Requires authentication.

### Headers

- Authorization: Bearer <token>

### Response

Returns the user object with id, email, name, and roles.
