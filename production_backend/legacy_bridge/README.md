# Legacy Bridge

This directory is for temporary migration bridges only.

Every bridge must declare:

```text
owner
legacy source
new target
reason
removal condition
deletion PR
```

No bridge may become a production fallback for the legacy agent loop,
`previous_response_id`, provider session, in-memory `ChatSession`, or direct
SQLite writes.

