# auth.md

cdotlib.org is a free, public, read-only REST API. This document describes how
software agents authenticate and register with it. The short version: they don't
have to.

## Agent audience

Any automated client — AI agents, LLM tools, scripts, or the
[cdot](https://github.com/SACGF/cdot) Python library — may use this API, as well
as humans with a browser or `curl`.

## Agent registration / provisioning

**No registration or provisioning is required.** There is no registration
endpoint (`register_uri`), no client onboarding, no account, and no API key to
request. Agents may begin calling the API immediately without any prior setup.

## Supported authentication methods

**None — access is anonymous and public.** There is no OAuth, no OpenID Connect,
no bearer tokens, and no protected resources. Every endpoint is open to all
callers, so there is no `/.well-known/oauth-protected-resource` or
`/.well-known/oauth-authorization-server` to discover.

## Credential use

**No credentials are used or accepted.** Do not send `Authorization` headers,
API keys, or cookies — they are ignored. Just make the request:

```
curl https://cdotlib.org/transcript/NM_000059.3
```

## More

- Usage guide for agents: <https://cdotlib.org/llms.txt>
- Machine-readable spec: <https://cdotlib.org/static/openapi.yaml>
