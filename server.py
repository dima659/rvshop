#!/usr/bin/env python3
"""RAVOUT SHMOT — Telegram Mini App: сервер + бот. Только стандартная библиотека Python 3.9+."""
import os, json, time, hmac, hashlib, sqlite3, threading, uuid, base64, re, urllib.request, urllib.parse
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path

ROOT = Path(__file__).parent; DATA = Path(os.getenv('DATA_DIR') or ROOT)
TOKEN = os.getenv('BOT_TOKEN', ''); URL = (os.getenv('WEBAPP_URL') or os.getenv('RENDER_EXTERNAL_URL') or '').rstrip('/')
ADMINS = {int(x) for x in re.split(r'[,\s]+', os.getenv('ADMIN_IDS', '')) if x.isdigit()}
PORT = int(os.getenv('PORT', '8080')); DEV = os.getenv('DEV') == '1'
DBURL = os.getenv('DATABASE_URL', ''); PG = DBURL.startswith('postgres')
SECRET = hashlib.sha256(TOKEN.encode()).hexdigest()[:32]
LIVE_CH = '@Ravoutshmotlive'; _subs = {}; CHECK_ADMINS = os.getenv('CHECK_ADMINS') == '1'
RS = {'new': 'Новая', 'work': 'В работе', 'done': 'Выполнена', 'rejected': 'Отклонена'}
OS = {'new': 'Новый', 'work': 'В работе', 'shipped': 'Отправлен', 'done': 'Завершён', 'cancel': 'Отменён'}

# База: PostgreSQL, если задан DATABASE_URL (данные хранятся вне сервера и не пропадают), иначе локальный SQLite.
LK = threading.Lock(); conn = None
def connect():
    global conn, DATA
    if PG:
        import psycopg
        from psycopg.rows import dict_row
        conn = psycopg.connect(DBURL, autocommit=True, row_factory=dict_row, connect_timeout=15)
    else:
        try: DATA.mkdir(parents=True, exist_ok=True); (DATA / '.w').write_text('1')
        except OSError: DATA = ROOT / 'data'; DATA.mkdir(exist_ok=True); print('ВНИМАНИЕ: DATABASE_URL не задан, данные пишутся в', DATA, 'и могут пропасть при перезапуске.')
        conn = sqlite3.connect(DATA / 'shop.db', check_same_thread=False, isolation_level=None); conn.row_factory = sqlite3.Row
def ex(sql, a=()):
    if PG: sql = sql.replace('?', '%s')
    for t in (1, 2):
        try:
            if conn is None: connect()
            return conn.execute(sql, a)
        except Exception as e:
            if t == 2 or not PG or type(e).__name__ not in ('OperationalError', 'InterfaceError'): raise
            connect()
def rows(sql, *a):
    with LK: return [dict(r) for r in ex(sql, a).fetchall()]
def run(sql, *a):
    with LK:
        l = sql.lstrip().lower(); ins = l.startswith('insert into') and not l.startswith(('insert into images', 'insert into meta'))
        cur = ex(sql + (' returning id' if PG and ins else ''), a)
        return (cur.fetchone()['id'] if ins else None) if PG else cur.lastrowid
PK = 'bigserial primary key' if PG else 'integer primary key autoincrement'; BIG = 'bigint' if PG else 'integer'; BLOB = 'bytea' if PG else 'blob'
for q in (f'create table if not exists products(id {PK},title text,brand text,category text,price {BIG} default 0,descr text,photo text,active integer default 1)',
          f"create table if not exists requests(id {PK},uid {BIG},uname text,name text,text text,photo text,status text default 'new',reply text default '',ts {BIG})",
          f"create table if not exists orders(id {PK},uid {BIG},uname text,name text,items text,total {BIG},contact text,comment text,status text default 'new',ts {BIG})",
          f'create table if not exists images(name text primary key,mime text,data {BLOB})',
          'create table if not exists meta(k text primary key,v text)'): run(q)
