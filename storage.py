import sqlite3
from contextlib import contextmanager
from pathlib import Path


def connect(path):
    db = sqlite3.connect(path, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys = ON')
    return db


def initialize(db):
    if db.in_transaction:
        raise ValueError('initialize requires a connection outside a transaction')
    script = Path(__file__).with_name('schema.sql').read_text(encoding='utf-8')
    try:
        db.executescript(script)
    except BaseException:
        if db.in_transaction:
            db.execute('ROLLBACK')
        raise


@contextmanager
def atomic(db):
    nested = db.in_transaction
    if nested:
        db.execute('SAVEPOINT coldchain_write')
    else:
        db.execute('BEGIN IMMEDIATE')
    try:
        yield
        db.execute('RELEASE SAVEPOINT coldchain_write' if nested else 'COMMIT')
    except BaseException:
        if db.in_transaction:
            if nested:
                db.execute('ROLLBACK TO SAVEPOINT coldchain_write')
                db.execute('RELEASE SAVEPOINT coldchain_write')
            else:
                db.execute('ROLLBACK')
        raise


def require_name(value, label):
    if type(value) is not str or not value.strip():
        raise ValueError(f'{label} must be a nonempty string')
    return value
