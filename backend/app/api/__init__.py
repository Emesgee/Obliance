"""HTTP routers, one per module. Every data route runs inside a tenant context
(app.core.rls.tenant) resolved from the authenticated user — never without one."""
