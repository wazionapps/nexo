# Security Policy

## Supported Versions

| Version | Supported          |
|---------|--------------------|
| 0.3.x   | Yes                |
| < 0.3   | No                 |

## Reporting a Vulnerability

If you discover a security vulnerability in NEXO Brain, please report it responsibly:

1. **Do NOT open a public issue**
2. Email **security@nexo-brain.com** with:
   - Description of the vulnerability
   - Steps to reproduce
   - Potential impact
3. You will receive a response within 48 hours
4. We will work with you to understand and fix the issue before any public disclosure

## Security Features

NEXO Brain includes built-in security:

- **4-layer memory poisoning defense** — validates all memory inputs
- **Secret redaction** — auto-detects and redacts API keys, tokens, passwords before storage
- **Quarantine queue** — new facts must earn trust before becoming knowledge
- **Local-only processing** — all vectors computed on CPU, no cloud dependencies
- **SQLite encryption support** — optional at-rest encryption

## Scope

The following are in scope:
- Memory injection/poisoning attacks
- Secret leakage through memory retrieval
- Authentication bypass in MCP tool calls
- Data exfiltration through plugin system

The following are out of scope:
- Vulnerabilities in upstream dependencies (report to them directly)
- Social engineering attacks
- Denial of service through excessive memory writes (rate limiting is the user's responsibility)
