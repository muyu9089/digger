"""Validated end-of-day A-share data from TuShare Pro."""
import datetime as dt
import json
import os
import urllib.error
import urllib.request
from decimal import Decimal, InvalidOperation


class TuShare:
    source = 'TuShare Pro'

    def __init__(self):
        self.token = os.environ.get('TUSHARE_TOKEN')
        if not self.token:
            raise ValueError('服务端未设置 TUSHARE_TOKEN')

    def query(self, api, params, fields):
        body = json.dumps({'api_name': api, 'token': self.token, 'params': params, 'fields': ','.join(fields)}).encode()
        request = urllib.request.Request('https://api.tushare.pro', body, {'Content-Type': 'application/json'}, method='POST')
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                raise ValueError('TuShare 接口发生重定向')
        try:
            with urllib.request.build_opener(NoRedirect).open(request, timeout=30) as response:
                raw = response.read(4_000_001)
        except (urllib.error.URLError, TimeoutError):
            raise ValueError('TuShare 连接失败，请检查网络和服务状态') from None
        if len(raw) > 4_000_000:
            raise ValueError('TuShare 响应过大')
        try:
            result = json.loads(raw)
            if result['code'] != 0:
                raise ValueError('TuShare 权限不足或 Token 无效（请检查接口积分）' if result['code'] == 2002 else 'TuShare 接口错误，代码：' + str(result['code']))
            data = result['data']
            if not isinstance(data['fields'], list) or not set(fields) <= set(data['fields']) or not isinstance(data['items'], list):
                raise ValueError('TuShare 响应字段不完整')
            if any(not isinstance(row, list) or len(row) != len(data['fields']) for row in data['items']):
                raise ValueError('TuShare 数据行不完整')
            return [dict(zip(data['fields'], row)) for row in data['items']]
        except (KeyError, TypeError, json.JSONDecodeError):
            raise ValueError('TuShare 响应结构无效') from None

    def test(self):
        day = '20240102'
        self.query('trade_cal', {'exchange': 'SSE', 'start_date': day, 'end_date': day}, ['cal_date', 'is_open'])
        self.query('stock_basic', {'ts_code': '000001.SZ'}, ['ts_code', 'symbol', 'name'])
        self.query('daily', {'ts_code': '000001.SZ', 'trade_date': day}, ['ts_code', 'trade_date', 'close'])
        self.query('daily_basic', {'ts_code': '000001.SZ', 'trade_date': day}, ['ts_code', 'trade_date', 'close', 'pe', 'pb', 'total_mv'])
        return {'ok': True, 'message': 'TuShare Token 与四项接口权限验证通过'}


def positive(value, label):
    try:
        result = Decimal(str(value))
        if not result.is_finite() or result <= 0 or result > Decimal('1e18') or result.as_tuple().exponent < -12:
            raise ValueError
        return str(result)
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError(label + '无效') from None


def finite(value, label):
    if value is None:
        return None
    try:
        result = Decimal(str(value))
        if not result.is_finite() or abs(result) > Decimal('1e18') or result.as_tuple().exponent < -12:
            raise ValueError
        return str(result)
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError(label + '无效') from None


def iso(value):
    try:
        return dt.datetime.strptime(value, '%Y%m%d').date().isoformat()
    except (ValueError, TypeError):
        raise ValueError('TuShare 日期无效') from None


def identity(security, rows):
    code = str(security['code']).upper().strip()
    market = str(security['market']).upper().strip()
    suffix = {'SSE': 'SH', 'SH': 'SH', 'SZSE': 'SZ', 'SZ': 'SZ', 'BSE': 'BJ', 'BJ': 'BJ'}.get(market)
    if '.' in code:
        symbol, code_suffix = code.rsplit('.', 1)
        if suffix and code_suffix != suffix:
            raise ValueError('证券代码与市场不一致：' + code)
        suffix = code_suffix
    else:
        symbol = code
    if market not in {'CN', 'A', 'ASHARE', 'SSE', 'SH', 'SZSE', 'SZ', 'BSE', 'BJ'} or not symbol.isdigit() or len(symbol) != 6:
        return None
    matches = [row for row in rows if row.get('symbol') == symbol and (suffix is None or row.get('ts_code') == symbol + '.' + suffix)]
    if len(matches) != 1:
        raise ValueError('证券代码和市场无法唯一映射：' + market + ':' + code)
    row = matches[0]
    if not row.get('name') or row.get('exchange') != {'SH': 'SSE', 'SZ': 'SZSE', 'BJ': 'BSE'}.get(row['ts_code'].rsplit('.', 1)[-1]) or row.get('list_status') not in {'L', 'D'}:
        raise ValueError('证券基础信息不完整或交易所不一致：' + code)
    return row