if not rows("select 1 as x from meta where k='seeded'"):
  if not rows('select 1 as x from products limit 1'):
    for t, b, c in [('Пуховик оверсайз', 'Бренд', 'Куртки'), ('Джинсы карго широкие', 'Бренд', 'Штаны'), ('Кроссовки чанки', 'Бренд', 'Обувь'), ('Кепка с вышивкой', 'Бренд', 'Аксессуары')]:
        run('insert into products(title,brand,category,price,descr,photo) values(?,?,?,0,?,?)', t, b, c, 'Пример товара: измените или удалите его в админ-панели.', '')
  run("insert into meta(k,v) values('seeded','1')")

def tg(method, **p):
    if not TOKEN: return {}
    try:
        req = urllib.request.Request(f'https://api.telegram.org/bot{TOKEN}/{method}', json.dumps(p).encode(), {'Content-Type': 'application/json'})
        return json.load(urllib.request.urlopen(req, timeout=35))
    except Exception as e:
        print('tg error', method, e); return {}
def notify(uid, text):
    threading.Thread(target=tg, args=('sendMessage',), kwargs={'chat_id': uid, 'text': text}, daemon=True).start()

def is_sub(uid, fresh=False):
    """Подписан ли пользователь на live-канал. Бот должен быть администратором канала."""
    if DEV or not TOKEN or (uid in ADMINS and not CHECK_ADMINS): return True
    if not fresh and _subs.get(uid, 0) > time.time(): return True
    r = tg('getChatMember', chat_id=LIVE_CH, user_id=uid)
    if not r.get('ok'): print('Не удалось проверить подписку (добавьте бота админом в канал):', r); return True
    st = r['result'].get('status')
    if st in ('member', 'administrator', 'creator') or (st == 'restricted' and r['result'].get('is_member')):
        _subs[uid] = time.time() + 60; return True
    _subs.pop(uid, None); return False

