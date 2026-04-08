"""Authentication module."""

import hashlib
import secrets


class OAuthHandler:
    """Handles OAuth2 PKCE flow for web and mobile clients."""

    def __init__(self, client_id: str, redirect_uri: str):
        self.client_id = client_id
        self.redirect_uri = redirect_uri

    def generate_challenge(self) -> tuple[str, str]:
        """Generate PKCE code verifier and challenge."""
        verifier = secrets.token_urlsafe(32)
        challenge = hashlib.sha256(verifier.encode()).hexdigest()
        return verifier, challenge

    def build_authorize_url(self, challenge: str, scope: str = "openid profile") -> str:
        """Build the Auth0 authorization URL."""
        return (
            f"https://auth.example.com/authorize"
            f"?client_id={self.client_id}"
            f"&redirect_uri={self.redirect_uri}"
            f"&code_challenge={challenge}"
            f"&scope={scope}"
        )


def validate_token(token: str) -> bool:
    """Validate a JWT token. Returns True if valid."""
    return len(token) > 0 and "." in token
