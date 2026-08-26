from unittest.mock import Mock

from tasks import sync_corporate_actions as task


def test_main_syncs_and_closes_the_session(monkeypatch):
    session = Mock()
    monkeypatch.setattr(task, "SessionLocal", Mock(return_value=session))
    monkeypatch.setattr(task, "sync_all",
                        Mock(return_value={"inserted": 2, "skipped": 1,
                                           "unparsed": 0, "ignored": 5}))

    result = task.main()

    assert result == {"inserted": 2, "skipped": 1, "unparsed": 0, "ignored": 5}
    session.close.assert_called_once()


def test_main_closes_the_session_even_when_sync_raises(monkeypatch):
    session = Mock()
    monkeypatch.setattr(task, "SessionLocal", Mock(return_value=session))
    monkeypatch.setattr(task, "sync_all", Mock(side_effect=RuntimeError("DNSE down")))

    try:
        task.main()
    except RuntimeError:
        pass

    session.close.assert_called_once()
