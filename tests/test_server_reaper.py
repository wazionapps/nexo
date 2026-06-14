"""Reaper selection safety: only orphaned NEXO servers may be reaped, never a
server with a live client, the warm resident, ourselves, or unrelated processes.
"""
from __future__ import annotations


def test_select_reapable_servers_only_orphans():
    import server

    rows = [
        {"pid": "100", "ppid": "1", "cmdline": "/py /Users/x/.nexo/core/server.py"},
        {"pid": "101", "ppid": "1", "cmdline": "/py /opt/homebrew/lib/node_modules/nexo-brain/src/server.py"},
        {"pid": "102", "ppid": "555", "cmdline": "/py /Users/x/.nexo/core/server.py"},   # live client
        {"pid": "103", "ppid": "1", "cmdline": "/py /Users/x/other/app/server.py"},       # not nexo
        {"pid": "104", "ppid": "1", "cmdline": "/py /Users/x/.nexo/core/cli.py"},          # not server.py
        {"pid": "200", "ppid": "1", "cmdline": "/py /Users/x/.nexo/core/server.py"},       # resident
        {"pid": "300", "ppid": "1", "cmdline": "/py /Users/x/.nexo/core/server.py"},       # self
    ]

    out = server._select_reapable_servers(rows, self_pid=300, resident_pid=200)
    assert set(out) == {100, 101}


def test_select_reapable_servers_keeps_everything_with_live_parent():
    import server

    rows = [
        {"pid": "10", "ppid": "9", "cmdline": "/py /Users/x/.nexo/core/server.py"},
        {"pid": "11", "ppid": "9", "cmdline": "/py /Users/x/.nexo/core/server.py"},
    ]
    assert server._select_reapable_servers(rows, self_pid=1, resident_pid=2) == []


def test_select_reapable_servers_tolerates_malformed_rows():
    import server

    rows = [
        {"pid": "x", "ppid": "1", "cmdline": "server.py nexo"},   # bad pid
        {"pid": "1", "ppid": None, "cmdline": "server.py nexo"},  # bad ppid
        {"cmdline": "server.py nexo"},                             # missing keys
    ]
    assert server._select_reapable_servers(rows, self_pid=99, resident_pid=98) == []
