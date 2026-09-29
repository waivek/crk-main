from tools.todo.core.history import History, push, redo, undo


def test_push_undo_redo():
    h = push(push(History(0), 1), 2)
    assert (h.past, h.present, h.future) == ((0, 1), 2, ())
    h = undo(h)
    assert (h.present, h.future) == (1, (2,))
    h = undo(h)
    assert h.present == 0 and not h.can_undo
    h = redo(h)
    assert h.present == 1 and h.can_redo


def test_undo_redo_at_edges_are_noops():
    h = History(0)
    assert undo(h) is h
    assert redo(h) is h


def test_push_clears_future():
    h = push(undo(push(History(0), 1)), 5)
    assert (h.past, h.present, h.future) == ((0,), 5, ())


def test_push_same_value_is_noop():
    h = History(0)
    assert push(h, 0) is h


def test_limit():
    h = History(0)
    for i in range(1, 10):
        h = push(h, i, limit=3)
    assert h.past == (6, 7, 8)
