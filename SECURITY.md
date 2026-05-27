# Security

## Reporting a Vulnerability

Please report security issues privately to `founders@zeroentropy.dev`.

Include the affected version, a clear reproduction path, and any logs or stack traces
that help us understand impact. We will acknowledge reports as soon as practical and
coordinate fixes before public disclosure.

## Data Handling

probe stores its index locally in `.probe/`. It sends chunk text to the configured
embedding and reranking providers to answer searches; it does not upload whole
repositories or persist documents on an external server.
