from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from quayt_service.vessel_calls import (
    PostgresVesselCallRepository,
    VesselCallFilters,
    set_tenant_context,
)


def test_tenant_context_refuses_implicit_transaction() -> None:
    session = MagicMock(spec=Session)
    session.in_transaction.return_value = False
    with pytest.raises(RuntimeError, match="explicit transaction"):
        set_tenant_context(session, uuid4())
    session.execute.assert_not_called()


@pytest.mark.parametrize("enforced", [False, None])
@pytest.mark.parametrize("operation", ["list", "get", "summary", "ready"])
def test_repository_fails_closed_when_database_role_or_rls_is_unsafe(enforced, operation) -> None:
    session = MagicMock(spec=Session)
    session.in_transaction.return_value = True
    session.scalar.return_value = enforced
    sessions = MagicMock()
    sessions.begin.return_value.__enter__.return_value = session
    repository = PostgresVesselCallRepository(sessions)
    if operation == "ready":
        assert repository.ready() is False
    else:
        args = {
            "list": (uuid4(), VesselCallFilters()),
            "get": (uuid4(), uuid4()),
            "summary": (uuid4(),),
        }[operation]
        with pytest.raises(RuntimeError, match="row security is not enforced"):
            getattr(repository, operation)(*args)
    # Only role entry and its catalog check may run. No operational query/context.
    assert session.execute.call_count == 1
    assert session.scalar.call_count == 1
    session.scalars.assert_not_called()
