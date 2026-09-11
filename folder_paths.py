"""Hierarchical folders stored compatibly as major + slash-separated relative path."""


def parts(key):
    return (key[0], *key[1].split('/')) if len(key) > 1 and key[1] else (key[0],)


def packed(path):
    return path[0], '/'.join(path[1:])


def ancestors(key):
    path = parts(key)
    return [packed(path[:n]) for n in range(1, len(path) + 1)]


def contains(parent, child):
    a, b = parts(parent), parts(child)
    return b[:len(a)] == a


def ui_key(key):
    return key if len(key) > 1 and key[1] else (key[0],)
