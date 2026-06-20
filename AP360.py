import os
import csv
import io
import json
import secrets
from datetime import datetime
from functools import wraps

import requests
from flask import (
    Flask, request, redirect, url_for, flash, render_template_string,
    abort, jsonify, Response
)
from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager, UserMixin, login_user, logout_user, login_required, current_user
)
from werkzeug.security import generate_password_hash, check_password_hash


# =========================================================
# CONFIG
# =========================================================
def normalize_database_url(db_url: str) -> str:
    if not db_url:
        return "sqlite:///ap360.db"
    if db_url.startswith("postgres://"):
        return db_url.replace("postgres://", "postgresql://", 1)
    return db_url


app = Flask(__name__)
app.url_map.strict_slashes = False
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "ap360vitor")
app.config["SQLALCHEMY_DATABASE_URI"] = normalize_database_url(
    os.getenv("DATABASE_URL", "sqlite:///ap360.db")
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "vitor26.nathank@gmail.com").strip().lower()
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "2021vitor")

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"


# =========================================================
# MODELS
# =========================================================
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(120), nullable=False)
    cpf = db.Column(db.String(20), nullable=True, unique=True)
    telefone = db.Column(db.String(30), nullable=True)
    email = db.Column(db.String(120), nullable=False, unique=True, index=True)
    password_hash = db.Column(db.String(255), nullable=False)

    perfil = db.Column(db.String(20), nullable=False, default="produtor")  # admin/produtor
    status = db.Column(db.String(20), nullable=False, default="ativo")      # ativo/bloqueado
    segmento = db.Column(db.String(20), nullable=True)                       # agricultura/pecuaria
    cooperativa = db.Column(db.String(120), nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def set_password(self, raw):
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw):
        return check_password_hash(self.password_hash, raw)


class AccessRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(120), nullable=False)
    cpf = db.Column(db.String(20), nullable=True)
    telefone = db.Column(db.String(30), nullable=True)
    email = db.Column(db.String(120), nullable=False, index=True)
    segmento = db.Column(db.String(20), nullable=False)
    cooperativa = db.Column(db.String(120), nullable=True)
    status = db.Column(db.String(20), default="pendente")  # pendente/liberado/negado
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class AccessInvite(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), nullable=False, unique=True, index=True)
    token = db.Column(db.String(120), nullable=False, unique=True, index=True)
    status = db.Column(db.String(20), default="convidado")  # convidado/ativado
    request_id = db.Column(db.Integer, db.ForeignKey("access_request.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    activated_at = db.Column(db.DateTime, nullable=True)


class CoopBenchmark(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    cadeia = db.Column(db.String(20), nullable=False)  # avicultura/suinocultura
    cooperativa = db.Column(db.String(120), nullable=False)
    media_gpd = db.Column(db.Float, default=0.0)
    media_ca = db.Column(db.Float, default=0.0)
    bonus_base = db.Column(db.Float, default=1000.0)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow)


class AgricultureQuote(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    produto = db.Column(db.String(40), nullable=False)
    quantidade_ton = db.Column(db.Float, nullable=False)
    origem = db.Column(db.String(120), nullable=False)
    porto = db.Column(db.String(80), nullable=False)

    cbot_usd_bushel = db.Column(db.Float, nullable=False)
    usd_brl = db.Column(db.Float, nullable=False)
    export_rs_ton = db.Column(db.Float, nullable=False)
    frete_rs_ton = db.Column(db.Float, nullable=False)
    liquido_rs_ton = db.Column(db.Float, nullable=False)
    total_rs = db.Column(db.Float, nullable=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Batch(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    cadeia = db.Column(db.String(20), nullable=False)  # avicultura/suinocultura
    estrutura = db.Column(db.String(40), nullable=False)
    lote = db.Column(db.String(40), nullable=False)

    peso_inicial = db.Column(db.Float, nullable=False)
    peso_final = db.Column(db.Float, nullable=False)
    dias = db.Column(db.Integer, nullable=False)
    racao_total_kg = db.Column(db.Float, nullable=False)

    gpd = db.Column(db.Float, nullable=False)
    ca = db.Column(db.Float, nullable=False)
    bonificacao = db.Column(db.Float, nullable=False)

    coop_media_gpd = db.Column(db.Float, default=0.0)
    coop_media_ca = db.Column(db.Float, default=0.0)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Bovino(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

    brinco = db.Column(db.String(40), nullable=False, unique=True)
    nome = db.Column(db.String(80), nullable=True)
    sexo = db.Column(db.String(10), nullable=True)
    raca = db.Column(db.String(60), nullable=True)
    nascimento = db.Column(db.String(10), nullable=True)  # YYYY-MM-DD

    origem = db.Column(db.String(120), nullable=True)
    lote = db.Column(db.String(60), nullable=True)
    status = db.Column(db.String(30), default="ativo")  # ativo/vendido/descartado

    peso_atual = db.Column(db.Float, default=0.0)
    ultima_pesagem = db.Column(db.String(10), nullable=True)

    observacoes = db.Column(db.Text, default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class BovinoPeso(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    bovino_id = db.Column(db.Integer, db.ForeignKey("bovino.id"), nullable=False)
    data = db.Column(db.String(10), nullable=False)  # YYYY-MM-DD
    peso = db.Column(db.Float, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class BovinoEvento(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    bovino_id = db.Column(db.Integer, db.ForeignKey("bovino.id"), nullable=False)
    tipo = db.Column(db.String(40), nullable=False)  # vacina, vermifugo, manejo, inseminacao etc.
    descricao = db.Column(db.Text, nullable=False)
    data = db.Column(db.String(10), nullable=False)  # YYYY-MM-DD
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


# =========================================================
# AUTH
# =========================================================
@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for("login"))
        if current_user.perfil != "admin":
            abort(403)
        return fn(*args, **kwargs)
    return wrapper


# =========================================================
# BUSINESS HELPERS
# =========================================================
CBOT_FALLBACK = {
    "soja": 11.80,
    "milho": 4.65,
    "trigo": 5.90,
    "aveia": 3.45,
    "arroz": 15.20
}
BUSHEL_KG = {
    "soja": 27.2155,
    "milho": 25.4012,
    "trigo": 27.2155,
    "aveia": 14.515,
    "arroz": 20.412
}
PORTOS = ["Paranaguá", "Santos", "Rio Grande", "Itajaí"]
FRETE_MEDIO = {
    "PR": {"Paranaguá": 120, "Santos": 170, "Rio Grande": 190, "Itajaí": 180},
    "MT": {"Paranaguá": 420, "Santos": 390, "Rio Grande": 460, "Itajaí": 440},
    "MS": {"Paranaguá": 260, "Santos": 240, "Rio Grande": 300, "Itajaí": 295},
    "RS": {"Paranaguá": 230, "Santos": 260, "Rio Grande": 110, "Itajaí": 210},
    "SC": {"Paranaguá": 170, "Santos": 220, "Rio Grande": 180, "Itajaí": 90},
}
DEFAULT_WHATSAPP = "https://wa.me/5545999037929"


def get_fx_usd_brl():
    # fallback fixo (pode trocar por API de câmbio)
    return 5.35


def get_cbot_usd_bushel(produto: str) -> float:
    return CBOT_FALLBACK.get(produto.lower(), 0.0)


def cbot_to_rs_ton(produto: str, usd_bushel: float, usd_brl: float) -> float:
    kg = BUSHEL_KG.get(produto.lower(), 27.2155)
    usd_ton = usd_bushel * (1000 / kg)
    return round(usd_ton * usd_brl, 2)


def calc_export_price_rs_ton(produto: str, porto: str):
    usd = get_cbot_usd_bushel(produto)
    fx = get_fx_usd_brl()
    base_rs = cbot_to_rs_ton(produto, usd, fx)
    premio_porto = 35 if porto in ["Paranaguá", "Santos"] else 28
    return round(base_rs + premio_porto, 2), usd, fx


def get_uf(origem: str) -> str:
    parts = (origem or "").upper().split("-")
    return parts[-1].strip() if len(parts) > 1 else "PR"


def calc_frete_medio(origem: str, porto: str) -> float:
    uf = get_uf(origem)
    return float(FRETE_MEDIO.get(uf, {}).get(porto, 250.0))


def calc_gpd(peso_i: float, peso_f: float, dias: int) -> float:
    if dias <= 0:
        return 0.0
    return round((peso_f - peso_i) / dias, 4)


def calc_ca(racao_total_kg: float, peso_i: float, peso_f: float) -> float:
    ganho = peso_f - peso_i
    if ganho <= 0:
        return 0.0
    return round(racao_total_kg / ganho, 4)


def calc_bonus(gpd: float, ca: float, media_gpd=0.065, media_ca=1.70, bonus_base=1000.0) -> float:
    if ca <= 0:
        return 0.0
    score = (gpd / media_gpd) * 50 + (media_ca / ca) * 50
    pct = max(-0.20, min(0.35, (score - 100) / 100))
    return round(bonus_base * pct, 2)


def get_coop_benchmark(cadeia: str, cooperativa: str):
    if not cooperativa:
        return 0.065, 1.70, 1000.0
    row = CoopBenchmark.query.filter_by(cadeia=cadeia, cooperativa=cooperativa).first()
    if row:
        return row.media_gpd or 0.065, row.media_ca or 1.70, row.bonus_base or 1000.0
    return 0.065, 1.70, 1000.0


# =========================================================
# UI LAYOUT
# =========================================================
BASE_HTML = """
<!doctype html>
<html lang="pt-br">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{{ title or "AP360" }}</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;700;800&display=swap" rel="stylesheet">
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <style>
    :root{
      --bg:#0d1324;
      --card:rgba(255,255,255,.11);
      --line:rgba(255,255,255,.22);
      --text:#f7f9ff;
      --muted:#ced6e6;
      --ok:#44dd95;
      --pri:#36b8ff;
    }
    *{box-sizing:border-box}
    body{
      margin:0;
      font-family:Inter,Arial,sans-serif;
      color:var(--text);
      background:
        linear-gradient(120deg, rgba(54,184,255,.15), rgba(68,221,149,.12)),
        url('https://images.unsplash.com/photo-1500937386664-56d1dfef3854?q=80&w=1800&auto=format&fit=crop') center/cover fixed no-repeat;
      min-height:100vh;
    }
    .wrap{min-height:100vh;background:linear-gradient(180deg, rgba(9,15,31,.74), rgba(9,15,31,.9));padding:20px}
    .container{max-width:1200px;margin:0 auto}
    .nav{display:flex;justify-content:space-between;align-items:center;gap:10px;background:rgba(255,255,255,.07);
      border:1px solid var(--line);padding:12px 16px;border-radius:14px;backdrop-filter:blur(8px);margin-bottom:16px}
    .brand{font-weight:800}
    .brand b{color:var(--ok)}
    .links a{color:var(--text);text-decoration:none;margin-left:12px;font-size:.94rem}
    .hero{border:1px solid var(--line);border-radius:20px;padding:28px;background:linear-gradient(145deg, rgba(255,255,255,.14), rgba(255,255,255,.05));backdrop-filter:blur(8px)}
    .card{border:1px solid var(--line);border-radius:16px;padding:16px;background:var(--card);margin-top:14px}
    .grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}
    .grid3{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}
    @media (max-width:900px){.grid,.grid3{grid-template-columns:1fr}}
    .btn{display:inline-block;padding:10px 14px;border:none;border-radius:12px;font-weight:700;cursor:pointer;text-decoration:none}
    .btn-ok{background:linear-gradient(135deg,var(--ok),#3fcb86);color:#042414}
    .btn-pri{background:linear-gradient(135deg,var(--pri),#6f89ff);color:#06182d}
    .btn-ghost{background:rgba(255,255,255,.14);color:#fff}
    input,select,textarea{width:100%;padding:10px;border:1px solid var(--line);border-radius:10px;background:rgba(255,255,255,.08);color:#fff;margin:5px 0}
    input::placeholder,textarea::placeholder{color:#dce6ff9a}
    table{width:100%;border-collapse:collapse;font-size:.92rem}
    th,td{border:1px solid var(--line);padding:8px;text-align:left}
    .flash{padding:10px 12px;background:rgba(255,255,255,.1);border:1px solid var(--line);border-radius:10px;margin-bottom:10px}
    .muted{color:var(--muted)}
    .kpi{font-size:1.3rem;font-weight:800}
  </style>
</head>
<body>
<div class="wrap">
  <div class="container">
    <div class="nav">
      <div class="brand">AP<b>360</b> — AgroPulse 360</div>
      <div class="links">
        {% if current_user.is_authenticated %}
          <a href="{{ url_for('dashboard') }}">Dashboard</a>
          <a href="{{ url_for('agricultura') }}">Agricultura</a>
          <a href="{{ url_for('avicultura') }}">Avicultura</a>
          <a href="{{ url_for('suinocultura') }}">Suinocultura</a>
          <a href="{{ url_for('bovinocultura') }}">Bovinocultura</a>
          <a href="{{ url_for('ai_page') }}">IA</a>
          {% if current_user.perfil == "admin" %}
            <a href="{{ url_for('admin_panel') }}">Admin</a>
          {% endif %}
          <a href="{{ url_for('logout') }}">Sair</a>
        {% else %}
          <a href="{{ url_for('index') }}">Início</a>
          <a href="{{ url_for('login') }}">Login</a>
          <a href="{{ url_for('signup_request') }}">Inscreva-se</a>
        {% endif %}
      </div>
    </div>

    {% for m in get_flashed_messages() %}
      <div class="flash">{{ m }}</div>
    {% endfor %}

    {{ content|safe }}
  </div>
</div>
</body>
</html>
"""


def page(content: str, **ctx):
    return render_template_string(BASE_HTML, content=content, **ctx)


# =========================================================
# INIT DB + ADMIN
# =========================================================
with app.app_context():
    db.create_all()
    adm = User.query.filter_by(email=ADMIN_EMAIL).first()
    if not adm:
        adm = User(
            nome="Administrador",
            email=ADMIN_EMAIL,
            perfil="admin",
            status="ativo",
            segmento="agricultura",
            cooperativa="N/A"
        )
        adm.set_password(ADMIN_PASSWORD)
        db.session.add(adm)
        db.session.commit()

    # seed benchmarks padrão
    if CoopBenchmark.query.count() == 0:
        db.session.add(CoopBenchmark(cadeia="avicultura", cooperativa="Coop Padrão", media_gpd=0.066, media_ca=1.68, bonus_base=1000))
        db.session.add(CoopBenchmark(cadeia="suinocultura", cooperativa="Coop Padrão", media_gpd=0.72, media_ca=2.45, bonus_base=1200))
        db.session.commit()


# =========================================================
# ROUTES - HOME / AUTH
# =========================================================
@app.route("/")
def index():
    html = """
    <section class="hero">
      <h1 style="margin:0;font-size:2.3rem">Gestão Agro completa em um só sistema</h1>
      <p class="muted">Lotes de avicultura/suinocultura, controle bovino individual, simulação agrícola com CBOT e frete médio por porto.</p>
      <div style="margin-top:12px;display:flex;gap:10px;flex-wrap:wrap">
        <a class="btn btn-ok" href="{{ url_for('login') }}">Entrar</a>
        <a class="btn btn-pri" href="{{ url_for('signup_request') }}">Não tem uma conta? Inscreva-se</a>
      </div>
    </section>
    <div class="grid">
      <div class="card"><h3>Agricultura</h3><p class="muted">Soja, milho, trigo, aveia, arroz e mais. Cálculo: exportação, frete e líquido.</p></div>
      <div class="card"><h3>Pecuária</h3><p class="muted">Avicultura, Suinocultura e Bovinocultura com indicadores e comparativos.</p></div>
    </div>
    """
    return page(html, title="AP360 | Início")


@app.route("/login", methods=["GET", "POST"])
@app.route("/login/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        senha = request.form.get("senha", "")
        user = User.query.filter_by(email=email).first()

        if not user or not user.check_password(senha):
            flash("Credenciais inválidas.")
            return redirect(url_for("login"))
        if user.status != "ativo":
            flash("Conta bloqueada ou pendente.")
            return redirect(url_for("login"))

        login_user(user)
        return redirect(url_for("dashboard"))

    html = """
    <div class="card" style="max-width:520px;margin:20px auto">
      <h2 style="margin-top:0">Login</h2>
      <form method="post">
        <input type="email" name="email" placeholder="Seu e-mail" required>
        <input type="password" name="senha" placeholder="Sua senha" required>
        <button class="btn btn-ok" type="submit">Entrar</button>
      </form>
      <p class="muted">Não tem uma conta? <a href="{{ url_for('signup_request') }}">Inscreva-se</a></p>
    </div>
    """
    return page(html, title="AP360 | Login")


@app.route("/inscreva-se", methods=["GET", "POST"])
@app.route("/inscreva_se", methods=["GET", "POST"])
def signup_request():
    """
    Fluxo:
    1) Usuário deixa dados e aparece WhatsApp para pagamento.
    2) Admin libera o e-mail (gera convite/token).
    3) Usuário ativa conta e define sua própria senha.
    """
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()

        inv = AccessInvite.query.filter_by(email=email, status="convidado").first()
        if inv:
            return redirect(url_for("activate_account", token=inv.token))

        req = AccessRequest(
            nome=request.form.get("nome", "").strip(),
            cpf=request.form.get("cpf", "").strip(),
            telefone=request.form.get("telefone", "").strip(),
            email=email,
            segmento=request.form.get("segmento", "agricultura"),
            cooperativa=request.form.get("cooperativa", "").strip(),
            status="pendente"
        )
        db.session.add(req)
        db.session.commit()
        flash("Cadastro recebido. Faça o pagamento mensal no WhatsApp e aguarde liberação do admin.")
        flash("WhatsApp: +55 (45) 9 9903-7929")
        return redirect(url_for("signup_request"))

    html = f"""
    <div class="card" style="max-width:680px;margin:0 auto">
      <h2 style="margin-top:0">Inscrição</h2>
      <form method="post">
        <input name="nome" placeholder="Nome completo" required>
        <input name="cpf" placeholder="CPF" required>
        <input name="telefone" placeholder="Telefone" required>
        <input name="email" type="email" placeholder="E-mail" required>
        <select name="segmento" required>
          <option value="agricultura">Agricultura</option>
          <option value="pecuaria">Pecuária</option>
        </select>
        <input name="cooperativa" placeholder="Cooperativa (opcional)">
        <button class="btn btn-pri" type="submit">Enviar inscrição</button>
      </form>
      <p class="muted">
        Pagamento mensal:
        <a href="{DEFAULT_WHATSAPP}" target="_blank">chamar no WhatsApp +55 (45) 9 9903-7929</a>
      </p>
    </div>
    """
    return page(html, title="AP360 | Inscrição")


@app.route("/ativar/<token>", methods=["GET", "POST"])
def activate_account(token):
    inv = AccessInvite.query.filter_by(token=token, status="convidado").first()
    if not inv:
        return "Token inválido ou já usado.", 400

    if request.method == "POST":
        senha = request.form.get("senha", "")
        confirmar = request.form.get("confirmar_senha", "")

        if senha != confirmar:
            flash("Senha e confirmação não conferem.")
            return redirect(url_for("activate_account", token=token))
        if len(senha) < 6:
            flash("Senha deve ter ao menos 6 caracteres.")
            return redirect(url_for("activate_account", token=token))

        if User.query.filter_by(email=inv.email).first():
            flash("Conta já existe para este e-mail. Faça login.")
            return redirect(url_for("login"))

        req = AccessRequest.query.get(inv.request_id) if inv.request_id else None

        user = User(
            nome=(req.nome if req else "Produtor"),
            cpf=(req.cpf if req else None),
            telefone=(req.telefone if req else None),
            email=inv.email,
            perfil="produtor",
            status="ativo",
            segmento=(req.segmento if req else "agricultura"),
            cooperativa=(req.cooperativa if req else "Coop Padrão")
        )
        user.set_password(senha)
        db.session.add(user)

        inv.status = "ativado"
        inv.activated_at = datetime.utcnow()
        if req:
            req.status = "liberado"

        db.session.commit()
        flash("Conta ativada com sucesso. Faça login.")
        return redirect(url_for("login"))

    html = """
    <div class="card" style="max-width:620px;margin:0 auto">
      <h2 style="margin-top:0">Ativar conta</h2>
      <p class="muted">E-mail liberado: <b>{{ inv.email }}</b>. Defina sua senha.</p>
      <form method="post">
        <input type="password" name="senha" placeholder="Crie sua senha" required>
        <input type="password" name="confirmar_senha" placeholder="Confirme a senha" required>
        <button class="btn btn-ok" type="submit">Ativar</button>
      </form>
    </div>
    """
    return page(html, inv=inv, title="AP360 | Ativar conta")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


# =========================================================
# DASHBOARD
# =========================================================
@app.route("/dashboard")
@login_required
def dashboard():
    ag_count = AgricultureQuote.query.filter_by(user_id=current_user.id).count()
    av_count = Batch.query.filter_by(user_id=current_user.id, cadeia="avicultura").count()
    su_count = Batch.query.filter_by(user_id=current_user.id, cadeia="suinocultura").count()
    bo_count = Bovino.query.filter_by(user_id=current_user.id).count()

    html = """
    <section class="hero">
      <h1 style="margin:0">Bem-vindo, {{ current_user.nome }}</h1>
      <p class="muted">Segmento: <b>{{ current_user.segmento or 'não definido' }}</b> |
      Cooperativa: <b>{{ current_user.cooperativa or 'N/A' }}</b></p>
    </section>

    <div class="grid3">
      <div class="card"><div class="muted">Cotações agrícolas</div><div class="kpi">{{ ag_count }}</div></div>
      <div class="card"><div class="muted">Lotes avicultura</div><div class="kpi">{{ av_count }}</div></div>
      <div class="card"><div class="muted">Lotes suinocultura</div><div class="kpi">{{ su_count }}</div></div>
    </div>
    <div class="card"><div class="muted">Bovinos cadastrados</div><div class="kpi">{{ bo_count }}</div></div>

    <div class="grid">
      <div class="card">
        <h3>Agricultura</h3>
        <p class="muted">Selecione produto, porto e origem para estimar valor líquido e total.</p>
        <a class="btn btn-pri" href="{{ url_for('agricultura') }}">Abrir módulo</a>
      </div>
      <div class="card">
        <h3>Pecuária</h3>
        <p class="muted">GPD, CA, bonificação e comparação de estruturas/lotes.</p>
        <a class="btn btn-ok" href="{{ url_for('avicultura') }}">Avicultura</a>
        <a class="btn btn-pri" href="{{ url_for('suinocultura') }}">Suinocultura</a>
        <a class="btn btn-ghost" href="{{ url_for('bovinocultura') }}">Bovinocultura</a>
      </div>
    </div>
    """
    return page(
        html,
        title="AP360 | Dashboard",
        ag_count=ag_count, av_count=av_count, su_count=su_count, bo_count=bo_count
    )


# =========================================================
# ADMIN
# =========================================================
@app.route("/admin", methods=["GET", "POST"])
@login_required
@admin_required
def admin_panel():
    if request.method == "POST":
        form_type = request.form.get("form_type")

        if form_type == "manual_invite":
            email = request.form.get("email", "").strip().lower()
            if not email:
                flash("E-mail inválido.")
                return redirect(url_for("admin_panel"))
            exists = AccessInvite.query.filter_by(email=email).first()
            if exists:
                flash("E-mail já possui convite.")
                return redirect(url_for("admin_panel"))
            token = secrets.token_urlsafe(24)
            db.session.add(AccessInvite(email=email, token=token, status="convidado"))
            db.session.commit()
            flash(f"Convite criado: {request.url_root.rstrip('/')}/ativar/{token}")
            return redirect(url_for("admin_panel"))

        if form_type == "approve_request":
            req_id = int(request.form.get("request_id"))
            req = AccessRequest.query.get_or_404(req_id)
            inv = AccessInvite.query.filter_by(email=req.email).first()
            if inv:
                flash("Esse e-mail já tem convite.")
            else:
                token = secrets.token_urlsafe(24)
                db.session.add(AccessInvite(email=req.email, token=token, status="convidado", request_id=req.id))
                req.status = "liberado"
                db.session.commit()
                flash(f"Convite gerado para {req.email}: {request.url_root.rstrip('/')}/ativar/{token}")
            return redirect(url_for("admin_panel"))

        if form_type == "set_benchmark":
            cadeia = request.form.get("cadeia")
            cooperativa = request.form.get("cooperativa", "").strip()
            media_gpd = float(request.form.get("media_gpd", 0))
            media_ca = float(request.form.get("media_ca", 0))
            bonus_base = float(request.form.get("bonus_base", 1000))
            row = CoopBenchmark.query.filter_by(cadeia=cadeia, cooperativa=cooperativa).first()
            if not row:
                row = CoopBenchmark(cadeia=cadeia, cooperativa=cooperativa)
                db.session.add(row)
            row.media_gpd = media_gpd
            row.media_ca = media_ca
            row.bonus_base = bonus_base
            row.updated_at = datetime.utcnow()
            db.session.commit()
            flash("Benchmark salvo.")
            return redirect(url_for("admin_panel"))

    requests_rows = AccessRequest.query.order_by(AccessRequest.created_at.desc()).limit(50).all()
    invites = AccessInvite.query.order_by(AccessInvite.created_at.desc()).limit(50).all()
    users = User.query.order_by(User.created_at.desc()).limit(50).all()
    benches = CoopBenchmark.query.order_by(CoopBenchmark.updated_at.desc()).all()

    html = """
    <h2>Painel Admin</h2>

    <div class="grid">
      <div class="card">
        <h3>Liberar e-mail manualmente</h3>
        <form method="post">
          <input type="hidden" name="form_type" value="manual_invite">
          <input type="email" name="email" placeholder="email@dominio.com" required>
          <button class="btn btn-ok" type="submit">Liberar</button>
        </form>
      </div>

      <div class="card">
        <h3>Configurar benchmark de cooperativa</h3>
        <form method="post">
          <input type="hidden" name="form_type" value="set_benchmark">
          <select name="cadeia" required><option>avicultura</option><option>suinocultura</option></select>
          <input name="cooperativa" placeholder="Nome da cooperativa" required>
          <input type="number" step="0.0001" name="media_gpd" placeholder="Média GPD" required>
          <input type="number" step="0.0001" name="media_ca" placeholder="Média CA" required>
          <input type="number" step="0.01" name="bonus_base" placeholder="Base bonificação R$" required>
          <button class="btn btn-pri" type="submit">Salvar benchmark</button>
        </form>
      </div>
    </div>

    <div class="card">
      <h3>Solicitações de acesso</h3>
      <table>
        <tr><th>Data</th><th>Nome</th><th>Email</th><th>Segmento</th><th>Status</th><th>Ação</th></tr>
        {% for r in requests_rows %}
          <tr>
            <td>{{ r.created_at.strftime("%d/%m %H:%M") }}</td>
            <td>{{ r.nome }}</td>
            <td>{{ r.email }}</td>
            <td>{{ r.segmento }}</td>
            <td>{{ r.status }}</td>
            <td>
              <form method="post" style="margin:0">
                <input type="hidden" name="form_type" value="approve_request">
                <input type="hidden" name="request_id" value="{{ r.id }}">
                <button class="btn btn-ok" type="submit">Gerar convite</button>
              </form>
            </td>
          </tr>
        {% endfor %}
      </table>
    </div>

    <div class="card">
      <h3>Convites</h3>
      <table>
        <tr><th>Email</th><th>Status</th><th>Token</th><th>Criado</th></tr>
        {% for i in invites %}
          <tr>
            <td>{{ i.email }}</td><td>{{ i.status }}</td><td>{{ i.token }}</td>
            <td>{{ i.created_at.strftime("%d/%m %H:%M") }}</td>
          </tr>
        {% endfor %}
      </table>
    </div>

    <div class="card">
      <h3>Usuários</h3>
      <table>
        <tr><th>Nome</th><th>Email</th><th>Perfil</th><th>Status</th><th>Segmento</th></tr>
        {% for u in users %}
          <tr><td>{{ u.nome }}</td><td>{{ u.email }}</td><td>{{ u.perfil }}</td><td>{{ u.status }}</td><td>{{ u.segmento }}</td></tr>
        {% endfor %}
      </table>
    </div>

    <div class="card">
      <h3>Benchmarks cadastrados</h3>
      <table>
        <tr><th>Cadeia</th><th>Cooperativa</th><th>GPD</th><th>CA</th><th>Base bônus</th></tr>
        {% for b in benches %}
          <tr><td>{{ b.cadeia }}</td><td>{{ b.cooperativa }}</td><td>{{ b.media_gpd }}</td><td>{{ b.media_ca }}</td><td>{{ b.bonus_base }}</td></tr>
        {% endfor %}
      </table>
    </div>
    """
    return page(
        html, title="AP360 | Admin",
        requests_rows=requests_rows, invites=invites, users=users, benches=benches
    )


# =========================================================
# AGRICULTURA
# =========================================================
@app.route("/agricultura", methods=["GET", "POST"])
@login_required
def agricultura():
    resultado = None

    if request.method == "POST":
        produto = request.form.get("produto", "soja").lower()
        quantidade_ton = float(request.form.get("quantidade_ton", 0))
        origem = request.form.get("origem", "")
        porto = request.form.get("porto", "Paranaguá")

        export_rs_ton, cbot, fx = calc_export_price_rs_ton(produto, porto)
        frete = calc_frete_medio(origem, porto)
        liquido = round(export_rs_ton - frete, 2)
        total = round(liquido * quantidade_ton, 2)

        row = AgricultureQuote(
            user_id=current_user.id,
            produto=produto,
            quantidade_ton=quantidade_ton,
            origem=origem,
            porto=porto,
            cbot_usd_bushel=cbot,
            usd_brl=fx,
            export_rs_ton=export_rs_ton,
            frete_rs_ton=frete,
            liquido_rs_ton=liquido,
            total_rs=total
        )
        db.session.add(row)
        db.session.commit()
        resultado = row

    hist = AgricultureQuote.query.filter_by(user_id=current_user.id).order_by(AgricultureQuote.created_at.desc()).limit(30).all()

    html = """
    <h2>Agricultura</h2>
    <div class="grid">
      <div class="card">
        <form method="post">
          <select name="produto" required>
            <option value="soja">Soja</option>
            <option value="milho">Milho</option>
            <option value="trigo">Trigo</option>
            <option value="aveia">Aveia</option>
            <option value="arroz">Arroz</option>
          </select>
          <input type="number" step="0.01" name="quantidade_ton" placeholder="Quantidade (ton)" required>
          <input name="origem" placeholder="Origem (ex.: Cascavel-PR)" required>
          <select name="porto" required>
            {% for p in portos %}<option>{{ p }}</option>{% endfor %}
          </select>
          <button class="btn btn-ok" type="submit">Calcular valor</button>
        </form>
      </div>
      <div class="card">
        <h3>Modelo de cálculo</h3>
        <p class="muted">Preço exportação = CBOT convertido para R$/ton + prêmio de porto.</p>
        <p class="muted">Líquido = preço exportação - frete médio.</p>
      </div>
    </div>

    {% if resultado %}
    <div class="card">
      <h3>Resultado</h3>
      <div class="grid3">
        <div><div class="muted">CBOT</div><div class="kpi">{{ resultado.cbot_usd_bushel }} USD/bushel</div></div>
        <div><div class="muted">USD/BRL</div><div class="kpi">{{ resultado.usd_brl }}</div></div>
        <div><div class="muted">Exportação</div><div class="kpi">R$ {{ resultado.export_rs_ton }}/ton</div></div>
      </div>
      <div class="grid3">
        <div><div class="muted">Frete</div><div class="kpi">R$ {{ resultado.frete_rs_ton }}/ton</div></div>
        <div><div class="muted">Líquido</div><div class="kpi">R$ {{ resultado.liquido_rs_ton }}/ton</div></div>
        <div><div class="muted">Total</div><div class="kpi">R$ {{ resultado.total_rs }}</div></div>
      </div>
    </div>
    {% endif %}

    <div class="card">
      <h3>Histórico</h3>
      <a class="btn btn-ghost" href="{{ url_for('export_agricultura_csv') }}">Exportar CSV</a>
      <table>
        <tr><th>Data</th><th>Produto</th><th>Origem</th><th>Porto</th><th>Líquido R$/ton</th><th>Total R$</th></tr>
        {% for h in hist %}
          <tr>
            <td>{{ h.created_at.strftime("%d/%m %H:%M") }}</td>
            <td>{{ h.produto }}</td>
            <td>{{ h.origem }}</td>
            <td>{{ h.porto }}</td>
            <td>{{ h.liquido_rs_ton }}</td>
            <td>{{ h.total_rs }}</td>
          </tr>
        {% endfor %}
      </table>
    </div>
    """
    return page(html, title="AP360 | Agricultura", portos=PORTOS, resultado=resultado, hist=hist)


@app.route("/agricultura/export.csv")
@login_required
def export_agricultura_csv():
    rows = AgricultureQuote.query.filter_by(user_id=current_user.id).order_by(AgricultureQuote.created_at.desc()).all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["data", "produto", "origem", "porto", "cbot", "usd_brl", "export_rs_ton", "frete_rs_ton", "liquido_rs_ton", "total_rs"])
    for r in rows:
        writer.writerow([
            r.created_at.isoformat(), r.produto, r.origem, r.porto,
            r.cbot_usd_bushel, r.usd_brl, r.export_rs_ton, r.frete_rs_ton,
            r.liquido_rs_ton, r.total_rs
        ])
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=agricultura.csv"}
    )


# =========================================================
# AVICULTURA / SUINOCULTURA
# =========================================================
def batch_module(cadeia: str):
    resultado = None
    compare_data = None

    if request.method == "POST":
        estrutura = request.form.get("estrutura", "")
        lote = request.form.get("lote", "")
        peso_i = float(request.form.get("peso_inicial", 0))
        peso_f = float(request.form.get("peso_final", 0))
        dias = int(request.form.get("dias", 0))
        racao_total = float(request.form.get("racao_total_kg", 0))

        media_gpd, media_ca, bonus_base = get_coop_benchmark(cadeia, current_user.cooperativa)
        gpd = calc_gpd(peso_i, peso_f, dias)
        ca = calc_ca(racao_total, peso_i, peso_f)
        bonus = calc_bonus(gpd, ca, media_gpd, media_ca, bonus_base)

        row = Batch(
            user_id=current_user.id, cadeia=cadeia, estrutura=estrutura, lote=lote,
            peso_inicial=peso_i, peso_final=peso_f, dias=dias, racao_total_kg=racao_total,
            gpd=gpd, ca=ca, bonificacao=bonus,
            coop_media_gpd=media_gpd, coop_media_ca=media_ca
        )
        db.session.add(row)
        db.session.commit()
        resultado = row

    batches = Batch.query.filter_by(user_id=current_user.id, cadeia=cadeia).order_by(Batch.created_at.desc()).all()

    c1 = request.args.get("c1")
    c2 = request.args.get("c2")
    if c1 and c2:
        b1 = Batch.query.filter_by(id=int(c1), user_id=current_user.id, cadeia=cadeia).first()
        b2 = Batch.query.filter_by(id=int(c2), user_id=current_user.id, cadeia=cadeia).first()
        if b1 and b2:
            compare_data = {
                "labels": ["GPD", "CA", "Bônus (R$)"],
                "a_name": f"Estr {b1.estrutura} / Lote {b1.lote}",
                "b_name": f"Estr {b2.estrutura} / Lote {b2.lote}",
                "a_vals": [b1.gpd, b1.ca, b1.bonificacao],
                "b_vals": [b2.gpd, b2.ca, b2.bonificacao]
            }

    html = """
    <h2>{{ cadeia|capitalize }}</h2>
    <div class="grid">
      <div class="card">
        <h3>Novo lote</h3>
        <form method="post">
          <input name="estrutura" placeholder="Número da estrutura" required>
          <input name="lote" placeholder="Número do lote" required>
          <input type="number" step="0.0001" name="peso_inicial" placeholder="Peso inicial (kg)" required>
          <input type="number" step="0.0001" name="peso_final" placeholder="Peso final (kg)" required>
          <input type="number" name="dias" placeholder="Dias de alojamento" required>
          <input type="number" step="0.0001" name="racao_total_kg" placeholder="Ração total (kg)" required>
          <button class="btn btn-ok" type="submit">Calcular e salvar</button>
        </form>
      </div>
      <div class="card">
        <h3>Médias da cooperativa</h3>
        <p class="muted">Cooperativa: <b>{{ current_user.cooperativa or 'N/A' }}</b></p>
        <p>GPD referência: <b>{{ batches[0].coop_media_gpd if batches else '-' }}</b></p>
        <p>CA referência: <b>{{ batches[0].coop_media_ca if batches else '-' }}</b></p>
      </div>
    </div>

    {% if resultado %}
      <div class="card">
        <h3>Resultado do lote</h3>
        <div class="grid3">
          <div><div class="muted">GPD</div><div class="kpi">{{ resultado.gpd }}</div></div>
          <div><div class="muted">Conversão alimentar</div><div class="kpi">{{ resultado.ca }}</div></div>
          <div><div class="muted">Bonificação</div><div class="kpi">R$ {{ resultado.bonificacao }}</div></div>
        </div>
      </div>
    {% endif %}

    <div class="card">
      <h3>Comparar duas estruturas/lotes</h3>
      <form method="get" class="grid">
        <div>
          <label>Lote A</label>
          <select name="c1" required>
            {% for b in batches %}
              <option value="{{ b.id }}">{{ b.estrutura }} / {{ b.lote }} ({{ b.created_at.strftime("%d/%m") }})</option>
            {% endfor %}
          </select>
        </div>
        <div>
          <label>Lote B</label>
          <select name="c2" required>
            {% for b in batches %}
              <option value="{{ b.id }}">{{ b.estrutura }} / {{ b.lote }} ({{ b.created_at.strftime("%d/%m") }})</option>
            {% endfor %}
          </select>
        </div>
        <button class="btn btn-pri" type="submit">Comparar</button>
      </form>

      {% if compare_data %}
      <canvas id="cmpChart" height="95"></canvas>
      <script>
      const cmp = {{ compare_data | tojson }};
      new Chart(document.getElementById('cmpChart'), {
        type: 'bar',
        data: {
          labels: cmp.labels,
          datasets: [
            { label: cmp.a_name, data: cmp.a_vals },
            { label: cmp.b_name, data: cmp.b_vals }
          ]
        },
        options: { responsive:true }
      });
      </script>
      {% endif %}
    </div>

    <div class="card">
      <h3>Histórico</h3>
      <table>
        <tr><th>Data</th><th>Estrutura</th><th>Lote</th><th>GPD</th><th>CA</th><th>Bônus</th><th>Coop GPD</th><th>Coop CA</th></tr>
        {% for b in batches %}
          <tr>
            <td>{{ b.created_at.strftime("%d/%m %H:%M") }}</td>
            <td>{{ b.estrutura }}</td>
            <td>{{ b.lote }}</td>
            <td>{{ b.gpd }}</td>
            <td>{{ b.ca }}</td>
            <td>R$ {{ b.bonificacao }}</td>
            <td>{{ b.coop_media_gpd }}</td>
            <td>{{ b.coop_media_ca }}</td>
          </tr>
        {% endfor %}
      </table>
    </div>
    """
    return page(
        html, title=f"AP360 | {cadeia.capitalize()}",
        cadeia=cadeia, resultado=resultado, batches=batches, compare_data=compare_data
    )


@app.route("/avicultura", methods=["GET", "POST"])
@login_required
def avicultura():
    return batch_module("avicultura")


@app.route("/suinocultura", methods=["GET", "POST"])
@login_required
def suinocultura():
    return batch_module("suinocultura")


# =========================================================
# BOVINOCULTURA
# =========================================================
@app.route("/bovinocultura", methods=["GET", "POST"])
@login_required
def bovinocultura():
    if request.method == "POST":
        form_type = request.form.get("form_type")

        if form_type == "novo_bovino":
            brinco = request.form.get("brinco", "").strip()
            if Bovino.query.filter_by(brinco=brinco).first():
                flash("Brinco já existe.")
                return redirect(url_for("bovinocultura"))

            b = Bovino(
                user_id=current_user.id,
                brinco=brinco,
                nome=request.form.get("nome", "").strip(),
                sexo=request.form.get("sexo", "").strip(),
                raca=request.form.get("raca", "").strip(),
                nascimento=request.form.get("nascimento", "").strip(),
                origem=request.form.get("origem", "").strip(),
                lote=request.form.get("lote", "").strip(),
                status=request.form.get("status", "ativo"),
                peso_atual=float(request.form.get("peso_atual", 0) or 0),
                ultima_pesagem=request.form.get("ultima_pesagem", "").strip(),
                observacoes=request.form.get("observacoes", "").strip()
            )
            db.session.add(b)
            db.session.commit()
            flash("Bovino cadastrado.")
            return redirect(url_for("bovinocultura"))

        if form_type == "novo_peso":
            bovino_id = int(request.form.get("bovino_id"))
            data = request.form.get("data", "")
            peso = float(request.form.get("peso", 0))
            bov = Bovino.query.filter_by(id=bovino_id, user_id=current_user.id).first_or_404()
            db.session.add(BovinoPeso(bovino_id=bov.id, data=data, peso=peso))
            bov.peso_atual = peso
            bov.ultima_pesagem = data
            db.session.commit()
            flash("Pesagem registrada.")
            return redirect(url_for("bovinocultura", animal=bov.id))

        if form_type == "novo_evento":
            bovino_id = int(request.form.get("bovino_id"))
            bov = Bovino.query.filter_by(id=bovino_id, user_id=current_user.id).first_or_404()
            db.session.add(BovinoEvento(
                bovino_id=bov.id,
                tipo=request.form.get("tipo", "manejo"),
                descricao=request.form.get("descricao", ""),
                data=request.form.get("data", "")
            ))
            db.session.commit()
            flash("Evento sanitário/manejo registrado.")
            return redirect(url_for("bovinocultura", animal=bov.id))

    animais = Bovino.query.filter_by(user_id=current_user.id).order_by(Bovino.created_at.desc()).all()
    selected_id = request.args.get("animal", type=int)
    animal = None
    pesos = []
    eventos = []
    chart = None

    if selected_id:
        animal = Bovino.query.filter_by(id=selected_id, user_id=current_user.id).first()
        if animal:
            pesos = BovinoPeso.query.filter_by(bovino_id=animal.id).order_by(BovinoPeso.data.asc()).all()
            eventos = BovinoEvento.query.filter_by(bovino_id=animal.id).order_by(BovinoEvento.data.desc()).all()
            chart = {
                "labels": [p.data for p in pesos],
                "vals": [p.peso for p in pesos]
            }

    html = """
    <h2>Bovinocultura</h2>

    <div class="grid">
      <div class="card">
        <h3>Novo animal</h3>
        <form method="post">
          <input type="hidden" name="form_type" value="novo_bovino">
          <input name="brinco" placeholder="Brinco (único)" required>
          <input name="nome" placeholder="Nome">
          <select name="sexo"><option value="M">M</option><option value="F">F</option></select>
          <input name="raca" placeholder="Raça">
          <label>Nascimento</label><input type="date" name="nascimento">
          <input name="origem" placeholder="Origem">
          <input name="lote" placeholder="Lote">
          <select name="status"><option>ativo</option><option>vendido</option><option>descartado</option></select>
          <input type="number" step="0.01" name="peso_atual" placeholder="Peso atual (kg)">
          <label>Última pesagem</label><input type="date" name="ultima_pesagem">
          <textarea name="observacoes" placeholder="Observações gerais"></textarea>
          <button class="btn btn-ok" type="submit">Salvar animal</button>
        </form>
      </div>

      <div class="card">
        <h3>Animais cadastrados</h3>
        <table>
          <tr><th>Brinco</th><th>Nome</th><th>Peso</th><th>Status</th><th>Ações</th></tr>
          {% for a in animais %}
            <tr>
              <td>{{ a.brinco }}</td>
              <td>{{ a.nome or '-' }}</td>
              <td>{{ a.peso_atual }}</td>
              <td>{{ a.status }}</td>
              <td><a class="btn btn-ghost" href="{{ url_for('bovinocultura', animal=a.id) }}">Abrir ficha</a></td>
            </tr>
          {% endfor %}
        </table>
      </div>
    </div>

    {% if animal %}
    <div class="card">
      <h3>Ficha: {{ animal.brinco }} - {{ animal.nome or '-' }}</h3>
      <p class="muted">Raça: {{ animal.raca or '-' }} | Sexo: {{ animal.sexo or '-' }} | Nascimento: {{ animal.nascimento or '-' }}</p>
      <p class="muted">Peso atual: <b>{{ animal.peso_atual }} kg</b> | Última pesagem: <b>{{ animal.ultima_pesagem or '-' }}</b></p>
    </div>

    <div class="grid">
      <div class="card">
        <h3>Registrar pesagem</h3>
        <form method="post">
          <input type="hidden" name="form_type" value="novo_peso">
          <input type="hidden" name="bovino_id" value="{{ animal.id }}">
          <label>Data</label><input type="date" name="data" required>
          <input type="number" step="0.01" name="peso" placeholder="Peso (kg)" required>
          <button class="btn btn-pri" type="submit">Salvar pesagem</button>
        </form>

        {% if chart and chart.labels %}
          <canvas id="pesoChart" height="90"></canvas>
          <script>
            const pData = {{ chart | tojson }};
            new Chart(document.getElementById('pesoChart'), {
              type: 'line',
              data: { labels: pData.labels, datasets: [{ label:'Peso (kg)', data: pData.vals, tension:0.2 }] },
              options: { responsive:true }
            });
          </script>
        {% endif %}
      </div>

      <div class="card">
        <h3>Registrar evento (vacina/manejo)</h3>
        <form method="post">
          <input type="hidden" name="form_type" value="novo_evento">
          <input type="hidden" name="bovino_id" value="{{ animal.id }}">
          <select name="tipo">
            <option>vacina</option>
            <option>vermifugo</option>
            <option>manejo</option>
            <option>inseminacao</option>
            <option>parto</option>
            <option>tratamento</option>
          </select>
          <label>Data</label><input type="date" name="data" required>
          <textarea name="descricao" placeholder="Descrição do evento" required></textarea>
          <button class="btn btn-ok" type="submit">Salvar evento</button>
        </form>
      </div>
    </div>

    <div class="card">
      <h3>Histórico de pesagens</h3>
      <table>
        <tr><th>Data</th><th>Peso (kg)</th></tr>
        {% for p in pesos %}
          <tr><td>{{ p.data }}</td><td>{{ p.peso }}</td></tr>
        {% endfor %}
      </table>
    </div>

    <div class="card">
      <h3>Histórico de eventos</h3>
      <table>
        <tr><th>Data</th><th>Tipo</th><th>Descrição</th></tr>
        {% for e in eventos %}
          <tr><td>{{ e.data }}</td><td>{{ e.tipo }}</td><td>{{ e.descricao }}</td></tr>
        {% endfor %}
      </table>
    </div>
    {% endif %}
    """
    return page(
        html, title="AP360 | Bovinocultura",
        animais=animais, animal=animal, pesos=pesos, eventos=eventos, chart=chart
    )


# =========================================================
# IA (placeholder estilo "grok-like")
# =========================================================
def ai_reply_local(prompt: str) -> str:
    prompt = (prompt or "").strip()
    if not prompt:
        return "Envie uma pergunta."
    tips = [
        "Foque primeiro no custo por tonelada e margem líquida.",
        "Na pecuária, acompanhe tendência semanal de GPD e CA para agir cedo.",
        "Padronize coleta de dados por estrutura para comparação justa.",
    ]
    return f"AP360 IA: análise inicial -> {prompt[:180]}. Dica: {tips[len(prompt) % len(tips)]}"


@app.route("/ia")
@login_required
def ai_page():
    html = """
    <h2>Assistente IA</h2>
    <div class="card">
      <form id="aiForm">
        <textarea name="mensagem" id="mensagem" placeholder="Pergunte sobre manejo, indicadores, margem, estratégia..." required></textarea>
        <button class="btn btn-pri" type="submit">Enviar</button>
      </form>
      <div id="resp" class="card" style="display:none"></div>
    </div>
    <script>
      document.getElementById('aiForm').addEventListener('submit', async (e) => {
        e.preventDefault();
        const fd = new FormData(e.target);
        const res = await fetch("{{ url_for('ai_chat') }}", { method:"POST", body: fd });
        const data = await res.json();
        const box = document.getElementById('resp');
        box.style.display = 'block';
        box.innerText = data.resposta || "Sem resposta";
      });
    </script>
    """
    return page(html, title="AP360 | IA")


@app.route("/ia/chat", methods=["POST"])
@login_required
def ai_chat():
    msg = request.form.get("mensagem", "")
    xai_key = os.getenv("XAI_API_KEY", "").strip()

    if xai_key:
        # Endpoint pode variar por versão da API
        try:
            resp = requests.post(
                "https://api.x.ai/v1/chat/completions",
                headers={"Authorization": f"Bearer {xai_key}", "Content-Type": "application/json"},
                json={
                    "model": "grok-2-latest",
                    "messages": [
                        {"role": "system", "content": "Você é um assistente para produtores rurais do Brasil."},
                        {"role": "user", "content": msg}
                    ]
                },
                timeout=25
            )
            if resp.ok:
                data = resp.json()
                txt = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
                if txt:
                    return jsonify({"resposta": txt})
        except Exception:
            pass

    return jsonify({"resposta": ai_reply_local(msg)})


# =========================================================
# ERRORS
# =========================================================
@app.errorhandler(403)
def err_403(_):
    return page("<div class='card'><h2>403</h2><p>Acesso negado.</p></div>", title="403"), 403


@app.errorhandler(404)
def err_404(_):
    return page("<div class='card'><h2>404</h2><p>Página não encontrada.</p></div>", title="404"), 404


@app.errorhandler(500)
def err_500(_):
    return page("<div class='card'><h2>500</h2><p>Erro interno do servidor.</p></div>", title="500"), 500


# =========================================================
# RUN
# =========================================================
if __name__ == "__main__":
    app.run(debug=True)