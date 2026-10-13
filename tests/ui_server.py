"""Disposable browser-test server; never uses the user's database."""
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app

with tempfile.TemporaryDirectory() as directory:
    store = app.Store(Path(directory) / 'ui.sqlite')
    store.set_password('ui-test-password-only')
    store.save_object('account', dict(id='a', name='测试证券账户', currency='CNY'))
    store.save_object('security', dict(id='stock', name='示例持仓（测试数据）', code='TEST001', market='CN', type='stock', currency='CNY', unit='股', multiplier='1', rationale='用于界面验收的虚构数据'))
    store.save_object('price', dict(id='stock', value='120', date=app.now()[:10], source='虚构测试价格'))
    store.opening_draft(dict(batch='ui-fixture', date='2026-01-01', rows=[dict(account='a', currency='CNY', amount='80000'), dict(account='a', security='stock', quantity='1000', cost='100000')]))
    store.confirm_batch('ui-fixture')
    server = app.ThreadingHTTPServer(('127.0.0.1', 8766), app.Handler)
    server.store = store
    server.secure_cookie = False
    try:
        server.serve_forever()
    finally:
        server.server_close()