def auth(init):
    if not init: return {'id': 0, 'first_name': 'Dev', 'username': 'dev'} if DEV else None
    try:
        d = dict(urllib.parse.parse_qsl(init, keep_blank_values=True)); h = d.pop('hash')
        chk = '\n'.join(f'{k}={d[k]}' for k in sorted(d))
        key = hmac.new(b'WebAppData', TOKEN.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(hmac.new(key, chk.encode(), hashlib.sha256).hexdigest(), h): return None
        if time.time() - int(d.get('auth_date', 0)) > 86400: return None
        return json.loads(d['user'])
    except Exception: return None

def save_img(data):
    m = re.match(r'^data:image/(jpeg|png|webp);base64,(.+)$', data or '', re.S)
    if not m: raise ValueError('Неверный формат фото')
    raw = base64.b64decode(m.group(2))
    if len(raw) > 6_000_000: raise ValueError('Фото слишком большое')
    name = f'{uuid.uuid4().hex}.{"jpg" if m.group(1) == "jpeg" else m.group(1)}'
    run('insert into images(name,mime,data) values(?,?,?)', name, 'image/' + m.group(1), raw); return '/uploads/' + name

def prod(r): return {'id': r['id'], 'title': r['title'], 'brand': r['brand'], 'category': r['category'], 'price': r['price'], 'desc': r['descr'], 'photo': r['photo'], 'active': r['active']}
def uname(u): return u.get('username') or ''
def full(u): return (u.get('first_name', '') + ' ' + u.get('last_name', '')).strip()

class Err(Exception):
    def __init__(s, msg, code=400): s.msg, s.code = msg, code

def route(method, path, b, u):
    adm = bool(u) and (DEV or u['id'] in ADMINS)
    if path == 'init':
        if not u: raise Err('Откройте приложение из Telegram', 401)
        return {'user': {'id': u['id'], 'name': full(u), 'username': uname(u), 'photo': u.get('photo_url', '')}, 'admin': adm, 'subscribed': is_sub(u['id'], True),
                'products': [prod(r) for r in rows('select * from products where active=1 order by id desc')]}
    if not u: raise Err('Откройте приложение из Telegram', 401)
    if path == 'sub': return {'subscribed': is_sub(u['id'], True)}
    if path == 'my':
        return {'requests': rows('select id,text,photo,status,reply,ts from requests where uid=? order by id desc', u['id']),
                'orders': [dict(o, items=json.loads(o['items'])) for o in rows('select id,items,total,status,ts from orders where uid=? order by id desc', u['id'])]}
    if path in ('request', 'order') and not is_sub(u['id']): raise Err('subscribe', 403)
    if path == 'request':
        text = (b.get('text') or '').strip()[:1000]; photo = save_img(b['photo']) if b.get('photo') else ''
        if not text and not photo: raise Err('Добавьте фото или описание')
        i = run('insert into requests(uid,uname,name,text,photo,ts) values(?,?,?,?,?,?)', u['id'], uname(u), full(u), text, photo, int(time.time()))
        for a in ADMINS: notify(a, f'Новая заявка #{i} от {full(u)} @{uname(u)}\n{text}\n{URL + photo if photo and URL else ""}')
        return {'id': i}
    if path == 'order':
        items, total = [], 0
        for it in (b.get('items') or [])[:50]:
            r = rows('select * from products where id=? and active=1', int(it.get('id', 0)))
            if r: q = max(1, min(99, int(it.get('qty', 1)))); items.append({'id': r[0]['id'], 'title': r[0]['title'], 'price': r[0]['price'], 'qty': q}); total += r[0]['price'] * q
        if not items: raise Err('Корзина пуста')
        i = run('insert into orders(uid,uname,name,items,total,contact,comment,ts) values(?,?,?,?,?,?,?,?)', u['id'], uname(u), full(u), json.dumps(items, ensure_ascii=False), total,
                (b.get('contact') or '')[:200], (b.get('comment') or '')[:500], int(time.time()))
        for a in ADMINS: notify(a, f'Новый заказ #{i} от {full(u)} @{uname(u)}\n' + '\n'.join(f'{x["title"]} × {x["qty"]}' for x in items) + f'\nИтого: {total} ₽')
        return {'id': i}
    if not adm: raise Err('Нет доступа', 403)
    if path == 'admin':
        return {'products': [prod(r) for r in rows('select * from products order by id desc')], 'requests': rows('select * from requests order by id desc limit 200'),
                'orders': [dict(o, items=json.loads(o['items'])) for o in rows('select * from orders order by id desc limit 200')]}
    if path == 'admin/product':
        photo = b.get('photo') or ''
        photo = save_img(photo) if photo.startswith('data:') else (photo if photo.startswith('/uploads/') else '')
        v = ((b.get('title') or '').strip()[:120] or 'Без названия', (b.get('brand') or '')[:60], (b.get('category') or '')[:40], max(0, int(b.get('price') or 0)), (b.get('desc') or '')[:2000], photo, 1 if b.get('active', True) else 0)
        if b.get('id'): run('update products set title=?,brand=?,category=?,price=?,descr=?,photo=?,active=? where id=?', *v, int(b['id']))
        else: run('insert into products(title,brand,category,price,descr,photo,active) values(?,?,?,?,?,?,?)', *v)
        return {}
    if path == 'admin/product/delete': run('delete from products where id=?', int(b['id'])); return {}
    if path == 'admin/request':
        r = rows('select * from requests where id=?', int(b['id']))
        if not r or b.get('status') not in RS: raise Err('Заявка не найдена')
        reply = (b.get('reply') or '')[:1000]; run('update requests set status=?,reply=? where id=?', b['status'], reply, r[0]['id'])
        if r[0]['status'] != b['status'] or r[0]['reply'] != reply: notify(r[0]['uid'], f'Заявка #{r[0]["id"]}: {RS[b["status"]]}' + (f'\n{reply}' if reply else ''))
        return {}
    if path == 'admin/order':
        r = rows('select * from orders where id=?', int(b['id']))
        if not r or b.get('status') not in OS: raise Err('Заказ не найден')
        run('update orders set status=? where id=?', b['status'], r[0]['id'])
        if r[0]['status'] != b['status']: notify(r[0]['uid'], f'Заказ #{r[0]["id"]}: {OS[b["status"]]}')
        return {}
    raise Err('Не найдено', 404)

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def send(self, code, body, ctype='application/json; charset=utf-8'):
        self.send_response(code); self.send_header('Content-Type', ctype); self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store' if ctype.startswith(('application', 'text')) else 'public, max-age=86400'); self.end_headers(); self.wfile.write(body)
    def handle_any(self, method):
        p = urllib.parse.urlparse(self.path).path
        try:
            if method == 'POST' and p == f'/tg/{SECRET}' and TOKEN:
                n = int(self.headers.get('Content-Length') or 0); up = json.loads(self.rfile.read(n) or b'{}')
                threading.Thread(target=handle_update, args=(up,), daemon=True).start(); return self.send(200, b'{}')
            if p.startswith('/api/'):
                n = int(self.headers.get('Content-Length') or 0)
                if n > 12_000_000: raise Err('Слишком большой запрос', 413)
                b = json.loads(self.rfile.read(n) or b'{}') if method == 'POST' else {}
                return self.send(200, json.dumps(route(method, p[5:].strip('/'), b, auth(self.headers.get('X-Init-Data', ''))), ensure_ascii=False).encode())
            if p.startswith('/uploads/'):
                r = rows('select mime,data from images where name=?', Path(p).name)
                if r: return self.send(200, bytes(r[0]['data']), r[0]['mime'])
            f = {'/': ROOT / 'index.html', '/index.html': ROOT / 'index.html', '/art.png': ROOT / 'art.png'}.get(p)
            if f and f.is_file():
                ct = {'.html': 'text/html; charset=utf-8', '.png': 'image/png', '.jpg': 'image/jpeg', '.webp': 'image/webp'}.get(f.suffix, 'application/octet-stream')
                return self.send(200, f.read_bytes(), ct)
            self.send(404, b'{"error":"not found"}')
        except Err as e: self.send(e.code, json.dumps({'error': e.msg}, ensure_ascii=False).encode())
        except Exception as e: print('error', e); self.send(400, json.dumps({'error': 'Некорректный запрос'}).encode())
    def do_GET(self): self.handle_any('GET')
    def do_POST(self): self.handle_any('POST')

def handle_update(up):
    m = up.get('message') or {}; t = (m.get('text') or '').strip(); cid = m.get('chat', {}).get('id')
    if t.startswith('/id') and cid: tg('sendMessage', chat_id=cid, text=f'Ваш Telegram ID: {m["from"]["id"]}')
    elif t.startswith('/start') and cid:
        kb = {'inline_keyboard': [[{'text': 'Открыть магазин', 'web_app': {'url': URL}}]]} if URL else None
        tg('sendMessage', chat_id=cid, text='RAVOUT SHMOT — вещи из Китая. Нажмите кнопку ниже.', **({'reply_markup': kb} if kb else {}))

def bot():
    if URL:
        tg('setChatMenuButton', menu_button={'type': 'web_app', 'text': 'Магазин', 'web_app': {'url': URL}})
        tg('setWebhook', url=f'{URL}/tg/{SECRET}'); return  # вебхук: Telegram сам будит сервис, даже если он «спал»
    tg('deleteWebhook'); off = 0
    while True:
        for up in tg('getUpdates', offset=off, timeout=25).get('result', []):
            off = up['update_id'] + 1; handle_update(up)
        time.sleep(1)

if __name__ == '__main__':
    if not TOKEN and not DEV: print('Задайте BOT_TOKEN (или DEV=1 для локальной проверки)')
    if TOKEN: threading.Thread(target=bot, daemon=True).start()
    print(f'Слушаю порт {PORT}; админы: {sorted(ADMINS) or "не заданы"}')
    ThreadingHTTPServer(('0.0.0.0', PORT), H).serve_forever()
