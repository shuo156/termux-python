"""
PhonePaaS v3 — 单文件 Python PaaS 平台
依赖: pip install flask pillow werkzeug
启动: python phonepaas.py
"""
import io, os, re, shutil, sqlite3, importlib.util, traceback, sys, random, json
from datetime import datetime
from functools import wraps
from pathlib import Path

from flask import (Flask, request, session, redirect,
                   render_template_string, flash, send_file, abort)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from werkzeug.serving import run_simple

try:
    from PIL import Image, ImageDraw, ImageFont, ImageFilter
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

# ═══ 配置 ════════════════════════════════════════════════════
PORT       = 8080
PUBLIC_URL = "https://ooo.shuocc.xyz"
DB_FILE    = "phonepaas.db"
SVC_DIR    = "services"
MAX_SVC    = 5
PREFIX     = "/s"

main_app = Flask(__name__)
main_app.secret_key = os.environ.get("SECRET_KEY", "phonepaas-dev-secret")

# ═══ 同进程 WSGI 分发器 ══════════════════════════════════════
class Dispatcher:
    def __init__(self, app):
        self.main = app
        self.apps = {}

    def mount(self, name, wsgi_app):
        self.apps[name] = wsgi_app

    def unmount(self, name):
        self.apps.pop(name, None)

    def __call__(self, environ, start_response):
        path = environ.get("PATH_INFO", "")
        pfx  = PREFIX + "/"
        if path.startswith(pfx):
            rest = path[len(pfx):]
            name = rest.split("/")[0]
            if name and name in self.apps:
                sub = path[len(pfx) + len(name):] or "/"
                env = dict(environ)
                env["SCRIPT_NAME"] = environ.get("SCRIPT_NAME", "") + pfx + name
                env["PATH_INFO"]   = sub
                return self.apps[name](env, start_response)
        return self.main(environ, start_response)

dispatcher = Dispatcher(main_app)

# ═══ 数据库 ══════════════════════════════════════════════════
def db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            pw_hash TEXT NOT NULL,
            email TEXT
        );
        CREATE TABLE IF NOT EXISTS services(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT UNIQUE NOT NULL,
            title TEXT,
            entry TEXT DEFAULT 'main.py',
            status TEXT DEFAULT 'stopped',
            err_msg TEXT,
            deployed_at TEXT
        );
        """)

# ═══ 认证 ═════════════════════════════════════════════════════
def me():
    uid = session.get("uid")
    if not uid:
        return None
    with db() as c:
        return c.execute("SELECT * FROM users WHERE id=?", [uid]).fetchone()

def login_required(f):
    @wraps(f)
    def inner(*a, **kw):
        if not me():
            return redirect("/login")
        return f(*a, **kw)
    return inner

# ═══ 验证码 ═══════════════════════════════════════════════════
def new_captcha():
    a  = random.randint(2, 15)
    b  = random.randint(1, 12)
    op = random.choice(["+", "-"])
    if op == "-" and b > a:
        a, b = b, a
    ans   = str(a + b if op == "+" else a - b)
    label = "{} {} {} = ?".format(a, op, b)
    session["cap"] = ans
    return label

def captcha_img(label):
    w, h = 170, 50
    img  = Image.new("RGB", (w, h), (13, 17, 23))
    draw = ImageDraw.Draw(img)
    for _ in range(8):
        draw.line([(random.randint(0,w), random.randint(0,h)),
                   (random.randint(0,w), random.randint(0,h))],
                  fill=(random.randint(40,80),)*3)
    for _ in range(60):
        draw.point((random.randint(0,w-1), random.randint(0,h-1)),
                   fill=(random.randint(50,90),)*3)
    try:
        font = ImageFont.truetype("/system/fonts/DroidSans.ttf", 22)
    except Exception:
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 20)
        except Exception:
            font = ImageFont.load_default()
    x = 8
    for ch in label:
        draw.text((x, random.randint(5,14)), ch, font=font,
                  fill=(random.randint(160,255), random.randint(150,230), random.randint(60,130)))
        x += random.randint(14, 20)
    img = img.filter(ImageFilter.SMOOTH)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    buf.seek(0)
    return buf

# ═══ 部署 ═════════════════════════════════════════════════════
def svc_dir(svc):
    return Path(SVC_DIR) / str(svc["user_id"]) / svc["name"]

def deploy(svc_id):
    with db() as c:
        svc = c.execute("SELECT * FROM services WHERE id=?", [svc_id]).fetchone()
    if not svc:
        return False, "服务不存在"

    entry = svc_dir(svc) / (svc["entry"] or "main.py")
    if not entry.exists():
        return False, "入口文件 {} 不存在".format(svc["entry"])

    svc_path = str(svc_dir(svc))
    if svc_path not in sys.path:
        sys.path.insert(0, svc_path)

    try:
        mod_name = "_svc_{}_{}".format(svc_id, int(datetime.now().timestamp()))
        spec = importlib.util.spec_from_file_location(mod_name, str(entry))
        mod  = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except Exception:
        err = traceback.format_exc()
        with db() as c:
            c.execute("UPDATE services SET status='error',err_msg=? WHERE id=?",
                      [err[-2000:], svc_id])
        return False, "代码加载失败，请查看错误详情"

    wsgi = None
    for attr in ("app", "application"):
        obj = getattr(mod, attr, None)
        if obj is not None and hasattr(obj, "wsgi_app"):
            wsgi = obj
            break
    if wsgi is None:
        fn = getattr(mod, "create_app", None)
        if callable(fn):
            wsgi = fn()
    if wsgi is None:
        err = "未找到 Flask app。请确保代码顶层有：\n\napp = Flask(__name__)\n\n不要写 app.run()"
        with db() as c:
            c.execute("UPDATE services SET status='error',err_msg=? WHERE id=?", [err, svc_id])
        return False, err

    dispatcher.mount(svc["name"], wsgi)
    with db() as c:
        c.execute("UPDATE services SET status='running',err_msg=NULL,deployed_at=? WHERE id=?",
                  [datetime.now().strftime("%Y-%m-%d %H:%M:%S"), svc_id])
    return True, "部署成功"

def undeploy(svc_id):
    with db() as c:
        svc = c.execute("SELECT * FROM services WHERE id=?", [svc_id]).fetchone()
    if svc:
        dispatcher.unmount(svc["name"])
        with db() as c:
            c.execute("UPDATE services SET status='stopped',err_msg=NULL WHERE id=?", [svc_id])

# ═══════════════════════════════════════════════════════════════
# 公共 CSS
# ═══════════════════════════════════════════════════════════════
CSS = """<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600&family=Outfit:wght@400;600;700&display=swap');
:root{--bg:#080c14;--s1:#0d1321;--s2:#131c2e;--bd:#1f2f47;--tx:#c8d3e8;--dim:#566882;
  --acc:#4f9cf9;--ok:#34d399;--er:#f87171;--wa:#fbbf24;
  --mono:'JetBrains Mono',monospace;--ui:'Outfit',sans-serif;--r:8px;}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--tx);font-family:var(--ui);min-height:100vh;line-height:1.6}
