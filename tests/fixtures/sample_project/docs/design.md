# Project Design

## Authentication

### OAuth Flow

We use PKCE-based OAuth 2.0 with Auth0 as the identity provider.
The flow works as follows:

1. Client generates a code verifier and challenge
2. User is redirected to Auth0's /authorize endpoint
3. Auth0 authenticates the user and returns an authorization code
4. Client exchanges the code + verifier for tokens

### Session Management

Sessions are stored in Redis with a 24-hour TTL.
Each session contains the user's ID, roles, and permissions.

## API Design

### Rate Limiting

All API endpoints are rate-limited to 100 requests per minute per user.
Rate limit headers are included in every response:

- X-RateLimit-Limit: 100
- X-RateLimit-Remaining: 95
- X-RateLimit-Reset: 1620000000

### Error Handling

All errors follow RFC 7807 Problem Details format.
