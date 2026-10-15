import io
import json
import tempfile
import unittest
import zipfile
from decimal import Decimal as D
from pathlib import Path
from unittest.mock import patch
import app


class Acceptance(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.s = app.Store(Path(self.tmp.name) / 'main.sqlite')
        for aid in ('a', 'b'):
            self.s.save_object('account', dict(id=aid, name=aid, currency='CNY'))
        for sid, kind, price in [('stock', 'stock', '100'), ('fund', 'fund', '1')]:
            self.s.save_object('security', dict(id=sid, name=sid, code=sid, market='CN', type=kind, currency='CNY', unit='份', multiplier='1'))
            self.s.save_object('price', dict(id=sid, value=price, date=app.now()[:10], source='test'))

    def opening(self, amount='10000', positions=None):
        return self.s.opening_draft(dict(batch='initial', date='2026-01-01', rows=[dict(account='a', currency='CNY', amount=amount)] + (positions or [])))

    def init(self, amount='10000', positions=None):
        self.opening(amount, positions)
        self.s.confirm_batch('initial')

    def entry(self, kind, **kw):
        return self.s.transaction(dict(account='a', date='2026-02-01', kind=kind, **kw))

    def summary(self):
        return self.s.state()['summary']

    def cash(self, aid='a'):
        return D(next(c['amount'] for c in self.summary()['cash'] if c['account'] == aid))

    def test_AC01_opening_preview_no_double_charge(self):
        p = self.opening(positions=[dict(account='a', security='stock', quantity='10', cost='900')])
        self.assertEqual(self.cash(), 0)
        self.assertEqual(D(p['after']['total']), 11000)
        self.s.confirm_batch('initial')
        self.assertEqual(self.cash(), 10000)
        self.assertEqual(D(self.summary()['total']), 11000)

    def test_AC02_duplicate(self):
        self.init()
        with self.assertRaises(ValueError): self.s.confirm_batch('initial')
        with self.assertRaises(ValueError): self.opening()
        self.assertEqual(self.cash(), 10000)

    def test_AC03_buy_fee(self):
        self.init()
        self.entry('buy', security='stock', quantity='10', price='100', fee='5')
        self.assertEqual(self.cash(), 8995)
        h = self.summary()['holdings'][0]
        self.assertEqual(D(h['quantity']), 10)
        self.assertEqual(D(h['cost']), 1005)

    def test_AC04_transfer(self):
        self.init()
        self.entry('transfer', to_account='b', amount='1000', currency='CNY', counterparty='b')
        self.assertEqual(self.cash(), 9000)
        self.assertEqual(self.cash('b'), 1000)
        self.assertEqual(self.summary()['flows'], [])
        self.assertEqual(D(self.summary()['total']), 10000)

    def test_AC05_subscription(self):
        self.init()
        r = self.entry('subscribe', security='fund', amount='1000')
        self.assertEqual(self.cash(), 10000)
        self.entry('subscribe_debit', request=r['id'])
        self.assertEqual(self.cash(), 9000)
        self.assertEqual(D(self.summary()['total']), 10000)
        self.entry('subscribe_confirm', request=r['id'], quantity='999', price='1', fee='1')
        self.assertEqual(D(self.summary()['total']), 9999)
        self.assertEqual(self.summary()['transit'], [])
        with self.assertRaises(ValueError):
            self.entry('subscribe_confirm', request=r['id'], quantity='999', price='1', fee='1')

    def test_AC06_redemption(self):
        self.init(positions=[dict(account='a', security='fund', quantity='999', cost='1000')])
        r = self.entry('redeem', security='fund', quantity='100')
        self.entry('redeem_confirm', request=r['id'], price='1', fee='1')
        self.assertEqual(D(self.summary()['holdings'][0]['quantity']), 899)
        self.assertEqual(D(self.summary()['transit'][0]['value']), 99)
        self.assertEqual(self.cash(), 10000)
        self.entry('redeem_receive', request=r['id'])
        self.assertEqual(self.cash(), 10099)
        self.assertEqual(self.summary()['transit'], [])

    def test_AC07_unknown_cost_price(self):
        self.init(positions=[dict(account='a', security='stock', quantity='1')])
        self.assertIsNone(self.summary()['holdings'][0]['unrealized'])
        with self.s.connect() as db: db.execute("DELETE FROM objects WHERE kind='price' AND id='stock'")
        s = self.summary()
        self.assertFalse(s['complete'])
        self.assertIsNone(s['holdings'][0]['value'])
        self.assertEqual(D(s['total']), 10000)

    def test_AC08_withdrawal_is_not_loss(self):
        self.init('1000000')
        self.s.save_object('config', dict(baseline=dict(date='2026-01-01', amount='1000000', confirmed=True), loss_limit='100000'))
        self.entry('withdraw', amount='50000', currency='CNY', counterparty='spending')
        self.assertEqual(self.cash(), 950000)
        self.assertEqual(D(self.summary()['loss']), 0)
        self.assertFalse(any(n['id'] == 'loss' and n['active'] for n in self.s.state()['notice']))

    def test_AC09_read_is_not_resolved(self):
        self.init('10', [dict(account='a', security='stock', quantity='10', cost='1000')])
        n = next(n for n in self.s.state()['notice'] if n['category'] == 'risk')
        self.s.save_object('notice', dict(id=n['id'], read=True))
        n = next(x for x in self.s.state()['notice'] if x['id'] == n['id'])
        self.assertTrue(n['read'])
        self.assertTrue(n['active'])

    def configure(self):
        self.s.save_object('config', dict(model_url='https://model.example/v1/chat/completions', model='test', consent=True, input_price='1', output_price='1'))

    def research(self, sid='stock'):
        self.configure()
        task = self.s.create_task(dict(type='research', security=sid, question='verify', depth='深度', sources=[dict(url='https://example.com/report', date='2026-02-01', text='verified source')]))
        answer = dict(logic='获支持', facts=['fact'], inferences=[], support=['support'], against=['against'], unknown=[], conclusion='result', constraints='constraints', watch=['watch'], changes='first', id='malicious', snapshot={})
        output = dict(choices=[dict(message=dict(content=app.encoded(answer)), finish_reason='stop')], usage=dict(prompt_tokens=100, completion_tokens=100))
        with patch.dict('os.environ', {'INVEST_MODEL_KEY': 'test-only'}), patch('app.fetch_json', return_value=output): self.s.run_next()
        t = next(t for t in self.s.state()['task'] if t['id'] == task['id'])
        report = next(r for r in self.s.state()['report'] if r['id'] == t['report'])
        self.assertEqual(report['status'], 'success', report.get('error'))
        return report

    def test_AC10_immutable_report(self):
        self.init(positions=[dict(account='a', security='stock', quantity='10', cost='1000')])
        r = self.research()
        self.assertNotEqual(r['id'], 'malicious')
        self.assertTrue(r['snapshot']['holdings'])
        self.entry('sell', security='stock', quantity='1', price='100', fee='0')
        current = next(x for x in self.s.state()['report'] if x['id'] == r['id'])
        self.assertEqual(current['snapshot'], r['snapshot'])
        self.assertNotEqual(current['snapshot']['holdings'], self.summary()['holdings'])

    def test_AC11_failed_sources(self):
        self.init(positions=[dict(account='a', security='stock', quantity='10')])
        self.configure()
        self.s.create_task(dict(type='research', security='stock', question='test', depth='普通'))
        with patch.dict('os.environ', {'INVEST_MODEL_KEY': 'test-only'}): self.s.run_next()
        r = self.s.state()['report'][0]
        self.assertEqual(r['status'], 'failed')
        self.assertEqual(r['logic'], '资料不足')
        self.assertIsNone(r['conclusion'])

    def test_AC12_decision_only(self):
        self.init(positions=[dict(account='a', security='stock', quantity='10')])
        r = self.research()
        before = self.s.state()['entries']
        self.s.save_object('decision', dict(report=r['id'], choice='采纳', reason='evidence'))
        self.assertEqual(self.s.state()['entries'], before)

    def test_AC13_retry_dedup(self):
        self.init()
        t = self.s.create_task(dict(type='check', event='weekly-check'))
        self.assertEqual(self.s.create_task(dict(type='check', event='weekly-check'))['id'], t['id'])
        self.s.run_next()
        self.s.task_action(dict(id=t['id'], action='retry'))
        self.s.run_next()
        with self.s.connect() as db: self.assertEqual(len(self.s.objects(db, 'snapshot')), 1)
        self.assertEqual(len([n for n in self.s.state()['notice'] if n['id'] == 'task:weekly-check']), 1)

    def test_AC14_restore(self):
        self.init(positions=[dict(account='a', security='fund', quantity='999', cost='1000')])
        r = self.research('fund')
        self.s.save_object('decision', dict(report=r['id'], choice='暂缓', reason='strategy'))
        other = app.Store(Path(self.tmp.name) / 'restored.sqlite')
        other.restore(self.s.backup())
        a, b = self.s.state(), other.state()
        for key in ('entries', 'report', 'decision', 'account', 'security', 'config'): self.assertEqual(a[key], b[key])
        self.assertEqual(a['summary']['total'], b['summary']['total'])
        with self.assertRaises(ValueError): other.restore(self.s.backup())

    def test_insufficient_and_unknown_fee(self):
        self.init('100')
        for fields in (dict(quantity='10', price='100', fee='0'), dict(quantity='1', price='1')):
            with self.assertRaises(ValueError): self.entry('buy', security='stock', **fields)
        with self.assertRaises(ValueError): self.entry('sell', security='stock', quantity='1', price='1', fee='0')
        self.assertEqual(self.cash(), 100)

    def test_correction_and_undo_are_atomic(self):
        self.init()
        e = self.entry('buy', security='stock', quantity='10', price='100', fee='5')
        self.entry('buy', security='stock', quantity='5', price='100', fee='5', replaces=e['id'], reason='correct')
        self.assertEqual(self.cash(), 9495)
        self.assertEqual(len(self.s.state()['entries']), 3)
        with self.assertRaises(ValueError): self.s.confirm_batch('initial', undo=True)
        self.assertEqual(self.cash(), 9495)

    def test_decimal_weighted_average(self):
        self.init('1000')
        self.entry('buy', security='fund', quantity='0.1', price='1.23', fee='0')
        self.assertEqual(self.cash(), D('999.877'))
        self.entry('buy', security='fund', quantity='0.2', price='2.46', fee='0')
        self.entry('sell', security='fund', quantity='0.1', price='3', fee='0.01')
        self.assertEqual(D(self.summary()['holdings'][0]['cost']), D('0.410'))
        self.assertEqual(D(self.summary()['realized'][0]['amount']), D('0.085'))

    def test_stale_quote_retained_and_older_update_rejected(self):
        self.init(positions=[dict(account='a', security='stock', quantity='1')])
        with self.s.connect() as db: db.execute("DELETE FROM objects WHERE kind='price' AND id='stock'")
        self.s.save_object('price', dict(id='stock', value='123', date='2020-01-01', source='historical'))
        self.assertEqual(D(self.summary()['holdings'][0]['value']), 123)
        self.assertTrue(self.summary()['stale'])
        with self.assertRaises(ValueError): self.s.save_object('price', dict(id='stock', value='1', date='2019-01-01', source='old'))

    def test_tushare_sync_updates_value_without_changing_ledger(self):
        self.s.save_object('security', dict(id='cn-share', name='旧名称', code='000001', market='CN', type='stock', currency='CNY', unit='股', multiplier='1'))
        self.init(positions=[dict(account='a', security='cn-share', quantity='10', cost='80')])
        moment = app.dt.datetime.now(app.TZ)
        day = (moment.date() if moment.hour >= 18 else moment.date() - app.dt.timedelta(days=1)).strftime('%Y%m%d')
        before = self.s.state()['entries']

        def query(api, params, fields):
            if api == 'trade_cal':
                return [dict(cal_date=day, is_open=1)]
            if api == 'stock_basic':
                return [dict(ts_code='000001.SZ', symbol='000001', name='平安银行', exchange='SZSE', list_status='L', list_date='19910403')] if params['exchange'] == 'SZSE' and params['list_status'] == 'L' else []
            if api == 'daily':
                return [dict(ts_code='000001.SZ', trade_date=day, close=12)]
            if api == 'daily_basic':
                return [dict(ts_code='000001.SZ', trade_date=day, close=12, pe=5, pb=1, total_mv=100)]
            self.fail(api)

        with patch.dict('os.environ', {'TUSHARE_TOKEN': 'secret'}), patch('market_data.TuShare.query', side_effect=query):
            self.assertEqual(self.s.sync_market()['updated'], 1)
        state = self.s.state()
        h = next(h for h in state['summary']['holdings'] if h['security'] == 'cn-share')
        self.assertEqual(D(h['value']), 120)
        self.assertEqual(D(h['unrealized']), 40)
        self.assertEqual(h['market_data']['source'], 'TuShare Pro')
        self.assertEqual(state['entries'], before)
        backup = self.s.backup()
        restored = app.Store(Path(self.tmp.name) / 'tushare-restored.sqlite')
        restored.restore(backup)
        self.assertEqual(restored.state()['summary']['holdings'], state['summary']['holdings'])

        with patch.dict('os.environ', {'TUSHARE_TOKEN': 'secret'}), patch('market_data.TuShare.query', side_effect=lambda api, params, fields: [] if api in {'daily', 'daily_basic'} else query(api, params, fields)):
            self.assertEqual(self.s.sync_market()['updated'], 0)
        self.assertEqual(next(x for x in self.s.state()['summary']['holdings'] if x['security'] == 'cn-share')['value'], h['value'])

        with self.s.connect() as db:
            db.execute("DELETE FROM objects WHERE kind='market_daily'")
        with patch.dict('os.environ', {'TUSHARE_TOKEN': 'secret'}), patch('market_data.TuShare.query', side_effect=lambda api, params, fields: [] if api == 'daily_basic' else query(api, params, fields)):
            with self.assertRaisesRegex(ValueError, '每日估值指标缺少'):
                self.s.sync_market()
        self.assertEqual(next(h for h in self.s.state()['summary']['holdings'] if h['security'] == 'cn-share')['value'], h['value'])
        self.assertEqual(self.s.state()['entries'], before)
        with patch.dict('os.environ', {'TUSHARE_TOKEN': 'secret'}), patch('market_data.TuShare.query', side_effect=ValueError('TuShare 权限不足或 Token 无效')):
            with self.assertRaisesRegex(ValueError, '权限不足'):
                self.s.sync_market()
        self.assertFalse(self.s.state()['market_sync']['ok'])
        self.assertEqual(next(x for x in self.s.state()['summary']['holdings'] if x['security'] == 'cn-share')['value'], h['value'])

    def test_tushare_identity_rejects_ambiguous_cn_code(self):
        from market_data import identity
        rows = [dict(symbol='000001', ts_code='000001.SZ', name='甲', exchange='SZSE', list_status='L'), dict(symbol='000001', ts_code='000001.SH', name='乙', exchange='SSE', list_status='L')]
        with self.assertRaisesRegex(ValueError, '无法唯一映射'):
            identity(dict(code='000001', market='CN'), rows)
        self.assertEqual(identity(dict(code='000001', market='SZ'), rows)['ts_code'], '000001.SZ')

    def test_cumulative_plans_and_separate_scenarios(self):
        self.init('100000')
        self.s.save_object('account', dict(id='a', name='a', currency='CNY', withdrawable='100000', liquidity_verified=True, liquidity_date=app.now()[:10], liquidity_source='manual'))
        self.s.save_object('config', dict(plans=[dict(id='one', date='2027-10-12', amount='50000', scenario='base', mode='cumulative'), dict(id='two', date='2029-10-12', amount='150000', scenario='base', mode='cumulative'), dict(id='house', date='2029-10-12', amount='1000000', scenario='house', mode='cumulative')]))
        p = self.s.state()['portfolio']
        targets = p['scenarios']['base']['targets']
        self.assertEqual(D(targets[1]['increment']), 100000)
        self.assertEqual(D(targets[1]['cash_coverage']), 50000)
        self.assertEqual(D(targets[1]['gap']), 50000)
        self.assertEqual(D(p['scenarios']['house']['targets'][0]['gap']), 900000)

    def test_password_and_backup_tamper(self):
        self.s.set_password('test-password-12345')
        self.assertTrue(self.s.login('test-password-12345'))
        self.assertFalse(self.s.login('wrong'))
        backup = json.loads(self.s.backup())
        backup['payload']['entries'].append(dict(body='{}'))
        with self.assertRaises(ValueError): app.Store(Path(self.tmp.name) / 'bad.sqlite').restore(app.encoded(backup))

    def test_xlsx_formula_is_reference_only(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as z:
            z.writestr('xl/workbook.xml', '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="test" sheetId="1" r:id="rId1"/></sheets></workbook>')
            z.writestr('xl/_rels/workbook.xml.rels', '<Relationships><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
            z.writestr('xl/worksheets/sheet1.xml', '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1"><v>10</v></c><c r="B1"><f>A1*2</f><v>20</v></c></row></sheetData></worksheet>')
        cells = app.read_xlsx(stream.getvalue())['sheets'][0]['cells']
        self.assertEqual(cells[0]['value'], '10')
        self.assertIsNone(cells[1]['value'])
        self.assertEqual(cells[1]['formula'], 'A1*2')
        self.assertEqual(cells[1]['cached_reference'], '20')


import http.client
import threading


class BoundaryTests(unittest.TestCase):
    setUp = Acceptance.setUp
    init = Acceptance.init
    opening = Acceptance.opening
    entry = Acceptance.entry
    summary = Acceptance.summary
    cash = Acceptance.cash
    configure = Acceptance.configure
    research = Acceptance.research
    def test_dividend_reinvestment_has_no_cash_or_double_asset(self):
        self.init(positions=[dict(account='a', security='fund', quantity='100', cost='100')])
        self.entry('income', income_mode='reinvest', security='fund', currency='CNY', amount='10', quantity='9', price='1', fee='1')
        self.assertEqual(self.cash(), 10000)
        h = self.summary()['holdings'][0]
        self.assertEqual(D(h['quantity']), 109)
        self.assertEqual(D(h['cost']), 110)
        self.assertEqual(D(self.summary()['total']), 10109)

    def test_fund_correction_preserves_request_identity(self):
        self.init()
        r = self.entry('subscribe', security='fund', amount='1000')
        self.entry('subscribe_debit', request=r['id'])
        corrected = self.entry('subscribe', security='fund', amount='1001', replaces=r['id'], reason='actual debit')
        self.assertEqual(self.cash(), 8999)
        self.entry('subscribe', security='fund', amount='1002', replaces=corrected['id'], reason='second correction')
        self.assertEqual(self.cash(), 8998)
        self.entry('subscribe_confirm', request=r['id'], quantity='1001', price='1', fee='1')
        self.assertEqual(D(self.summary()['holdings'][0]['quantity']), 1001)

    def test_posted_draft_cannot_duplicate(self):
        self.init()
        draft = self.s.save_object('draft', dict(kind='buy', account='a', security='stock', date='2026-02-01', quantity='1', price='100', fee='0'))
        self.entry('buy', security='stock', quantity='1', price='100', fee='0', draft_id=draft['id'])
        with self.assertRaises(ValueError):
            self.entry('buy', security='stock', quantity='1', price='100', fee='0', draft_id=draft['id'])
        self.assertEqual(self.cash(), 9900)

    def test_unpriced_data_does_not_resolve_existing_risk(self):
        self.init('10', [dict(account='a', security='stock', quantity='10', cost='1000')])
        with self.s.connect() as db: db.execute("DELETE FROM objects WHERE kind='price'")
        notices = self.s.state()['notice']
        risks = [n for n in notices if n['category'] == 'risk']
        self.assertTrue(risks)
        self.assertTrue(all(n['active'] and n['check_pending'] for n in risks))

    def test_malformed_model_response_fails_cleanly(self):
        self.init(positions=[dict(account='a', security='stock', quantity='1')])
        self.configure()
        self.s.create_task(dict(type='research', security='stock', depth='普通', question='test', sources=[dict(url='https://example.com', date='2026-01-01', text='evidence')]))
        with patch.dict('os.environ', {'INVEST_MODEL_KEY': 'test-only'}), patch('app.fetch_json', return_value={'choices': []}): self.s.run_next()
        task = self.s.state()['task'][0]
        self.assertEqual(task['status'], 'failed')
        self.assertEqual(self.s.state()['report'][0]['logic'], '资料不足')

    def test_invalid_plans_roll_back_config(self):
        self.init()
        self.s.save_object('config', dict(concentration='0.3'))
        with self.assertRaises(ValueError):
            self.s.save_object('config', dict(plans=[dict(id='a', date='2027-01-01', amount='100', scenario='base', mode='cumulative'), dict(id='b', date='2028-01-01', amount='50', scenario='base', mode='cumulative')]))
        self.assertEqual(self.s.state()['config']['concentration'], '0.3')

    def test_connection_test_reserves_budget(self):
        self.init()
        self.s.save_object('config', dict(daily_budget='0.01', task_budget='0.5'))
        with self.assertRaises(ValueError): self.s.create_task(dict(type='test_model'))

    def test_http_auth_csrf_and_backup(self):
        self.s.set_password('http-test-password-123')
        server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        server.store, server.secure_cookie = self.s, False
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
        try:
            connection.request('GET', '/api/state')
            response = connection.getresponse()
            self.assertEqual(response.status, 401)
            response.read()
            connection.request('POST', '/api/login', app.encoded(dict(password='http-test-password-123')), {'Content-Type': 'application/json'})
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            cookie = response.getheader('Set-Cookie').split(';')[0]
            self.assertIn('HttpOnly', response.getheader('Set-Cookie'))
            csrf = json.loads(response.read())['csrf']
            connection.request('POST', '/api/save', app.encoded(dict(kind='draft', data=dict(kind='buy'))), {'Content-Type': 'application/json', 'Cookie': cookie})
            response = connection.getresponse()
            self.assertEqual(response.status, 403)
            response.read()
            connection.request('POST', '/api/save', app.encoded(dict(kind='draft', data=dict(kind='buy'))), {'Content-Type': 'application/json', 'Cookie': cookie, 'X-CSRF-Token': csrf})
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            response.read()
            connection.request('GET', '/api/backup', headers={'Cookie': cookie})
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            payload = json.loads(response.read())['payload']
            self.assertNotIn('auth', payload)
            self.assertNotIn('http-test-password-123', app.encoded(payload))
        finally:
            connection.close()
            server.shutdown()
            server.server_close()
            thread.join(5)

    def test_risk_recheck_clears_pending_when_data_returns(self):
        self.init('10', [dict(account='a', security='stock', quantity='10', cost='1000')])
        with self.s.connect() as db: db.execute("DELETE FROM objects WHERE kind='price'")
        self.s.state()
        self.s.save_object('price', dict(id='stock', value='100', date=app.now()[:10], source='restored'))
        risks = [n for n in self.s.state()['notice'] if n['category'] == 'risk']
        self.assertTrue(risks)
        self.assertTrue(all(n['active'] and not n.get('check_pending') for n in risks))

    def test_research_prompt_contains_prior_evidence_and_rationale_changes(self):
        self.init(positions=[dict(account='a', security='stock', quantity='10')])
        report = self.research()
        stock = next(s for s in self.s.state()['security'] if s['id'] == 'stock')
        self.s.save_object('security', dict(stock, rationale='changed investment reason'))
        task = self.s.create_task(dict(type='research', security='stock', question='verify', depth='深度', sources=report['sources']))
        answer = dict(logic='存在疑点', facts=[], inferences=[], support=[], against=['new issue'], unknown=[], conclusion='review', constraints='constraints', watch=[], changes='reason changed')
        output = dict(choices=[dict(message=dict(content=app.encoded(answer)), finish_reason='stop')], usage=dict(prompt_tokens=100, completion_tokens=100))
        with patch.dict('os.environ', {'INVEST_MODEL_KEY': 'test-only'}), patch('app.fetch_json', return_value=output) as fetch:
            self.s.run_next()
            prompt = json.loads(fetch.call_args[0][1]['messages'][1]['content'])
            self.assertEqual(prompt['previous_report']['conclusion'], report['conclusion'])
            self.assertEqual(prompt['security']['rationale'], 'changed investment reason')
        reports = self.s.state()['report']
        self.assertEqual(len(reports), 2)
        self.assertEqual(reports[-1]['version'], 2)

    def test_asset_control_difference_blocks_confirm_until_revised(self):
        data = dict(batch='control', date='2026-01-01', rows=[dict(account='a', amount='10000', currency='CNY'), dict(account='a', security='stock', quantity='10', cost='900')], controls=[dict(account='a', currency='CNY', amount='11200', type='asset_total')])
        preview = self.s.opening_draft(data)
        self.assertEqual(D(preview['controls'][0]['difference']), -200)
        with self.assertRaises(ValueError): self.s.confirm_batch('control')
        self.assertEqual(self.cash(), 0)
        data['controls'][0]['amount'] = '11000'
        data['revise'] = True
        self.s.opening_draft(data)
        self.s.confirm_batch('control')
        self.assertEqual(self.cash(), 10000)
        self.assertEqual(D(self.summary()['total']), 11000)


if __name__ == '__main__': unittest.main()