a{color:var(--acc);text-decoration:none}a:hover{opacity:.8}
::-webkit-scrollbar{width:5px;height:5px}::-webkit-scrollbar-thumb{background:var(--bd);border-radius:3px}
nav{background:var(--s1);border-bottom:1px solid var(--bd);height:54px;padding:0 24px;
  display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;z-index:100}
.logo{font-family:var(--mono);font-size:.95rem;font-weight:600;color:var(--tx);
  display:flex;align-items:center;gap:10px}
.ldot{width:8px;height:8px;border-radius:50%;background:var(--acc);box-shadow:0 0 10px var(--acc)}
.nav-r{display:flex;gap:4px;align-items:center}
.nav-r a{color:var(--dim);padding:5px 12px;border-radius:var(--r);font-size:.83rem;transition:.15s}
.nav-r a:hover{background:var(--s2);color:var(--tx)}
.wrap{max-width:1040px;margin:0 auto;padding:28px 18px}
.btn{display:inline-flex;align-items:center;gap:5px;padding:7px 15px;border-radius:var(--r);
  border:1px solid transparent;cursor:pointer;font-size:.83rem;font-weight:600;
  font-family:var(--ui);transition:.15s;text-decoration:none;white-space:nowrap;line-height:1.4}
.bp{background:var(--acc);color:#060d1a;border-color:var(--acc)}
.bgg{background:transparent;color:var(--dim);border-color:var(--bd)}
.bgg:hover{background:var(--s2);color:var(--tx)}
.bok{background:transparent;color:var(--ok);border-color:var(--ok)}
.bok:hover{background:var(--ok);color:#060d1a}
.ber{background:transparent;color:var(--er);border-color:var(--er)}
.ber:hover{background:var(--er);color:#fff}
.sm{padding:4px 11px;font-size:.78rem}
.card{background:var(--s1);border:1px solid var(--bd);border-radius:var(--r);padding:20px}
.fg{margin-bottom:14px}
.fg label{display:block;font-size:.78rem;color:var(--dim);margin-bottom:5px}
input,select,textarea{background:var(--s2);border:1px solid var(--bd);border-radius:var(--r);
  color:var(--tx);font-family:var(--ui);font-size:.88rem;padding:8px 12px;width:100%;transition:.15s}
input:focus,textarea:focus{outline:none;border-color:var(--acc)}
.fl{padding:10px 15px;border-radius:var(--r);font-size:.85rem;margin-bottom:8px}
.fle{background:rgba(248,113,113,.1);border:1px solid var(--er);color:var(--er)}
.fls{background:rgba(52,211,153,.1);border:1px solid var(--ok);color:var(--ok)}
.fli{background:rgba(79,156,249,.1);border:1px solid var(--acc);color:var(--acc)}
.badge{display:inline-flex;align-items:center;gap:5px;padding:2px 9px;border-radius:20px;
  font-family:var(--mono);font-size:.7rem;font-weight:600}
.bok2{background:rgba(52,211,153,.1);color:var(--ok)}
.bst{background:rgba(86,104,130,.12);color:var(--dim)}
.ber2{background:rgba(248,113,113,.1);color:var(--er)}
@keyframes bk{0%,100%{opacity:1}50%{opacity:.2}}
.ph{display:flex;justify-content:space-between;align-items:center;margin-bottom:20px;flex-wrap:wrap;gap:10px}
.ph h1{font-size:1.2rem;font-weight:700}
.cap-row{display:flex;gap:10px;align-items:flex-end}
.cap-row img{border-radius:6px;border:1px solid var(--bd);cursor:pointer;height:40px;flex-shrink:0}
.cap-row input{flex:1}
.ftree{border:1px solid var(--bd);border-radius:var(--r);overflow:hidden}
.frow{display:flex;align-items:center;padding:9px 14px;border-bottom:1px solid var(--bd);
  gap:10px;font-size:.85rem;transition:.1s}
.frow:last-child{border-bottom:0}
.frow:hover{background:var(--s2)}
.fname{flex:1;font-family:var(--mono);font-size:.82rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.fsize{color:var(--dim);font-size:.73rem;white-space:nowrap}
.ed-bar{background:var(--s2);border:1px solid var(--bd);border-bottom:0;
  border-radius:var(--r) var(--r) 0 0;padding:8px 14px;
  display:flex;align-items:center;justify-content:space-between}
.err-block{background:rgba(248,113,113,.07);border:1px solid rgba(248,113,113,.3);
  border-radius:var(--r);padding:14px;margin-bottom:14px}
.err-block pre{font-family:var(--mono);font-size:.73rem;color:var(--er);
  white-space:pre-wrap;max-height:220px;overflow-y:auto}
.err-page{display:flex;flex-direction:column;align-items:center;justify-content:center;
  min-height:70vh;text-align:center;gap:16px}
.err-code{font-family:var(--mono);font-size:5rem;font-weight:700;line-height:1;
  background:linear-gradient(135deg,var(--acc),var(--ok));
  -webkit-background-clip:text;-webkit-text-fill-color:transparent}
.err-title{font-size:1.4rem;font-weight:700}
.err-desc{color:var(--dim);font-size:.9rem;max-width:420px;line-height:1.7}
.err-divider{width:40px;height:2px;background:var(--bd);border-radius:2px}
@media(max-width:580px){.wrap{padding:16px 10px}nav{padding:0 12px}}
</style>"""

# ═══════════════════════════════════════════════════════════════
# page() — 使用 Jinja2 变量，body 里的 JS { } 不会破坏渲染
# ═══════════════════════════════════════════════════════════════
PAGE_TPL = """\
<!DOCTYPE html><html lang='zh'><head>
<meta charset='UTF-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>{{ page_title }}</title>
{{ page_css | safe }}
</head><body>
<nav>
  <div class='logo'><div class='ldot'></div>PhonePaaS</div>
  <div class='nav-r'>{{ page_nav | safe }}</div>
</nav>
<div class='wrap'>
  {% with msgs = get_flashed_messages(with_categories=true) %}
    {% for cat, txt in msgs %}
      <div class='fl fl{{ cat[0] }}'>{{ txt }}</div>
    {% endfor %}
  {% endwith %}
  {{ page_body | safe }}
</div>
</body></html>"""

def page(body, title="PhonePaaS"):
    user = me()
    if user:
        nav = '<a href="/">仪表盘</a><a href="/logout">退出 {}</a>'.format(user["username"])
    else:
        nav = '<a href="/login">登录</a><a href="/register" class="btn bp sm" style="margin-left:4px">注册</a>'
    return render_template_string(
        PAGE_TPL,
        page_title=title,
        page_css=CSS,
        page_nav=nav,
        page_body=body,
    )

def error_page(code, title, desc):
    body = (
        "<div class='err-page'>"
        "<div class='err-code'>{}</div>".format(code) +
        "<div class='err-divider'></div>"
        "<div class='err-title'>{}</div>".format(title) +
        "<div class='err-desc'>{}</div>".format(desc) +
        "<div style='display:flex;gap:10px;flex-wrap:wrap;justify-content:center;margin-top:8px'>"
        "<a href='/' class='btn bp'>回到首页</a>"
        "<a href='javascript:history.back()' class='btn bgg'>返回上页</a>"
        "</div></div>"
    )
    return page(body, "{} — PhonePaaS".format(code))

# ═══ 错误处理器 ════════════════════════════════════════════════
@main_app.errorhandler(400)
def err_400(e):
    return error_page(400, "请求有误", "服务器无法理解你的请求，请检查参数后重试。"), 400

@main_app.errorhandler(403)
def err_403(e):
    return error_page(403, "禁止访问", "你没有权限访问此资源。"), 403

@main_app.errorhandler(404)
def err_404(e):
    return error_page(404, "页面不见了", "你访问的页面不存在或已被删除。也许链接有误？"), 404

@main_app.errorhandler(405)
def err_405(e):
    return error_page(405, "方法不允许", "该页面不支持此请求方式。"), 405

@main_app.errorhandler(500)
def err_500(e):
    return error_page(500, "服务器出错了", "服务器内部出现了一些问题，请稍后重试。"), 500

@main_app.errorhandler(502)
def err_502(e):
    return error_page(502, "网关错误", "上游服务器返回了无效响应，请稍后重试。"), 502

@main_app.errorhandler(503)
def err_503(e):
    return error_page(503, "服务暂时不可用", "服务器正在维护或过载，请稍后再试。"), 503

# ═══ 验证码路由 ════════════════════════════════════════════════
@main_app.route("/captcha")
def captcha_route():
    label = new_captcha()
    if HAS_PIL:
        return send_file(captcha_img(label), mimetype="image/png", max_age=0)
    return (
        "<div style='background:#0d1321;color:#4f9cf9;font-size:1.4rem;"
        "padding:8px 14px;border-radius:6px;font-family:monospace'>{}</div>".format(label)
    ), 200

# ═══ 注册 ═════════════════════════════════════════════════════
@main_app.route("/register", methods=["GET","POST"])
def register():
    if request.method == "POST":
        u   = request.form.get("username","").strip()
        pw  = request.form.get("pw","")
        em  = request.form.get("email","").strip() or None
        cap = request.form.get("cap","").strip()
        if cap != session.get("cap","__no__"):
            flash("验证码错误", "error")
        elif not re.match(r"^[a-zA-Z0-9_]{3,20}$", u):
            flash("用户名：3-20位字母/数字/下划线", "error")
        elif not re.search(r"(?=.*[a-z])(?=.*[A-Z])(?=.*\d).{8,}", pw):
            flash("密码需含大小写字母和数字，至少8位", "error")
        else:
            try:
                with db() as c:
                    c.execute("INSERT INTO users(username,pw_hash,email) VALUES(?,?,?)",
                              [u, generate_password_hash(pw), em])
                with db() as c:
                    row = c.execute("SELECT id FROM users WHERE username=?", [u]).fetchone()
                session["uid"] = row["id"]
                return redirect("/")
            except sqlite3.IntegrityError:
                flash("用户名已存在", "error")

    body = """
<div style="max-width:400px;margin:60px auto">
  <h1 style="font-size:1.4rem;font-weight:700;margin-bottom:4px">创建账户</h1>
  <p style="color:var(--dim);font-size:.87rem;margin-bottom:24px">部署你的第一个 Python 服务</p>
  <form method="post">
    <div class="fg"><label>用户名</label><input type="text" name="username" required></div>
    <div class="fg"><label>密码（≥8位，含大小写+数字）</label><input type="password" name="pw" required></div>
    <div class="fg"><label>邮箱（可选）</label><input type="email" name="email"></div>
    <div class="fg"><label>验证码（点图刷新）</label>
      <div class="cap-row">
        <img src="/captcha" id="ci" onclick="this.src='/captcha?'+Date.now()" title="点击刷新">
        <input type="text" name="cap" placeholder="填写答案" required autocomplete="off">
      </div>
    </div>
    <button class="btn bp" style="width:100%;justify-content:center">注册</button>
  </form>
  <p style="margin-top:16px;text-align:center;font-size:.83rem;color:var(--dim)">
    已有账户？ <a href="/login">登录</a></p>
</div>"""
    return page(body, "注册")

# ═══ 登录 ═════════════════════════════════════════════════════
@main_app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        u   = request.form.get("username","").strip()
        pw  = request.form.get("pw","")
        cap = request.form.get("cap","").strip()
        if cap != session.get("cap","__no__"):
            flash("验证码错误", "error")
        else:
            with db() as c:
                row = c.execute("SELECT * FROM users WHERE username=?", [u]).fetchone()
            if row and check_password_hash(row["pw_hash"], pw):
                session["uid"] = row["id"]
                return redirect("/")
            flash("用户名或密码错误", "error")

    body = """
<div style="max-width:400px;margin:60px auto">
  <h1 style="font-size:1.4rem;font-weight:700;margin-bottom:4px">欢迎回来</h1>
  <p style="color:var(--dim);font-size:.87rem;margin-bottom:24px">登录以管理你的服务</p>
  <form method="post">
    <div class="fg"><label>用户名</label><input type="text" name="username" required></div>
    <div class="fg"><label>密码</label><input type="password" name="pw" required></div>
    <div class="fg"><label>验证码（点图刷新）</label>
      <div class="cap-row">
        <img src="/captcha" id="ci" onclick="this.src='/captcha?'+Date.now()" title="点击刷新">
        <input type="text" name="cap" placeholder="填写答案" required autocomplete="off">
      </div>
    </div>
    <button class="btn bp" style="width:100%;justify-content:center">登录</button>
  </form>
  <p style="margin-top:16px;text-align:center;font-size:.83rem;color:var(--dim)">
    没有账户？ <a href="/register">注册</a></p>
</div>"""
    return page(body, "登录")

@main_app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")

# ═══ 仪表盘 ════════════════════════════════════════════════════
@main_app.route("/")
@login_required
def dashboard():
    u = me()
    with db() as c:
        svcs = c.execute(
            "SELECT * FROM services WHERE user_id=? ORDER BY id DESC", [u["id"]]
        ).fetchall()

    cards = ""
    for s in svcs:
        nm = s["name"]
        st = s["status"]
        if st == "running":
            badge   = "<span class='badge bok2'><span style='width:6px;height:6px;border-radius:50%;background:currentColor;display:inline-block;animation:bk 1.3s infinite'></span>运行中</span>"
            pub     = "{}{}/{}/".format(PUBLIC_URL, PREFIX, nm)
            publink = '<a href="{}" target="_blank" style="font-size:.78rem">{}</a>'.format(pub, pub)
            tbtn    = '<form method="post" action="/svc/{}/undeploy" style="display:inline"><button class="btn bgg sm">停止</button></form>'.format(s["id"])
        elif st == "error":
            badge   = "<span class='badge ber2'>异常</span>"
            publink = ""
            tbtn    = '<form method="post" action="/svc/{}/deploy" style="display:inline"><button class="btn bok sm">重新部署</button></form>'.format(s["id"])
        else:
            badge   = "<span class='badge bst'>已停止</span>"
            publink = ""
            tbtn    = '<form method="post" action="/svc/{}/deploy" style="display:inline"><button class="btn bok sm"><svg width="12" height="12" viewBox="0 0 12 12" fill="currentColor" style="vertical-align:middle"><polygon points="2,1 11,6 2,11"/></svg> 部署</button></form>'.format(s["id"])

        dep_info = ""
        if s["deployed_at"] and st == "running":
            dep_info = "<span>· 部署于 {}</span>".format(s["deployed_at"])

        err_html = ""
        if st == "error" and s["err_msg"]:
            safe_err = s["err_msg"].replace("&","&amp;").replace("<","&lt;")
            err_html = (
                "<div class='err-block' style='margin-top:12px'>"
                "<div style='font-size:.78rem;font-weight:600;margin-bottom:6px'>"
                "错误详情 — 修改代码后重新部署</div>"
                "<pre>{}</pre></div>".format(safe_err)
            )

        sn = (s["title"] or nm).replace("<","&lt;")
        cards += (
            "<div class='card' style='margin-bottom:10px'>"
            "<div style='display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:10px'>"
            "<div style='min-width:0'>"
            "<div style='display:flex;align-items:center;gap:10px;margin-bottom:6px;flex-wrap:wrap'>"
            "<strong>{}</strong> {}".format(sn, badge) +
            "</div>"
            "<div style='font-size:.78rem;color:var(--dim);display:flex;flex-wrap:wrap;gap:10px;align-items:center'>"
            "<span style='font-family:var(--mono)'>{}/{}/</span>".format(PREFIX, nm) +
            publink + dep_info +
            "</div></div>"
            "<div style='display:flex;gap:7px;flex-wrap:wrap;flex-shrink:0'>"
            '<a href="/svc/{}" class="btn bgg sm">文件</a>'.format(s["id"]) +
            tbtn +
            '<form method="post" action="/svc/{}/remove" onsubmit="return confirm(\'确认删除？\')" style="display:inline">'.format(s["id"]) +
            "<button class='btn ber sm'>删除</button></form>"
            "</div></div>"
            + err_html +
            "</div>"
        )

    if not cards:
        cards = (
            "<div style='text-align:center;padding:60px 20px;color:var(--dim)'>"
            "<div style='margin-bottom:12px'><svg width='40' height='40' viewBox='0 0 40 40' fill='currentColor' opacity='0.3'><polygon points='20,2 38,20 20,38 2,20'/></svg></div>"
            "<p style='margin-bottom:16px'>还没有服务，创建第一个吧</p>"
            "<a href='/new' class='btn bp'>新建服务</a>"
            "</div>"
        )

    body = (
        "<div class='ph'>"
        "<div><h1>我的服务</h1>"
        "<p style='color:var(--dim);font-size:.82rem'>{} / {} 个</p></div>".format(len(svcs), MAX_SVC) +
        "<a href='/new' class='btn bp'>+ 新建服务</a>"
        "</div>" + cards
    )
    return page(body)

# ═══ 新建服务 ══════════════════════════════════════════════════
DEFAULT_CODE = """\
from flask import Flask, jsonify

app = Flask(__name__)

@app.route("/")
def index():
    return jsonify({"service": "__NAME__", "status": "ok"})

@app.route("/hello")
def hello():
    return "Hello from PhonePaaS!"

# 不要写 app.run() — 平台会自动加载此文件
"""

@main_app.route("/new", methods=["GET","POST"])
@login_required
def new_svc():
    u = me()
    if request.method == "POST":
        title = request.form.get("title","").strip()
        name  = request.form.get("name","").strip().lower()
        entry = (request.form.get("entry","") or "main.py").strip()
        if not re.match(r"^[a-z0-9][a-z0-9-]{1,28}[a-z0-9]$", name):
            flash("子路径：小写字母/数字/短横线，3-30位，不能以横线开头结尾", "error")
        else:
            with db() as c:
                cnt = c.execute("SELECT COUNT(*) FROM services WHERE user_id=?", [u["id"]]).fetchone()[0]
            if cnt >= MAX_SVC:
                flash("最多创建 {} 个服务".format(MAX_SVC), "error")
            else:
                d = Path(SVC_DIR) / str(u["id"]) / name
                d.mkdir(parents=True, exist_ok=True)
                ep = d / entry
                if not ep.exists():
                    ep.write_text(DEFAULT_CODE.replace("__NAME__", name))
                try:
                    with db() as c:
                        c.execute(
                            "INSERT INTO services(user_id,name,title,entry) VALUES(?,?,?,?)",
                            [u["id"], name, title, entry]
                        )
                    flash("服务已创建，进入文件管理器写好代码后点「部署」", "success")
                    return redirect("/")
                except sqlite3.IntegrityError:
                    flash("子路径名已被占用", "error")

    body = (
        "<div class='ph'><h1>新建服务</h1></div>"
        "<div class='card' style='max-width:520px'>"
        "<form method='post'>"
        "<div class='fg'><label>显示名称</label>"
        "<input type='text' name='title' placeholder='我的 Flask API' required></div>"
        "<div class='fg'><label>子路径（小写字母/数字/短横线，3-30位）</label>"
        "<div style='display:flex;align-items:center;gap:8px'>"
        "<span style='color:var(--dim);font-size:.78rem;white-space:nowrap;font-family:var(--mono)'>{}/</span>".format(PREFIX) +
        "<input type='text' name='name' placeholder='my-api' required>"
        "<span style='color:var(--dim);font-size:.78rem;font-family:var(--mono)'>/</span>"
        "</div></div>"
        "<div class='fg'><label>入口文件（默认 main.py）</label>"
        "<input type='text' name='entry' value='main.py'></div>"
        "<div style='display:flex;gap:10px'>"
        "<a href='/' class='btn bgg'>取消</a>"
        "<button class='btn bp'>创建服务</button>"
        "</div></form></div>"
    )
    return page(body, "新建服务")

# ═══ 文件管理器 ════════════════════════════════════════════════
TEXT_EXTS = {".py",".txt",".md",".json",".yaml",".yml",
             ".toml",".ini",".cfg",".env",".html",".css",
             ".js",".sh",".csv",".xml",".rst",".sql"}

def safe_path(base, rel):
    base = Path(base).resolve()
    p    = (base / rel).resolve()
    if p == base or base in p.parents:
        return p
    return None

def list_items(svc, rel=""):
    base   = svc_dir(svc)
    target = (base / rel) if rel else base
    if not target.is_dir():
        return []
    out = []
    for p in sorted(target.iterdir(), key=lambda x: (x.is_file(), x.name.lower())):
        rel_path = (rel + "/" + p.name) if rel else p.name
        out.append({"name": p.name, "is_dir": p.is_dir(),
                    "size": p.stat().st_size if p.is_file() else 0,
                    "rel":  rel_path})
    return out

@main_app.route("/svc/<int:sid>")
@login_required
def svc_files(sid):
    u = me()
    with db() as c:
        svc = c.execute("SELECT * FROM services WHERE id=? AND user_id=?", [sid, u["id"]]).fetchone()
    if not svc:
        abort(404)

    rel    = request.args.get("rel", "")
    items  = list_items(svc, rel)
    parent = str(Path(rel).parent) if rel and str(Path(rel).parent) != "." else ""

    if svc["status"] == "running":
        toggle = '<form method="post" action="/svc/{}/undeploy"><button class="btn bgg sm">停止</button></form>'.format(sid)
    else:
        toggle = '<form method="post" action="/svc/{}/deploy"><button class="btn bok sm"><svg width="12" height="12" viewBox="0 0 12 12" fill="currentColor" style="vertical-align:middle"><polygon points="2,1 11,6 2,11"/></svg> 部署</button></form>'.format(sid)

    err_html = ""
    if svc["status"] == "error" and svc["err_msg"]:
        safe_err = svc["err_msg"].replace("&","&amp;").replace("<","&lt;")
        err_html = (
            "<div class='err-block'><div style='font-size:.78rem;font-weight:600;margin-bottom:6px'>"
            "部署失败 — 修改代码后重新部署</div><pre>{}</pre></div>".format(safe_err)
        )

    rows = ""
    if rel:
        back = "/svc/{}".format(sid) + ("?rel={}".format(parent) if parent else "")
        rows += (
            "<div class='frow'><span style='color:var(--acc)'>"
            "<svg width='14' height='14' viewBox='0 0 14 14' fill='none' stroke='currentColor' "
            "stroke-width='2' stroke-linecap='round' stroke-linejoin='round' style='vertical-align:middle'>"
            "<path d='M9 2L4 7l5 5'/></svg></span>"
            "<a class='fname' href='{}' style='color:var(--acc)'>.. 返回上级</a></div>".format(back)
        )

    for f in items:
        sn = f["name"].replace("&","&amp;").replace("<","&lt;")
        if f["is_dir"]:
            icon     = "<span style='color:var(--wa)'><svg width='14' height='12' viewBox='0 0 14 12' fill='currentColor' style='vertical-align:middle'><path d='M1 2a1 1 0 011-1h3.5l1.5 1.5H12a1 1 0 011 1V10a1 1 0 01-1 1H2a1 1 0 01-1-1V2z'/></svg></span>"
            fname    = "<a class='fname' href='/svc/{}?rel={}' style='color:var(--wa)'>{}/ </a>".format(sid, f["rel"], sn)
            edit_btn = ""
        else:
            icon     = "<span style='opacity:.4'><svg width='12' height='12' viewBox='0 0 12 12' fill='none' stroke='currentColor' stroke-width='1.5' style='vertical-align:middle'><path d='M2 1h6l2 2v8H2V1z'/><path d='M8 1v3h2'/></svg></span>"
            fname    = "<span class='fname'>{}</span><span class='fsize'>{:.1f} KB</span>".format(sn, f["size"]/1024)
            ext      = Path(f["name"]).suffix.lower()
            edit_btn = "<a class='btn bgg sm' href='/svc/{}/edit?path={}'>编辑</a>".format(sid, f["rel"]) if ext in TEXT_EXTS else ""

        del_form = (
            "<form method='post' action='/svc/{}/delete'"
            " onsubmit=\"return confirm('删除 {}？')\">"
            "<input type='hidden' name='rel_path' value='{}'>"
            "<input type='hidden' name='back_rel' value='{}'>"
            "<button class='btn ber sm'>删除</button></form>"
        ).format(sid, sn, f["rel"], rel)

        rows += "<div class='frow'>{} {}<div style='display:flex;gap:6px;flex-shrink:0'>{}{}</div></div>".format(
            icon, fname, edit_btn, del_form)

    if not rows:
        rows = "<div class='frow' style='color:var(--dim);justify-content:center;font-size:.83rem'>目录为空</div>"

    rel_display = ("/" + rel) if rel else "/"
    title_h     = (svc["title"] or svc["name"]).replace("<","&lt;")

    body = (
        "<div class='ph'>"
        "<div><h1>{}</h1>".format(title_h) +
        "<p style='color:var(--dim);font-size:.78rem;font-family:var(--mono)'>{}/{}/  ·  入口: {}</p></div>".format(PREFIX, svc["name"], svc["entry"]) +
        "<div style='display:flex;gap:8px;flex-wrap:wrap'>"
        "{}<a href='/' class='btn bgg sm'>仪表盘</a>".format(toggle) +
        "</div></div>"
        + err_html +
        "<div style='display:flex;gap:8px;margin-bottom:12px;flex-wrap:wrap;align-items:center'>"
        "<span style='font-size:.78rem;color:var(--dim);font-family:var(--mono)'>{}</span>".format(rel_display) +
        "<div style='flex:1'></div>"
        "<form method='post' action='/svc/{}/upload' enctype='multipart/form-data' style='display:inline-flex;gap:6px;align-items:center'>".format(sid) +
        "<input type='hidden' name='rel' value='{}'>".format(rel) +
        "<input type='file' name='files' id='fu' multiple style='display:none' onchange='this.form.submit()'>"
        "<button type='button' class='btn bgg sm' onclick=\"document.getElementById('fu').click()\">上传文件</button></form>"
        "<form method='post' action='/svc/{}/newfile' style='display:inline-flex;gap:6px;align-items:center'>".format(sid) +
        "<input type='hidden' name='rel' value='{}'>".format(rel) +
        "<input type='text' name='fname' placeholder='新文件.py' style='width:130px;padding:5px 9px;font-size:.78rem'>"
        "<button class='btn bgg sm'>新建文件</button></form>"
        "<form method='post' action='/svc/{}/mkdir' style='display:inline-flex;gap:6px;align-items:center'>".format(sid) +
        "<input type='hidden' name='rel' value='{}'>".format(rel) +
        "<input type='text' name='dname' placeholder='文件夹名' style='width:110px;padding:5px 9px;font-size:.78rem'>"
        "<button class='btn bgg sm'>新建文件夹</button></form>"
        "</div>"
        "<div class='ftree'>{}</div>".format(rows)
    )
    return page(body, "文件 — {}".format(svc["name"]))

@main_app.route("/svc/<int:sid>/upload", methods=["POST"])
@login_required
def upload_file(sid):
    u = me()
    with db() as c:
        svc = c.execute("SELECT * FROM services WHERE id=? AND user_id=?", [sid, u["id"]]).fetchone()
    if not svc:
        abort(404)
    rel  = request.form.get("rel","")
    base = svc_dir(svc) / rel if rel else svc_dir(svc)
    base.mkdir(parents=True, exist_ok=True)
    count = 0
    for f in request.files.getlist("files"):
        if f.filename:
            f.save(str(base / secure_filename(f.filename)))
            count += 1
    flash("上传了 {} 个文件".format(count), "success")
    return redirect("/svc/{}".format(sid) + ("?rel={}".format(rel) if rel else ""))

@main_app.route("/svc/<int:sid>/newfile", methods=["POST"])
@login_required
def new_file(sid):
    u = me()
    with db() as c:
        svc = c.execute("SELECT * FROM services WHERE id=? AND user_id=?", [sid, u["id"]]).fetchone()
    if not svc:
        abort(404)
    rel   = request.form.get("rel","")
    fname = secure_filename(request.form.get("fname","").strip())
    if not fname:
        flash("文件名不能为空", "error")
        return redirect("/svc/{}".format(sid) + ("?rel={}".format(rel) if rel else ""))
    base = svc_dir(svc) / rel if rel else svc_dir(svc)
    fp   = base / fname
    if not fp.exists():
        fp.write_text("")
    path = "{}/{}".format(rel, fname) if rel else fname
    return redirect("/svc/{}/edit?path={}".format(sid, path))

@main_app.route("/svc/<int:sid>/mkdir", methods=["POST"])
@login_required
def make_dir(sid):
    u = me()
    with db() as c:
        svc = c.execute("SELECT * FROM services WHERE id=? AND user_id=?", [sid, u["id"]]).fetchone()
    if not svc:
        abort(404)
    rel   = request.form.get("rel","")
    dname = secure_filename(request.form.get("dname","").strip())
    if dname:
        target = svc_dir(svc) / rel / dname if rel else svc_dir(svc) / dname
        target.mkdir(exist_ok=True)
        flash("{} 已创建".format(dname), "success")
    else:
        flash("文件夹名不能为空", "error")
    return redirect("/svc/{}".format(sid) + ("?rel={}".format(rel) if rel else ""))

@main_app.route("/svc/<int:sid>/delete", methods=["POST"])
@login_required
def delete_path(sid):
    u = me()
    with db() as c:
        svc = c.execute("SELECT * FROM services WHERE id=? AND user_id=?", [sid, u["id"]]).fetchone()
    if not svc:
        abort(404)
    rel_path = request.form.get("rel_path","")
    back_rel = request.form.get("back_rel","")
    target   = safe_path(svc_dir(svc), rel_path)
    if target and target.exists():
        shutil.rmtree(target) if target.is_dir() else target.unlink()
        flash("已删除", "info")
    return redirect("/svc/{}".format(sid) + ("?rel={}".format(back_rel) if back_rel else ""))

# ═══════════════════════════════════════════════════════════════
# 代码编辑器
# 编辑器页面完全独立渲染，不经过 page()，
# JS 里的所有 { } 都在 Jinja2 注释/raw 块之外，完全安全
# ═══════════════════════════════════════════════════════════════

EXT_MODE = {
    ".py":   "python",
    ".js":   "javascript",
    ".json": "javascript",
    ".html": "htmlmixed",
    ".htm":  "htmlmixed",
    ".css":  "css",
    ".sh":   "shell",
    ".md":   "markdown",
    ".yaml": "yaml",
    ".yml":  "yaml",
    ".xml":  "xml",
    ".sql":  "sql",
}

MODE_SCRIPTS = {
    "python":     ["mode/python/python.min.js"],
    "javascript": ["mode/javascript/javascript.min.js"],
    "htmlmixed":  ["mode/xml/xml.min.js",
                   "mode/javascript/javascript.min.js",
                   "mode/css/css.min.js",
                   "mode/htmlmixed/htmlmixed.min.js"],
    "css":        ["mode/css/css.min.js"],
    "shell":      ["mode/shell/shell.min.js"],
    "markdown":   ["mode/markdown/markdown.min.js"],
    "yaml":       ["mode/yaml/yaml.min.js"],
    "xml":        ["mode/xml/xml.min.js"],
    "sql":        ["mode/sql/sql.min.js"],
}

CDN = "https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.16/"

# 编辑器模板：JS 代码块全部放在 {% raw %} ... {% endraw %} 里，
# 只有真正需要 Python 值的地方才用 {{ }} 变量
EDITOR_TPL = """\
<!DOCTYPE html><html lang='zh'><head>
<meta charset='UTF-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>编辑 {{ e_path }} — PhonePaaS</title>
{{ e_css | safe }}
<link rel='stylesheet' href='{{ e_cdn }}codemirror.min.css'>
<link rel='stylesheet' href='{{ e_cdn }}theme/material-darker.min.css'>
<style>
.CodeMirror{
  height: calc(100vh - 160px);
  min-height: 400px;
  font-size: 13px;
  font-family: var(--mono) !important;
  border: 1px solid var(--bd);
  border-radius: 0 0 var(--r) var(--r);
}
</style>
</head><body>
<nav>
  <div class='logo'><div class='ldot'></div>PhonePaaS</div>
  <div class='nav-r'>{{ e_nav | safe }}</div>
</nav>
<div style='max-width:1040px;margin:0 auto;padding:14px 18px'>
  {% with msgs = get_flashed_messages(with_categories=true) %}
    {% for cat, txt in msgs %}
      <div class='fl fl{{ cat[0] }}'>{{ txt }}</div>
    {% endfor %}
  {% endwith %}

  <div class='ph' style='margin-bottom:10px'>
    <div>
      <h1 style='font-family:var(--mono);font-size:1rem'>{{ e_path }}</h1>
      <p style='color:var(--dim);font-size:.78rem'>{{ e_svc_title }}</p>
    </div>
  </div>

  <form method='post' id='ef'>
    <input type='hidden' name='path' value='{{ e_path }}'>
    <div class='ed-bar'>
      <span style='font-family:var(--mono);font-size:.78rem;color:var(--acc)'>{{ e_path }}</span>
      <div style='display:flex;gap:8px;align-items:center'>
        <span id='ed-status' style='font-size:.72rem;color:var(--dim)'></span>
        <a href='{{ e_back_url }}' class='btn bgg sm'>取消</a>
        <button type='submit' class='btn bp sm'>
          <svg width='13' height='13' viewBox='0 0 13 13' fill='none' stroke='currentColor'
               stroke-width='1.4' stroke-linecap='round' stroke-linejoin='round'
               style='vertical-align:middle'>
            <path d='M2 1h7l2 2v9H2V1z'/>
            <path d='M4 1v3h4V1'/>
            <rect x='3' y='7' width='6' height='5' rx='0.5'/>
          </svg>
          保存 (Ctrl+S)
        </button>
      </div>
    </div>
    <textarea id='ed' name='code' style='display:none'></textarea>
    <div id='cm-wrap'></div>
  </form>
</div>

<script src='{{ e_cdn }}codemirror.min.js'></script>
{% for s in e_extra_scripts %}
  <script src='{{ e_cdn }}{{ s }}'></script>
{% endfor %}
<script src='{{ e_cdn }}addon/edit/matchbrackets.min.js'></script>
<script src='{{ e_cdn }}addon/edit/closebrackets.min.js'></script>
<script src='{{ e_cdn }}addon/comment/comment.min.js'></script>

<script>
/* 用 JSON 安全注入两个值，完全不受代码内容特殊字符影响 */
var _CODE = {{ e_code_json | safe }};
var _MODE = {{ e_mode_json | safe }};
</script>
<script>
(function () {
  var cm = CodeMirror(document.getElementById('cm-wrap'), {
    value:             _CODE,
    mode:              _MODE,
    theme:             'material-darker',
    lineNumbers:       true,
    matchBrackets:     true,
    autoCloseBrackets: true,
    indentUnit:        4,
    tabSize:           4,
    indentWithTabs:    false,
    lineWrapping:      false,
    autofocus:         true,
    extraKeys: {
      'Ctrl-S': function () { doSave(); },
      'Cmd-S':  function () { doSave(); },
      'Tab':    function (c) { c.execCommand('insertSoftTab'); },
      'Ctrl-/': 'toggleComment'
    }
  });

  function doSave() {
    document.getElementById('ed').value = cm.getValue();
    document.getElementById('ef').submit();
  }

  document.getElementById('ef').addEventListener('submit', function () {
    document.getElementById('ed').value = cm.getValue();
  });

  var isSaved = true;
  cm.on('change', function () {
    if (isSaved) {
      isSaved = false;
      document.getElementById('ed-status').textContent = '● 未保存';
    }
  });
  document.getElementById('ef').addEventListener('submit', function () {
    isSaved = true;
  });
}());
</script>
</body></html>"""

@main_app.route("/svc/<int:sid>/edit", methods=["GET","POST"])
@login_required
def edit_file(sid):
    u = me()
    with db() as c:
        svc = c.execute("SELECT * FROM services WHERE id=? AND user_id=?", [sid, u["id"]]).fetchone()
    if not svc:
        abort(404)

    path = (request.args.get("path") or request.form.get("path","")).strip("/")
    if not path:
        abort(400)
    target = safe_path(svc_dir(svc), path)
    if not target:
        abort(403)

    back_rel = str(Path(path).parent) if str(Path(path).parent) != "." else ""
    back_url = "/svc/{}".format(sid) + ("?rel={}".format(back_rel) if back_rel else "")

    if request.method == "POST":
        code = request.form.get("code","")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(code, encoding="utf-8")
        flash("已保存", "success")
        return redirect("/svc/{}/edit?path={}".format(sid, path))

    code = target.read_text(encoding="utf-8", errors="replace") if target.exists() else ""

    ext           = Path(path).suffix.lower()
    cm_mode       = EXT_MODE.get(ext)          # None → 纯文本
    extra_scripts = MODE_SCRIPTS.get(cm_mode, [])
    code_json     = json.dumps(code)            # Python str → JS 字符串字面量
    mode_json     = json.dumps(cm_mode)         # None→"null"  "python"→'"python"'

    user = me()
    if user:
        nav = '<a href="/">仪表盘</a><a href="/logout">退出 {}</a>'.format(user["username"])
    else:
        nav = '<a href="/login">登录</a>'

    return render_template_string(
        EDITOR_TPL,
        e_cdn           = CDN,
        e_path          = path,
        e_svc_title     = svc["title"] or svc["name"],
        e_back_url      = back_url,
        e_code_json     = code_json,
        e_mode_json     = mode_json,
        e_extra_scripts = extra_scripts,
        e_css           = CSS,
        e_nav           = nav,
    )

# ═══ 部署 / 停止 / 删除路由 ════════════════════════════════════
@main_app.route("/svc/<int:sid>/deploy", methods=["POST"])
@login_required
def do_deploy(sid):
    u = me()
    with db() as c:
        svc = c.execute("SELECT * FROM services WHERE id=? AND user_id=?", [sid, u["id"]]).fetchone()
    if not svc:
        abort(404)
    ok, msg = deploy(sid)
    flash(msg, "success" if ok else "error")
    return redirect(request.referrer or "/")

@main_app.route("/svc/<int:sid>/undeploy", methods=["POST"])
@login_required
def do_undeploy(sid):
    u = me()
    with db() as c:
        svc = c.execute("SELECT * FROM services WHERE id=? AND user_id=?", [sid, u["id"]]).fetchone()
    if not svc:
        abort(404)
    undeploy(sid)
    flash("服务已停止", "info")
    return redirect(request.referrer or "/")

@main_app.route("/svc/<int:sid>/remove", methods=["POST"])
@login_required
def remove_svc(sid):
    u = me()
    with db() as c:
        svc = c.execute("SELECT * FROM services WHERE id=? AND user_id=?", [sid, u["id"]]).fetchone()
    if not svc:
        abort(404)
    undeploy(sid)
    shutil.rmtree(svc_dir(svc), ignore_errors=True)
    with db() as c:
        c.execute("DELETE FROM services WHERE id=?", [sid])
    flash("服务已删除", "info")
    return redirect("/")

# ═══ 启动 ══════════════════════════════════════════════════════
if __name__ == "__main__":
    Path(SVC_DIR).mkdir(exist_ok=True)
    init_db()

    with db() as c:
        rows = c.execute("SELECT * FROM services WHERE status='running'").fetchall()
    for svc in rows:
        ok, msg = deploy(svc["id"])
        print("  自动恢复 [{}]: {}".format(svc["name"], "OK" if ok else "FAIL — " + msg))

    print("""
  PhonePaaS v3 就绪
  本地: http://0.0.0.0:{port}
  公网: {pub}
  用户服务路径: {pub}{pfx}/<名称>/
""".format(port=PORT, pub=PUBLIC_URL, pfx=PREFIX))

    run_simple("0.0.0.0", PORT, dispatcher, use_reloader=False, threaded=True)
