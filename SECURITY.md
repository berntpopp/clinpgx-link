# Security Policy

`clinpgx-link` is a read-only FastMCP backend in the GeneFoundry fleet. It is unauthenticated
by design and MUST be reached only through the `genefoundry-router` or an authorized reverse proxy at the
trust boundary — never published directly without an enclosing gateway. Research use only; not clinical decision support.

## Reporting a vulnerability

Report suspected vulnerabilities privately to the maintainer (bernt.popp@charite.de)
rather than opening a public issue. Include reproduction steps and affected revision.

## Required repository security settings (operator follow-up)

GitHub **secret scanning** and **push protection** are repository settings, not workflow
files, so they cannot be enabled from a pull request. An operator with admin rights on the
repository must enable them:

```bash
gh api -X PATCH repos/berntpopp/clinpgx-link \
  -f "security_and_analysis[secret_scanning][status]=enabled" \
  -f "security_and_analysis[secret_scanning_push_protection][status]=enabled"
```

Verify both are `enabled`:

```bash
gh api repos/berntpopp/clinpgx-link --jq ".security_and_analysis"
```

Expected output includes:

```json
{
  "secret_scanning": { "status": "enabled" },
  "secret_scanning_push_protection": { "status": "enabled" }
}
```

Code scanning (CodeQL) is configured for this repository; this document tracks only the secret-scanning and
push-protection settings, which are the outstanding operator action.
