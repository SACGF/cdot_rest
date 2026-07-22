# auth.md

**No authentication.** cdotlib.org is a free, public, read-only REST API. Any
client — AI agents, scripts, or humans — may call every endpoint anonymously.
There is no API key, no OAuth, no registration, and no rate-limit signup.

Access method: send the request, get JSON back.

```
curl https://cdotlib.org/transcript/NM_000059.3
```

See <https://cdotlib.org/llms.txt> for usage and
<https://cdotlib.org/static/openapi.yaml> for the machine-readable spec.
