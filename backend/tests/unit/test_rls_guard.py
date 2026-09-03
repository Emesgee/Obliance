"""ADR-0002 §Systemkontekst: a request-scoped tenant context without a user is a
bug, never a fallback. Pure — no database."""

from __future__ import annotations

import uuid

import pytest

from app.core.rls import RlsContextError, TenantContext, current, tenant


def test_request_context_without_user_raises():
    with pytest.raises(RlsContextError):
        with tenant(uuid.uuid4()):
            pass


def test_system_context_must_be_explicit():
    org = uuid.uuid4()
    with tenant(org, system=True) as ctx:
        assert ctx.is_system
        assert ctx.user_id is None
        assert ctx.org_id == str(org)


def test_user_context_carries_user_and_role():
    org, user = uuid.uuid4(), uuid.uuid4()
    with tenant(org, user_id=user, role="auditor") as ctx:
        assert not ctx.is_system
        assert ctx == TenantContext(org_id=str(org), user_id=str(user), role="auditor")
        assert current() is ctx


def test_context_is_restored_on_exit():
    assert current() is None
    with tenant(uuid.uuid4(), system=True):
        assert current() is not None
    assert current() is None


def test_nested_contexts_unwind_correctly():
    a, b = uuid.uuid4(), uuid.uuid4()
    with tenant(a, system=True) as outer:
        with tenant(b, system=True) as inner:
            assert current() is inner
        assert current() is outer
    assert current() is None
