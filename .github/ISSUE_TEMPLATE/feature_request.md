---
name: Feature request
about: Suggest an idea or improvement
title: ''
labels: enhancement
assignees: ''
---

**Problem**
What are you trying to do that dishka-fastmcp makes hard or impossible today?

**Proposed solution**
What you'd like to see. API sketches welcome.

**Alternatives considered**
Other approaches you weighed.

**Scope check**
dishka-fastmcp is deliberately a thin, correct bridge over FastMCP's native
extension points. It supports the scopes FastMCP can honor (`APP` and `REQUEST`);
`SESSION` is out until FastMCP exposes a session-teardown hook. Injection logic
stays delegated to dishka's `wrap_injection`.
