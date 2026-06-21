import os
import io
import csv
import secrets
from datetime import datetime
from functools import wraps

from flask import (
    Flask, request, redirect, url_for, flash,
    render_template_string, jsonify, Response, abort
)
from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager, UserMixin, login_user, logout_user,
    login_required, current_user
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

app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "troque-essa-chave")
app.config["SQLALCHEMY_DATABASE_URI"] = normalize_database_url(
    os.getenv("DATABASE_URL", "sqlite:///ap360.db")
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "vitor26.nathank@gmail.com").strip().lower()
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "2021vitor")
ADMIN_NAME = os.getenv("ADMIN_NAME", "Vitor") # Adicionado para o nome do admin

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"


# =========================================================
# MODELS
# =========================================================
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(120), nullable=False, default="Usuário")
    cpf = db.Column(db.String(20), nullable=True)
    telefone = db.Column(db.String(30), nullable=True)
    email = db.Column(db.String(120), nullable=False, unique=True, index=True)
    password_hash = db.Column(db.String(255), nullable=False)

    perfil = db.Column(db.String(20), nullable=False, default="produtor")   # admin/produtor
    status = db.Column(db.String(20), nullable=False, default="ativo")       # ativo/bloqueado
    segmento = db.Column(db.String(20), nullable=True)                       # agricultura/pecuaria (expandido para avicultura/suinocultura/bovinocultura)
    cooperativa = db.Column(db.String(120), nullable=True)

    criado_em = db.Column(db.DateTime, default=datetime.utcnow)

    def set_password(self, raw: str):
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw: str):
        return check_password_hash(self.password_hash, raw)


class AccessRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(120), nullable=False)
    cpf = db.Column(db.String(20), nullable=True)
    telefone = db.Column(db.String(30), nullable=True)
    email = db.Column(db.String(120), nullable=False, index=True)
    segmento = db.Column(db.String(20), nullable=False, default="agricultura")
    cooperativa = db.Column(db.String(120), nullable=True)
    status = db.Column(db.String(20), default="pendente")  # pendente/liberado/negado
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)


class AccessInvite(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), nullable=False, unique=True, index=True)
    token = db.Column(db.String(120), nullable=False, unique=True, index=True)
    status = db.Column(db.String(20), default="convidado")  # convidado/ativado
    request_id = db.Column(db.Integer, db.ForeignKey("access_request.id"), nullable=True)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)
    ativado_em = db.Column(db.DateTime, nullable=True)


class CoopBenchmark(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    cadeia = db.Column(db.String(20), nullable=False)  # avicultura/suinocultura
    cooperativa = db.Column(db.String(120), nullable=False)
    media_gpd = db.Column(db.Float, default=0.0)
    media_ca = db.Column(db.Float, default=0.0)
    bonus_base = db.Column(db.Float, default=1000.0)
    atualizado_em = db.Column(db.DateTime, default=datetime.utcnow)


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

    criado_em = db.Column(db.DateTime, default=datetime.utcnow)


class Batch(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

    cadeia = db.Column(db.String(20), nullable=False)  # avicultura/suinocultura
    estrutura = db.Column(db.String(40), nullable=False)
    lote = db.Column(db.String(40), nullable=False)

    # Base produtiva
    peso_inicial = db.Column(db.Float, nullable=False)
    peso_final = db.Column(db.Float, nullable=False)
    dias = db.Column(db.Integer, nullable=False)
    racao_total_kg = db.Column(db.Float, nullable=False)

    # Plantel/lote
    animais_iniciais = db.Column(db.Integer, default=0)
    animais_final = db.Column(db.Integer, default=0)
    viabilidade_pct = db.Column(db.Float, default=0.0)
    mortalidade_pct = db.Column(db.Float, default=0.0)

    # Indicadores clássicos
    gpd = db.Column(db.Float, nullable=False)
    ca = db.Column(db.Float, nullable=False)
    ca_ajustada = db.Column(db.Float, default=0.0)

    # Referências cooperativa (manual)
    ca_coop_ref = db.Column(db.Float, default=0.0)
    gpd_coop_ref = db.Column(db.Float, default=0.0)

    # Parâmetros CAA (avicultura)
    peso_meta_coop = db.Column(db.Float, default=0.0)
    idade_meta_coop = db.Column(db.Integer, default=0)
    fator_peso_caa = db.Column(db.Float, default=0.30)
    fator_idade_caa = db.Column(db.Float, default=0.01)

    # Índices
    iep = db.Column(db.Float, default=0.0)
    indice_lote = db.Column(db.Float, default=0.0)

    # Carcaça (suínos)
    peso_vivo_medio = db.Column(db.Float, default=0.0)
    peso_carcaca_medio = db.Column(db.Float, default=0.0)
    rendimento_carcaca_pct = db.Column(db.Float, default=0.0)
    carne_magra_pct = db.Column(db.Float, default=0.0)
    bonus_tipificacao = db.Column(db.Float, default=0.0)

    # Bonificação total
    bonificacao = db.Column(db.Float, nullable=False)

    # Benchmarks efetivos usados
    coop_media_gpd = db.Column(db.Float, default=0.0)
    coop_media_ca = db.Column(db.Float, default=0.0)

    criado_em = db.Column(db.DateTime, default=datetime.utcnow)


class Bovino(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

    brinco = db.Column(db.String(40), nullable=False, unique=True)
    nome = db.Column(db.String(80), nullable=True)
    sexo = db.Column(db.String(10), nullable=True)
    raca = db.Column(db.String(60), nullable=True)
    nascimento = db.Column(db.String(10), nullable=True)

    origem = db.Column(db.String(120), nullable=True)
    lote = db.Column(db.String(60), nullable=True)
    status = db.Column(db.String(30), default="ativo")

    peso_atual = db.Column(db.Float, default=0.0)
    ultima_pesagem = db.Column(db.String(10), nullable=True)

    observacoes = db.Column(db.Text, default="")
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)


class BovinoPeso(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    bovino_id = db.Column(db.Integer, db.ForeignKey("bovino.id"), nullable=False)
    data = db.Column(db.String(10), nullable=False)
    peso = db.Column(db.Float, nullable=False)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)


class BovinoEvento(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    bovino_id = db.Column(db.Integer, db.ForeignKey("bovino.id"), nullable=False)
    tipo = db.Column(db.String(40), nullable=False)
    descricao = db.Column(db.Text, nullable=False)
    data = db.Column(db.String(10), nullable=False)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)


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
# HELPERS
# =========================================================
CBOT = {
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


def fx_usd_brl():
    return 5.35


def cbot_para_rs_ton(produto: str):
    p = (produto or "").lower()
    usd_bushel = CBOT.get(p, 0.0)
    kg = BUSHEL_KG.get(p, 27.2155)
    usd_ton = usd_bushel * (1000.0 / kg)
    rs_ton = usd_ton * fx_usd_brl()
    return round(rs_ton, 2), usd_bushel


def extrai_uf(origem: str):
    partes = (origem or "").upper().split("-")
    return partes[-1].strip() if len(partes) > 1 else "PR"


def frete_medio(origem: str, porto: str):
    uf = extrai_uf(origem)
    return float(FRETE_MEDIO.get(uf, {}).get(porto, 250.0))


def calc_gpd(p0, p1, dias):
    if dias <= 0:
        return 0.0
    return round((p1 - p0) / dias, 4)


def calc_ca(racao_kg, p0, p1):
    ganho = p1 - p0
    if ganho <= 0:
        return 0.0
    return round(racao_kg / ganho, 4)


def calc_viabilidade(animais_iniciais: int, animais_final: int) -> float:
    if animais_iniciais <= 0:
        return 0.0
    return round((animais_final / animais_iniciais) * 100.0, 2)


def calc_mortalidade(animais_iniciais: int, animais_final: int) -> float:
    if animais_iniciais <= 0:
        return 0.0
    mortos = max(0, animais_iniciais - animais_final)
    return round((mortos / animais_iniciais) * 100.0, 2)


def calc_ca_ajustada_avicultura(ca_observada: float, peso_real: float, idade_real: int,
                                peso_meta: float, idade_meta: int,
                                fator_peso: float = 0.30, fator_idade: float = 0.01) -> float:
    caa = ca_observada + (fator_peso * (peso_meta - peso_real)) + (fator_idade * (idade_real - idade_meta))
    return round(max(caa, 0.01), 4)


def calc_iep_avicultura(viabilidade_pct: float, peso_medio: float, idade_dias: int, ca_ajustada: float) -> float:
    if idade_dias <= 0 or ca_ajustada <= 0:
        return 0.0
    return round(((viabilidade_pct * peso_medio) / (idade_dias * ca_ajustada)) * 100.0, 2)


def calc_rendimento_carcaca(peso_vivo_medio: float, peso_carcaca_medio: float) -> float:
    if peso_vivo_medio <= 0:
        return 0.0
    return round((peso_carcaca_medio / peso_vivo_medio) * 100.0, 2)


def calc_indice_lote_suino(gpd: float, viabilidade_pct: float, ca_ajustada: float) -> float:
    if ca_ajustada <= 0:
        return 0.0
    return round(((gpd * 1000.0) * (viabilidade_pct / 100.0)) / ca_ajustada, 2)


def calc_bonus_tipificacao(carne_magra_pct: float, rendimento_carcaca_pct: float, base_rs: float = 12.0) -> float:
    score = 0.0
    if carne_magra_pct >= 58:
        score += 0.6
    elif carne_magra_pct >= 56:
        score += 0.35
    elif carne_magra_pct >= 54:
        score += 0.15

    if rendimento_carcaca_pct >= 78:
        score += 0.4
    elif rendimento_carcaca_pct >= 76:
        score += 0.2

    return round(base_rs * score, 2)


def calc_bonificacao(gpd, ca, meta_gpd=0.065, meta_ca=1.70, base=1000.0):
    if ca <= 0:
        return 0.0
    score = (gpd / meta_gpd) * 50 + (meta_ca / ca) * 50
    bonus_pct = max(-0.20, min(0.35, (score - 100) / 100))
    return round(base * bonus_pct, 2)


def get_benchmark(cadeia, cooperativa):
    if not cooperativa:
        return 0.065, 1.70, 1000.0
    row = CoopBenchmark.query.filter_by(cadeia=cadeia, cooperativa=cooperativa).first()
    if row:
        return row.media_gpd or 0.065, row.media_ca or 1.70, row.bonus_base or 1000.0
    return 0.065, 1.70, 1000.0


# =========================================================
# UI BASE
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
      --card:rgba(255,255,255,.10);
      --line:rgba(255,255,255,.20);
      --text:#f8fbff;
      --muted:#d4deef;
      --ok:#46dd98;
      --pri:#3bb9ff;
    }
    *{box-sizing:border-box}
    body{
      margin:0;
      font-family:Inter,Arial,sans-serif;
      color:var(--text);
      background:
        linear-gradient(120deg, rgba(59,185,255,.15), rgba(70,221,152,.12)),
        url('https://images.unsplash.com/photo-1500937386664-56d1dfef3854?q=80&w=1800&auto=format&fit=crop') center/cover fixed no-repeat;
      min-height:100vh;
    }
    .wrap{min-height:100vh;background:linear-gradient(180deg, rgba(8,14,30,.75), rgba(8,14,30,.90));padding:20px}
    .container{max-width:1200px;margin:0 auto}
    .nav{
      display:flex;justify-content:space-between;align-items:center;gap:10px;
      background:rgba(255,255,255,.07);border:1px solid var(--line);
      border-radius:14px;padding:12px 16px;backdrop-filter:blur(8px);margin-bottom:14px
    }
    .brand{font-weight:800}
    .brand b{color:var(--ok)}
    .links a{color:var(--text);text-decoration:none;margin-left:12px;font-size:.93rem}
    .hero{
      border:1px solid var(--line);border-radius:20px;padding:24px;
      background:linear-gradient(145deg, rgba(255,255,255,.13), rgba(255,255,255,.05))
    }
    .card{border:1px solid var(--line);border-radius:15px;padding:14px;background:var(--card);margin-top:12px}
    .grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}
    .grid3{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}
    @media (max-width:900px){.grid,.grid3{grid-template-columns:1fr}}
    .btn{display:inline-block;padding:10px 14px;border:none;border-radius:11px;font-weight:700;text-decoration:none;cursor:pointer}
    .btn-ok{background:linear-gradient(135deg,var(--ok),#38c984);color:#042516}
    .btn-pri{background:linear-gradient(135deg,var(--pri),#718aff);color:#041a30}
    .btn-ghost{background:rgba(255,255,255,.14);color:#fff}
    input,select,textarea{
      width:100%;padding:10px;border:1px solid var(--line);border-radius:10px;
      background:rgba(255,255,255,.08);color:#fff;margin:5px 0
    }
    input::placeholder,textarea::placeholder{color:#dbe7ff90}
    table{width:100%;border-collapse:collapse;font-size:.92rem}
    th,td{border:1px solid var(--line);padding:8px;text-align:left}
    .flash{padding:10px;background:rgba(255,255,255,.12);border:1px solid var(--line);border-radius:10px;margin-bottom:10px}
    .muted{color:var(--muted)}
    .kpi{font-size:1.25rem;font-weight:800}
    .welcome-name {
      color: var(--pri);
      font-weight: 800;
      text-transform: capitalize;
    }
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
          <a href="{{ url_for('ia_page') }}">IA</a>
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
    # Renderiza o conteúdo específico da página com o contexto fornecido
    processed_content = render_template_string(content, **ctx)
    # Renderiza o BASE_HTML, injetando o conteúdo processado
    return render_template_string(BASE_HTML, content=processed_content, **ctx)


# =========================================================
# INIT DB
# =========================================================
with app.app_context():
    db.create_all()

    adm = User.query.filter_by(email=ADMIN_EMAIL).first()
    if not adm:
        adm = User(
            nome=ADMIN_NAME, # Usa o nome do admin da variável de ambiente
            email=ADMIN_EMAIL,
            perfil="admin",
            status="ativo",
            segmento="agricultura",
            cooperativa="Coop Padrão"
        )
        adm.set_password(ADMIN_PASSWORD)
        db.session.add(adm)
        db.session.commit()
    elif adm.nome != ADMIN_NAME: # Atualiza o nome se o admin já existe mas o nome mudou
        adm.nome = ADMIN_NAME
        db.session.commit()


    if CoopBenchmark.query.count() == 0:
        db.session.add(CoopBenchmark(cadeia="avicultura", cooperativa="Coop Padrão", media_gpd=0.066, media_ca=1.68, bonus_base=1000))
        db.session.add(CoopBenchmark(cadeia="suinocultura", cooperativa="Coop Padrão", media_gpd=0.72, media_ca=2.45, bonus_base=1200))
        db.session.commit()


# =========================================================
# HOME / AUTH
# =========================================================
@app.route("/")
def index():
    html_content = """
    <section class="hero">
      <h1 style="margin:0;font-size:2.15rem">Gestão Agro completa em um único sistema</h1>
      <p class="muted">Agricultura, avicultura, suinocultura e bovinocultura com indicadores, comparativos e histórico.</p>
      <div style="margin-top:12px;display:flex;gap:10px;flex-wrap:wrap">
        <a class="btn btn-ok" href="{{ url_for('login') }}">Entrar</a>
        <a class="btn btn-pri" href="{{ url_for('signup_request') }}">Criar acesso</a>
      </div>
    </section>
    """
    return page(html_content, title="AP360 | Início")


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

    html_content = """
    <div class="card" style="max-width:520px;margin:20px auto">
      <h2 style="margin-top:0">Login</h2>
      <form method="post">
        <input type="email" name="email" placeholder="Seu e-mail" required>
        <input type="password" name="senha" placeholder="Sua senha" required>
        <button class="btn btn-ok" type="submit">Entrar</button>
      </form>
      <p class="muted">Não tem conta? <a href="{{ url_for('signup_request') }}">Inscreva-se</a></p>
    </div>
    """
    return page(html_content, title="AP360 | Login")


@app.route("/inscreva_se", methods=["GET", "POST"])
@app.route("/inscreva-se", methods=["GET", "POST"])
def signup_request():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()

        inv = AccessInvite.query.filter_by(email=email, status="convidado").first()
        if inv:
            flash("Este e-mail já possui um convite pendente. Por favor, ative sua conta.")
            return redirect(url_for("activate_account", token=inv.token))

        # Verifica se o e-mail já está em uso por um usuário ativo
        if User.query.filter_by(email=email).first():
            flash("Este e-mail já está cadastrado. Por favor, faça login.")
            return redirect(url_for("login"))

        req = AccessRequest(
            nome=request.form.get("nome", "").strip(),
            cpf=request.form.get("cpf", "").strip(),
            telefone=request.form.get("telefone", "").strip(),
            email=email,
            segmento=request.form.get("segmento", "agricultura"), # Pega o segmento escolhido
            cooperativa=request.form.get("cooperativa", "").strip(), # Cooperativa opcional
            status="pendente"
        )
        db.session.add(req)
        db.session.commit()

        flash("Solicitação de acesso recebida. Em breve você receberá um convite por e-mail para ativar sua conta.")
        flash("Para agilizar, entre em contato via WhatsApp: +55 45 99903-7929")
        return redirect(url_for("signup_request"))

    html_content = """
    <div class="card" style="max-width:700px;margin:0 auto">
      <h2 style="margin-top:0">Inscrição</h2>
      <form method="post">
        <input name="nome" placeholder="Nome completo" required>
        <input name="cpf" placeholder="CPF" required>
        <input name="telefone" placeholder="Telefone" required>
        <input name="email" type="email" placeholder="E-mail" required>
        <select name="segmento" required>
          <option value="agricultura">Agricultura</option>
          <option value="avicultura">Avicultura</option>
          <option value="suinocultura">Suinocultura</option>
          <option value="bovinocultura">Bovinocultura</option>
        </select>
        <input name="cooperativa" placeholder="Cooperativa (opcional)">
        <button class="btn btn-pri" type="submit">Enviar inscrição</button>
      </form>
      <p class="muted">
        Após o envio, aguarde a liberação do acesso. Para agilizar, entre em contato:
        <a target="_blank" href="https://wa.me/5545999037929">+55 45 99903-7929</a>
      </p>
    </div>
    """
    return page(html_content, title="AP360 | Inscrição")


@app.route("/ativar/<token>", methods=["GET", "POST"])
def activate_account(token):
    inv = AccessInvite.query.filter_by(token=token, status="convidado").first()
    if not inv:
        flash("Token inválido ou já utilizado para ativação.")
        return redirect(url_for("login"))

    if request.method == "POST":
        senha = request.form.get("senha", "")
        confirmar = request.form.get("confirmar_senha", "")

        if senha != confirmar:
            flash("Senha e confirmação não conferem.")
            return redirect(url_for("activate_account", token=token))
        if len(senha) < 6:
            flash("Senha deve ter no mínimo 6 caracteres.")
            return redirect(url_for("activate_account", token=token))
        if User.query.filter_by(email=inv.email).first():
            flash("Já existe uma conta ativa com este e-mail. Por favor, faça login.")
            return redirect(url_for("login"))

        req = AccessRequest.query.get(inv.request_id) if inv.request_id else None
        user = User(
            nome=req.nome if req and req.nome else "Produtor",
            cpf=req.cpf if req else None,
            telefone=req.telefone if req else None,
            email=inv.email,
            perfil="produtor",
            status="ativo",
            segmento=req.segmento if req and req.segmento else "agricultura", # Pega o segmento da solicitação
            cooperativa=req.cooperativa if req and req.cooperativa else None # Cooperativa da solicitação
        )
        user.set_password(senha)

        inv.status = "ativado"
        inv.ativado_em = datetime.utcnow()
        if req:
            req.status = "liberado"

        db.session.add(user)
        db.session.commit()

        flash("Conta ativada com sucesso. Faça login para começar!")
        return redirect(url_for("login"))

    html_content = """
    <div class="card" style="max-width:600px;margin:0 auto">
      <h2 style="margin-top:0">Ativar conta</h2>
      <p class="muted">E-mail liberado: <b>{{ inv.email }}</b></p>
      <form method="post">
        <input type="password" name="senha" placeholder="Crie sua senha" required>
        <input type="password" name="confirmar_senha" placeholder="Confirme sua senha" required>
        <button class="btn btn-ok" type="submit">Ativar</button>
      </form>
    </div>
    """
    return page(html_content, inv=inv, title="AP360 | Ativar conta")


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
    ag = AgricultureQuote.query.filter_by(user_id=current_user.id).count()
    av = Batch.query.filter_by(user_id=current_user.id, cadeia="avicultura").count()
    su = Batch.query.filter_by(user_id=current_user.id, cadeia="suinocultura").count()
    bo = Bovino.query.filter_by(user_id=current_user.id).count()

    html_content = """
    <section class="hero">
      <h1 style="margin:0">Bem-vindo, <span class="welcome-name">{{ current_user.nome }}</span></h1>
      <p class="muted">
        Segmento principal: <b>{{ current_user.segmento|capitalize or "Não definido" }}</b>
        {% if current_user.cooperativa %}
          | Cooperativa: <b>{{ current_user.cooperativa }}</b>
        {% else %}
          | Cooperativa: <b>Indefinida</b>
        {% endif %}
      </p>
    </section>

    <div class="grid3">
      <div class="card">
        <div class="muted">Cotações agrícolas</div>
        <div class="kpi">{{ ag }}</div>
        <a class="btn btn-ghost" href="{{ url_for('agricultura') }}">Acessar</a>
      </div>
      <div class="card">
        <div class="muted">Lotes avicultura</div>
        <div class="kpi">{{ av }}</div>
        <a class="btn btn-ghost" href="{{ url_for('avicultura') }}">Acessar</a>
      </div>
      <div class="card">
        <div class="muted">Lotes suinocultura</div>
        <div class="kpi">{{ su }}</div>
        <a class="btn btn-ghost" href="{{ url_for('suinocultura') }}">Acessar</a>
      </div>
    </div>
    <div class="card">
      <div class="muted">Bovinos cadastrados</div>
      <div class="kpi">{{ bo }}</div>
      <a class="btn btn-ghost" href="{{ url_for('bovinocultura') }}">Acessar</a>
    </div>
    """
    return page(html_content, title="AP360 | Dashboard", ag=ag, av=av, su=su, bo=bo)


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
                flash("Informe um e-mail válido.")
                return redirect(url_for("admin_panel"))

            if AccessInvite.query.filter_by(email=email).first():
                flash("Esse e-mail já possui convite.")
                return redirect(url_for("admin_panel"))

            token = secrets.token_urlsafe(24)
            db.session.add(AccessInvite(email=email, token=token, status="convidado"))
            db.session.commit()
            flash(f"E-mail liberado: {email}")
            flash(f"Link: {request.url_root.rstrip('/')}/ativar/{token}")
            return redirect(url_for("admin_panel"))

        if form_type == "approve_request":
            req_id = int(request.form.get("request_id"))
            req = AccessRequest.query.get_or_404(req_id)

            if AccessInvite.query.filter_by(email=req.email).first():
                flash("Esse e-mail já possui convite.")
                return redirect(url_for("admin_panel"))

            token = secrets.token_urlsafe(24)
            inv = AccessInvite(email=req.email, token=token, status="convidado", request_id=req.id)
            req.status = "liberado"
            db.session.add(inv)
            db.session.commit()
            flash(f"Convite gerado para {req.email}")
            flash(f"Link: {request.url_root.rstrip('/')}/ativar/{token}")
            return redirect(url_for("admin_panel"))

        if form_type == "benchmark":
            cadeia = request.form.get("cadeia", "")
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
            row.atualizado_em = datetime.utcnow()
            db.session.commit()
            flash("Benchmark salvo.")
            return redirect(url_for("admin_panel"))

    reqs = AccessRequest.query.order_by(AccessRequest.criado_em.desc()).limit(80).all()
    invites = AccessInvite.query.order_by(AccessInvite.criado_em.desc()).limit(80).all()
    users = User.query.filter(User.perfil != "admin").order_by(User.criado_em.desc()).limit(80).all() # Não mostra o admin aqui
    benches = CoopBenchmark.query.order_by(CoopBenchmark.atualizado_em.desc()).all()

    html_content = """
    <h2>Admin</h2>

    <div class="grid">
      <div class="card">
        <h3>Liberar e-mail manual</h3>
        <form method="post">
          <input type="hidden" name="form_type" value="manual_invite">
          <input type="email" name="email" placeholder="email@dominio.com" required>
          <button class="btn btn-ok" type="submit">Liberar</button>
        </form>
      </div>
      <div class="card">
        <h3>Benchmark cooperativa</h3>
        <form method="post">
          <input type="hidden" name="form_type" value="benchmark">
          <select name="cadeia" required>
            <option value="avicultura" {% if cadeia == 'avicultura' %}selected{% endif %}>Avicultura</option>
            <option value="suinocultura" {% if cadeia == 'suinocultura' %}selected{% endif %}>Suinocultura</option>
          </select>
          <input name="cooperativa" placeholder="Nome cooperativa" required>
          <input type="number" step="0.0001" name="media_gpd" placeholder="Média GPD" required>
          <input type="number" step="0.0001" name="media_ca" placeholder="Média CA" required>
          <input type="number" step="0.01" name="bonus_base" placeholder="Base bônus R$" required>
          <button class="btn btn-pri" type="submit">Salvar</button>
        </form>
      </div>
    </div>

    <div class="card">
      <h3>Solicitações</h3>
      <table>
        <tr><th>Data</th><th>Nome</th><th>Email</th><th>Segmento</th><th>Status</th><th>Ação</th></tr>
        {% for r in reqs %}
        <tr>
          <td>{{ r.criado_em.strftime("%d/%m %H:%M") }}</td>
          <td>{{ r.nome }}</td>
          <td>{{ r.email }}</td>
          <td>{{ r.segmento|capitalize }}</td>
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
        <tr><th>Email</th><th>Status</th><th>Token</th></tr>
        {% for i in invites %}
          <tr><td>{{ i.email }}</td><td>{{ i.status }}</td><td>{{ i.token }}</td></tr>
        {% endfor %}
      </table>
    </div>

    <div class="card">
      <h3>Usuários</h3>
      <table>
        <tr><th>Nome</th><th>Email</th><th>Perfil</th><th>Status</th><th>Segmento</th><th>Cooperativa</th></tr>
        {% for u in users %}
          <tr>
            <td>{{ u.nome }}</td>
            <td>{{ u.email }}</td>
            <td>{{ u.perfil }}</td>
            <td>{{ u.status }}</td>
            <td>{{ u.segmento|capitalize or '-' }}</td>
            <td>{{ u.cooperativa or 'Indefinida' }}</td>
          </tr>
        {% endfor %}
      </table>
    </div>

    <div class="card">
      <h3>Benchmarks</h3>
      <table>
        <tr><th>Cadeia</th><th>Cooperativa</th><th>GPD</th><th>CA</th><th>Base bônus</th></tr>
        {% for b in benches %}
          <tr><td>{{ b.cadeia|capitalize }}</td><td>{{ b.cooperativa }}</td><td>{{ b.media_gpd }}</td><td>{{ b.media_ca }}</td><td>{{ b.bonus_base }}</td></tr>
        {% endfor %}
      </table>
    </div>
    """
    return page(html_content, title="AP360 | Admin", reqs=reqs, invites=invites, users=users, benches=benches)


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

        preco_base_rs_ton, cbot_usd = cbot_para_rs_ton(produto)
        premio_porto = 35.0 if porto in ["Paranaguá", "Santos"] else 28.0
        preco_export = round(preco_base_rs_ton + premio_porto, 2)
        frete = frete_medio(origem, porto)
        liquido = round(preco_export - frete, 2)
        total = round(liquido * quantidade_ton, 2)

        q = AgricultureQuote(
            user_id=current_user.id,
            produto=produto,
            quantidade_ton=quantidade_ton,
            origem=origem,
            porto=porto,
            cbot_usd_bushel=cbot_usd,
            usd_brl=fx_usd_brl(),
            export_rs_ton=preco_export,
            frete_rs_ton=frete,
            liquido_rs_ton=liquido,
            total_rs=total
        )
        db.session.add(q)
        db.session.commit()
        resultado = q

    hist = AgricultureQuote.query.filter_by(user_id=current_user.id).order_by(AgricultureQuote.criado_em.desc()).limit(30).all()

    html_content = """
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
            {% for p in portos %}<option value="{{ p }}">{{ p }}</option>{% endfor %}
          </select>
          <button class="btn btn-ok" type="submit">Calcular</button>
        </form>
      </div>
      <div class="card">
        <h3>Modelo</h3>
        <p class="muted">Preço exportação = CBOT convertido + prêmio porto.</p>
        <p class="muted">Líquido = exportação - frete médio.</p>
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
        <tr>
          <th>Data</th><th>Produto</th><th>Origem</th><th>Porto</th><th>Líquido R$/ton</th><th>Total R$</th>
          <th>Ações</th>
        </tr>
        {% for h in hist %}
          <tr>
            <td>{{ h.criado_em.strftime("%d/%m %H:%M") }}</td>
            <td>{{ h.produto|capitalize }}</td>
            <td>{{ h.origem }}</td>
            <td>{{ h.porto }}</td>
            <td>{{ h.liquido_rs_ton }}</td>
            <td>{{ h.total_rs }}</td>
            <td>
              <a class="btn btn-ghost" href="{{ url_for('editar_agricultura', quote_id=h.id) }}">Editar</a>
              <form method="post" action="{{ url_for('excluir_agricultura', quote_id=h.id) }}" style="display:inline;">
                <button type="submit" class="btn btn-ghost" onclick="return confirm('Tem certeza que deseja excluir esta cotação?');">Excluir</button>
              </form>
            </td>
          </tr>
        {% endfor %}
      </table>
    </div>
    """
    return page(html_content, title="AP360 | Agricultura", portos=PORTOS, resultado=resultado, hist=hist)


@app.route("/agricultura/export.csv")
@login_required
def export_agricultura_csv():
    rows = AgricultureQuote.query.filter_by(user_id=current_user.id).order_by(AgricultureQuote.criado_em.desc()).all()
    output = io.StringIO()
    w = csv.writer(output)
    w.writerow(["data", "produto", "origem", "porto", "cbot", "usd_brl", "export_rs_ton", "frete_rs_ton", "liquido_rs_ton", "total_rs"])
    for r in rows:
        w.writerow([
            r.criado_em.isoformat(), r.produto, r.origem, r.porto, r.cbot_usd_bushel,
            r.usd_brl, r.export_rs_ton, r.frete_rs_ton, r.liquido_rs_ton, r.total_rs
        ])
    return Response(output.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=agricultura.csv"})


@app.route("/agricultura/editar/<int:quote_id>", methods=["GET", "POST"])
@login_required
def editar_agricultura(quote_id):
    quote = AgricultureQuote.query.filter_by(id=quote_id, user_id=current_user.id).first_or_404()

    if request.method == "POST":
        quote.produto = request.form.get("produto", "").lower()
        quote.quantidade_ton = float(request.form.get("quantidade_ton", 0))
        quote.origem = request.form.get("origem", "")
        quote.porto = request.form.get("porto", "")

        # Recalcula os valores
        preco_base_rs_ton, cbot_usd = cbot_para_rs_ton(quote.produto)
        premio_porto = 35.0 if quote.porto in ["Paranaguá", "Santos"] else 28.0
        quote.export_rs_ton = round(preco_base_rs_ton + premio_porto, 2)
        quote.frete_rs_ton = frete_medio(quote.origem, quote.porto)
        quote.liquido_rs_ton = round(quote.export_rs_ton - quote.frete_rs_ton, 2)
        quote.total_rs = round(quote.liquido_rs_ton * quote.quantidade_ton, 2)
        quote.cbot_usd_bushel = cbot_usd
        quote.usd_brl = fx_usd_brl()

        db.session.commit()
        flash("Cotação agrícola atualizada com sucesso!")
        return redirect(url_for("agricultura"))

    html_content = """
    <h2>Editar Cotação Agrícola</h2>
    <div class="card" style="max-width:700px;margin:0 auto">
      <form method="post">
        <label>Produto</label>
        <select name="produto" required>
          <option value="soja" {% if quote.produto == 'soja' %}selected{% endif %}>Soja</option>
          <option value="milho" {% if quote.produto == 'milho' %}selected{% endif %}>Milho</option>
          <option value="trigo" {% if quote.produto == 'trigo' %}selected{% endif %}>Trigo</option>
          <option value="aveia" {% if quote.produto == 'aveia' %}selected{% endif %}>Aveia</option>
          <option value="arroz" {% if quote.produto == 'arroz' %}selected{% endif %}>Arroz</option>
        </select>
        <label>Quantidade (ton)</label>
        <input type="number" step="0.01" name="quantidade_ton" value="{{ quote.quantidade_ton }}" required>
        <label>Origem (ex.: Cascavel-PR)</label>
        <input name="origem" value="{{ quote.origem }}" required>
        <label>Porto</label>
        <select name="porto" required>
          {% for p in portos %}
            <option value="{{ p }}" {% if quote.porto == p %}selected{% endif %}>{{ p }}</option>
          {% endfor %}
        </select>
        <button class="btn btn-ok" type="submit">Salvar Alterações</button>
        <a class="btn btn-ghost" href="{{ url_for('agricultura') }}">Cancelar</a>
      </form>
    </div>
    """
    return page(html_content, title="AP360 | Editar Cotação", quote=quote, portos=PORTOS)


@app.route("/agricultura/excluir/<int:quote_id>", methods=["POST"])
@login_required
def excluir_agricultura(quote_id):
    quote = AgricultureQuote.query.filter_by(id=quote_id, user_id=current_user.id).first_or_404()
    db.session.delete(quote)
    db.session.commit()
    flash("Cotação agrícola excluída com sucesso!")
    return redirect(url_for("agricultura"))


# =========================================================
# AVICULTURA / SUINOCULTURA
# =========================================================
def modulo_lotes(cadeia):
    resultado = None
    compare_data = None

    if request.method == "POST":
        estrutura = request.form.get("estrutura", "").strip()
        lote = request.form.get("lote", "").strip()

        peso_i = float(request.form.get("peso_inicial", 0))
        peso_f = float(request.form.get("peso_final", 0))
        dias = int(request.form.get("dias", 0))
        racao = float(request.form.get("racao_total_kg", 0))

        animais_iniciais = int(request.form.get("animais_iniciais", 0) or 0)
        animais_final = int(request.form.get("animais_final", 0) or 0)

        # benchmark base do admin
        meta_gpd_db, meta_ca_db, bonus_base = get_benchmark(cadeia, current_user.cooperativa)

        # referência manual informada pelo produtor (opcional)
        ca_coop_ref = float(request.form.get("ca_coop_ref", 0) or 0)
        gpd_coop_ref = float(request.form.get("gpd_coop_ref", 0) or 0)

        meta_ca = ca_coop_ref if ca_coop_ref > 0 else meta_ca_db
        meta_gpd = gpd_coop_ref if gpd_coop_ref > 0 else meta_gpd_db

        gpd = calc_gpd(peso_i, peso_f, dias)
        ca = calc_ca(racao, peso_i, peso_f)

        viabilidade = calc_viabilidade(animais_iniciais, animais_final)
        mortalidade = calc_mortalidade(animais_iniciais, animais_final)

        # defaults
        ca_ajustada = ca
        iep = 0.0
        indice_lote = 0.0
        peso_vivo_medio = 0.0
        peso_carcaca_medio = 0.0
        rendimento_carcaca_pct = 0.0
        carne_magra_pct = 0.0
        bonus_tipificacao = 0.0

        # avicultura: CAA + IEP
        peso_meta = float(request.form.get("peso_meta_coop", 0) or 0)
        idade_meta = int(request.form.get("idade_meta_coop", 0) or 0)
        fator_peso = float(request.form.get("fator_peso_caa", 0.30) or 0.30)
        fator_idade = float(request.form.get("fator_idade_caa", 0.01) or 0.01)

        if cadeia == "avicultura":
            if peso_meta > 0 and idade_meta > 0:
                ca_ajustada = calc_ca_ajustada_avicultura(
                    ca_observada=ca,
                    peso_real=peso_f,
                    idade_real=dias,
                    peso_meta=peso_meta,
                    idade_meta=idade_meta,
                    fator_peso=fator_peso,
                    fator_idade=fator_idade
                )
            iep = calc_iep_avicultura(viabilidade, peso_f, dias, ca_ajustada)

        # suínos: carcaça, tipificação, índice
        if cadeia == "suinocultura":
            peso_vivo_medio = float(request.form.get("peso_vivo_medio", 0) or 0)
            peso_carcaca_medio = float(request.form.get("peso_carcaca_medio", 0) or 0)
            carne_magra_pct = float(request.form.get("carne_magra_pct", 0) or 0)

            rendimento_carcaca_pct = calc_rendimento_carcaca(peso_vivo_medio, peso_carcaca_medio)

            # ajuste leve CA suína por peso de abate (referência 120kg)
            if peso_vivo_medio > 0:
                ajuste_peso = 0.003 * (peso_vivo_medio - 120.0)
                ca_ajustada = round(max(ca + ajuste_peso, 0.01), 4)

            indice_lote = calc_indice_lote_suino(gpd, viabilidade, ca_ajustada)
            bonus_tipificacao = calc_bonus_tipificacao(carne_magra_pct, rendimento_carcaca_pct)

        bon = calc_bonificacao(gpd, ca_ajustada, meta_gpd, meta_ca, bonus_base)
        bon_total = round(bon + bonus_tipificacao, 2)

        b = Batch(
            user_id=current_user.id,
            cadeia=cadeia,
            estrutura=estrutura,
            lote=lote,
            peso_inicial=peso_i,
            peso_final=peso_f,
            dias=dias,
            racao_total_kg=racao,
            animais_iniciais=animais_iniciais,
            animais_final=animais_final,
            viabilidade_pct=viabilidade,
            mortalidade_pct=mortalidade,
            gpd=gpd,
            ca=ca,
            ca_ajustada=ca_ajustada,
            ca_coop_ref=ca_coop_ref,
            gpd_coop_ref=gpd_coop_ref,
            peso_meta_coop=peso_meta,
            idade_meta_coop=idade_meta,
            fator_peso_caa=fator_peso,
            fator_idade_caa=fator_idade,
            iep=iep,
            indice_lote=indice_lote,
            peso_vivo_medio=peso_vivo_medio,
            peso_carcaca_medio=peso_carcaca_medio,
            rendimento_carcaca_pct=rendimento_carcaca_pct,
            carne_magra_pct=carne_magra_pct,
            bonus_tipificacao=bonus_tipificacao,
            bonificacao=bon_total,
            coop_media_gpd=meta_gpd,
            coop_media_ca=meta_ca
        )
        db.session.add(b)
        db.session.commit()
        resultado = b

    hist = Batch.query.filter_by(user_id=current_user.id, cadeia=cadeia).order_by(Batch.criado_em.desc()).all()

    c1 = request.args.get("c1", type=int)
    c2 = request.args.get("c2", type=int)
    if c1 and c2:
        b1 = Batch.query.filter_by(id=c1, user_id=current_user.id, cadeia=cadeia).first()
        b2 = Batch.query.filter_by(id=c2, user_id=current_user.id, cadeia=cadeia).first()
        if b1 and b2:
            compare_data = {
                "labels": ["GPD", "CA", "CA Ajustada", "Bonificação"],
                "a_name": f"Estr {b1.estrutura}/Lote {b1.lote}",
                "b_name": f"Estr {b2.estrutura}/Lote {b2.lote}",
                "a_vals": [b1.gpd, b1.ca, b1.ca_ajustada, b1.bonificacao],
                "b_vals": [b2.gpd, b2.ca, b2.ca_ajustada, b2.bonificacao]
            }

    html_content = """
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
          <input type="number" name="animais_iniciais" placeholder="Animais iniciais" required>
          <input type="number" name="animais_final" placeholder="Animais finais (abatidos/vendidos)" required>

          <h4>Referência cooperativa (informada pelo produtor)</h4>
          <input type="number" step="0.0001" name="gpd_coop_ref" placeholder="GPD médio coop (opcional)">
          <input type="number" step="0.0001" name="ca_coop_ref" placeholder="CA média coop (opcional)">

          {% if cadeia == 'avicultura' %}
            <h4>CA Ajustada (Avicultura)</h4>
            <input type="number" step="0.0001" name="peso_meta_coop" placeholder="Peso meta coop (kg), ex: 2.90" required>
            <input type="number" name="idade_meta_coop" placeholder="Idade meta coop (dias), ex: 42" required>
            <input type="number" step="0.0001" name="fator_peso_caa" value="0.30" placeholder="Fator peso CAA">
            <input type="number" step="0.0001" name="fator_idade_caa" value="0.01" placeholder="Fator idade CAA">
          {% endif %}

          {% if cadeia == 'suinocultura' %}
            <h4>Carcaça e tipificação (Suínos)</h4>
            <input type="number" step="0.01" name="peso_vivo_medio" placeholder="Peso vivo médio (kg/cab)" required>
            <input type="number" step="0.01" name="peso_carcaca_medio" placeholder="Peso carcaça médio (kg/cab)" required>
            <input type="number" step="0.01" name="carne_magra_pct" placeholder="% carne magra (ex: 57.5)" required>
          {% endif %}

          <button class="btn btn-ok" type="submit">Calcular e salvar</button>
        </form>
      </div>

      <div class="card">
        <h3>Métricas calculadas</h3>
        <p class="muted">GPD, CA, CAA, viabilidade, mortalidade e bônus.</p>
        <p class="muted">Avicultura: IEP/EPEF.</p>
        <p class="muted">Suínos: rendimento de carcaça, índice de lote e bônus de tipificação.</p>
      </div>
    </div>

    {% if resultado %}
      <div class="card">
        <h3>Resultado do lote</h3>
        <div class="grid3">
          <div><div class="muted">GPD</div><div class="kpi">{{ resultado.gpd }}</div></div>
          <div><div class="muted">CA</div><div class="kpi">{{ resultado.ca }}</div></div>
          <div><div class="muted">CA Ajustada</div><div class="kpi">{{ resultado.ca_ajustada }}</div></div>
        </div>
        <div class="grid3">
          <div><div class="muted">Viabilidade</div><div class="kpi">{{ resultado.viabilidade_pct }}%</div></div>
          <div><div class="muted">Mortalidade</div><div class="kpi">{{ resultado.mortalidade_pct }}%</div></div>
          <div><div class="muted">Bônus total</div><div class="kpi">R$ {{ resultado.bonificacao }}</div></div>
        </div>

        {% if cadeia == 'avicultura' %}
        <div class="grid3">
          <div><div class="muted">IEP/EPEF</div><div class="kpi">{{ resultado.iep }}</div></div>
          <div><div class="muted">Peso meta</div><div class="kpi">{{ resultado.peso_meta_coop }}</div></div>
          <div><div class="muted">Idade meta</div><div class="kpi">{{ resultado.idade_meta_coop }}</div></div>
        </div>
        {% endif %}

        {% if cadeia == 'suinocultura' %}
        <div class="grid3">
          <div><div class="muted">Rendimento carcaça</div><div class="kpi">{{ resultado.rendimento_carcaca_pct }}%</div></div>
          <div><div class="muted">% carne magra</div><div class="kpi">{{ resultado.carne_magra_pct }}%</div></div>
          <div><div class="muted">Bônus tipificação</div><div class="kpi">R$ {{ resultado.bonus_tipificacao }}</div></div>
        </div>
        <div class="grid3">
          <div><div class="muted">Índice lote</div><div class="kpi">{{ resultado.indice_lote }}</div></div>
          <div><div class="muted">Peso vivo médio</div><div class="kpi">{{ resultado.peso_vivo_medio }}</div></div>
          <div><div class="muted">Peso carcaça médio</div><div class="kpi">{{ resultado.peso_carcaca_medio }}</div></div>
        </div>
        {% endif %}
      </div>
    {% endif %}

    <div class="card">
      <h3>Comparar dois lotes</h3>
      <form method="get" class="grid">
        <div>
          <label>Lote A</label>
          <select name="c1" required>
            {% for h in hist %}
              <option value="{{ h.id }}" {% if c1 == h.id %}selected{% endif %}>{{ h.estrutura }} / {{ h.lote }} ({{ h.criado_em.strftime("%d/%m") }})</option>
            {% endfor %}
          </select>
        </div>
        <div>
          <label>Lote B</label>
          <select name="c2" required>
            {% for h in hist %}
              <option value="{{ h.id }}" {% if c2 == h.id %}selected{% endif %}>{{ h.estrutura }} / {{ h.lote }} ({{ h.criado_em.strftime("%d/%m") }})</option>
            {% endfor %}
          </select>
        </div>
        <button class="btn btn-pri" type="submit">Comparar</button>
      </form>

      {% if compare_data %}
      <canvas id="cmpChart" height="90"></canvas>
      <script>
        const cmp = {{ compare_data | tojson }};
        new Chart(document.getElementById("cmpChart"), {
          type: "bar",
          data: {
            labels: cmp.labels,
            datasets: [
              { label: cmp.a_name, data: cmp.a_vals },
              { label: cmp.b_name, data: cmp.b_vals }
            ]
          },
          options: { responsive: true }
        });
      </script>
      {% endif %}
    </div>

    <div class="card">
      <h3>Histórico</h3>
      <table>
        <tr>
          <th>Data</th><th>Estrutura</th><th>Lote</th><th>GPD</th><th>CA</th><th>CAA</th>
          <th>Viab%</th><th>Mort%</th><th>IEP/Índice</th><th>Rend. Carcaça%</th><th>Bônus</th>
          <th>Ações</th>
        </tr>
        {% for h in hist %}
        <tr>
          <td>{{ h.criado_em.strftime("%d/%m %H:%M") }}</td>
          <td>{{ h.estrutura }}</td>
          <td>{{ h.lote }}</td>
          <td>{{ h.gpd }}</td>
          <td>{{ h.ca }}</td>
          <td>{{ h.ca_ajustada }}</td>
          <td>{{ h.viabilidade_pct }}</td>
          <td>{{ h.mortalidade_pct }}</td>
          <td>{% if cadeia == 'avicultura' %}{{ h.iep }}{% else %}{{ h.indice_lote }}{% endif %}</td>
          <td>{{ h.rendimento_carcaca_pct }}</td>
          <td>R$ {{ h.bonificacao }}</td>
          <td>
            <a class="btn btn-ghost" href="{{ url_for('editar_lote', cadeia=cadeia, batch_id=h.id) }}">Editar</a>
            <form method="post" action="{{ url_for('excluir_lote', cadeia=cadeia, batch_id=h.id) }}" style="display:inline;">
              <button type="submit" class="btn btn-ghost" onclick="return confirm('Tem certeza que deseja excluir este lote?');">Excluir</button>
            </form>
          </td>
        </tr>
        {% endfor %}
      </table>
    </div>
    """
    return page(html_content, title=f"AP360 | {cadeia.capitalize()}",
                cadeia=cadeia, resultado=resultado, hist=hist, compare_data=compare_data, c1=c1, c2=c2)


@app.route("/avicultura", methods=["GET", "POST"])
@login_required
def avicultura():
    return modulo_lotes("avicultura")


@app.route("/suinocultura", methods=["GET", "POST"])
@login_required
def suinocultura():
    return modulo_lotes("suinocultura")


@app.route("/<string:cadeia>/editar/<int:batch_id>", methods=["GET", "POST"])
@login_required
def editar_lote(cadeia, batch_id):
    batch = Batch.query.filter_by(id=batch_id, user_id=current_user.id, cadeia=cadeia).first_or_404()

    if request.method == "POST":
        batch.estrutura = request.form.get("estrutura", "").strip()
        batch.lote = request.form.get("lote", "").strip()
        batch.peso_inicial = float(request.form.get("peso_inicial", 0))
        batch.peso_final = float(request.form.get("peso_final", 0))
        batch.dias = int(request.form.get("dias", 0))
        batch.racao_total_kg = float(request.form.get("racao_total_kg", 0))
        batch.animais_iniciais = int(request.form.get("animais_iniciais", 0) or 0)
        batch.animais_final = int(request.form.get("animais_final", 0) or 0)

        # Recalcula tudo
        batch.gpd = calc_gpd(batch.peso_inicial, batch.peso_final, batch.dias)
        batch.ca = calc_ca(batch.racao_total_kg, batch.peso_inicial, batch.peso_final)
        batch.viabilidade_pct = calc_viabilidade(batch.animais_iniciais, batch.animais_final)
        batch.mortalidade_pct = calc_mortalidade(batch.animais_iniciais, batch.animais_final)

        # Referência manual (cooperativa informada pelo produtor)
        batch.ca_coop_ref = float(request.form.get("ca_coop_ref", 0) or 0)
        batch.gpd_coop_ref = float(request.form.get("gpd_coop_ref", 0) or 0)

        meta_gpd_db, meta_ca_db, bonus_base = get_benchmark(cadeia, current_user.cooperativa)
        meta_ca = batch.ca_coop_ref if batch.ca_coop_ref > 0 else meta_ca_db
        meta_gpd = batch.gpd_coop_ref if batch.gpd_coop_ref > 0 else meta_gpd_db

        batch.ca_ajustada = batch.ca # Default
        batch.iep = 0.0
        batch.indice_lote = 0.0
        batch.peso_vivo_medio = 0.0
        batch.peso_carcaca_medio = 0.0
        batch.rendimento_carcaca_pct = 0.0
        batch.carne_magra_pct = 0.0
        batch.bonus_tipificacao = 0.0

        if cadeia == "avicultura":
            batch.peso_meta_coop = float(request.form.get("peso_meta_coop", 0) or 0)
            batch.idade_meta_coop = int(request.form.get("idade_meta_coop", 0) or 0)
            batch.fator_peso_caa = float(request.form.get("fator_peso_caa", 0.30) or 0.30)
            batch.fator_idade_caa = float(request.form.get("fator_idade_caa", 0.01) or 0.01)

            if batch.peso_meta_coop > 0 and batch.idade_meta_coop > 0:
                batch.ca_ajustada = calc_ca_ajustada_avicultura(
                    ca_observada=batch.ca,
                    peso_real=batch.peso_final,
                    idade_real=batch.dias,
                    peso_meta=batch.peso_meta_coop,
                    idade_meta=batch.idade_meta_coop,
                    fator_peso=batch.fator_peso_caa,
                    fator_idade=batch.fator_idade_caa
                )
            batch.iep = calc_iep_avicultura(batch.viabilidade_pct, batch.peso_final, batch.dias, batch.ca_ajustada)

        if cadeia == "suinocultura":
            batch.peso_vivo_medio = float(request.form.get("peso_vivo_medio", 0) or 0)
            batch.peso_carcaca_medio = float(request.form.get("peso_carcaca_medio", 0) or 0)
            batch.carne_magra_pct = float(request.form.get("carne_magra_pct", 0) or 0)

            batch.rendimento_carcaca_pct = calc_rendimento_carcaca(batch.peso_vivo_medio, batch.peso_carcaca_medio)

            if batch.peso_vivo_medio > 0:
                ajuste_peso = 0.003 * (batch.peso_vivo_medio - 120.0)
                batch.ca_ajustada = round(max(batch.ca + ajuste_peso, 0.01), 4)

            batch.indice_lote = calc_indice_lote_suino(batch.gpd, batch.viabilidade_pct, batch.ca_ajustada)
            batch.bonus_tipificacao = calc_bonus_tipificacao(batch.carne_magra_pct, batch.rendimento_carcaca_pct)

        bon = calc_bonificacao(batch.gpd, batch.ca_ajustada, meta_gpd, meta_ca, bonus_base)
        batch.bonificacao = round(bon + batch.bonus_tipificacao, 2)
        batch.coop_media_gpd = meta_gpd
        batch.coop_media_ca = meta_ca

        db.session.commit()
        flash(f"Lote de {cadeia} atualizado com sucesso!")
        return redirect(url_for(cadeia))

    html_content = """
    <h2>Editar Lote de {{ cadeia|capitalize }}</h2>
    <div class="card" style="max-width:700px;margin:0 auto">
      <form method="post">
        <label>Estrutura</label>
        <input name="estrutura" value="{{ batch.estrutura }}" required>
        <label>Lote</label>
        <input name="lote" value="{{ batch.lote }}" required>
        <label>Peso inicial (kg)</label>
        <input type="number" step="0.0001" name="peso_inicial" value="{{ batch.peso_inicial }}" required>
        <label>Peso final (kg)</label>
        <input type="number" step="0.0001" name="peso_final" value="{{ batch.peso_final }}" required>
        <label>Dias de alojamento</label>
        <input type="number" name="dias" value="{{ batch.dias }}" required>
        <label>Ração total (kg)</label>
        <input type="number" step="0.0001" name="racao_total_kg" value="{{ batch.racao_total_kg }}" required>
        <label>Animais iniciais</label>
        <input type="number" name="animais_iniciais" value="{{ batch.animais_iniciais }}" required>
        <label>Animais finais (abatidos/vendidos)</label>
        <input type="number" name="animais_final" value="{{ batch.animais_final }}" required>

        <h4>Referência cooperativa (informada pelo produtor)</h4>
        <label>GPD médio cooperativa (opcional)</label>
        <input type="number" step="0.0001" name="gpd_coop_ref" value="{{ batch.gpd_coop_ref }}">
        <label>CA média cooperativa (opcional)</label>
        <input type="number" step="0.0001" name="ca_coop_ref" value="{{ batch.ca_coop_ref }}">

        {% if cadeia == 'avicultura' %}
          <h4>CA Ajustada (Avicultura)</h4>
          <label>Peso meta coop (kg)</label>
          <input type="number" step="0.0001" name="peso_meta_coop" value="{{ batch.peso_meta_coop }}" required>
          <label>Idade meta coop (dias)</label>
          <input type="number" name="idade_meta_coop" value="{{ batch.idade_meta_coop }}" required>
          <label>Fator peso CAA</label>
          <input type="number" step="0.0001" name="fator_peso_caa" value="{{ batch.fator_peso_caa }}">
          <label>Fator idade CAA</label>
          <input type="number" step="0.0001" name="fator_idade_caa" value="{{ batch.fator_idade_caa }}">
        {% endif %}

        {% if cadeia == 'suinocultura' %}
          <h4>Carcaça e tipificação (Suínos)</h4>
          <label>Peso vivo médio (kg/cab)</label>
          <input type="number" step="0.01" name="peso_vivo_medio" value="{{ batch.peso_vivo_medio }}" required>
          <label>Peso carcaça médio (kg/cab)</label>
          <input type="number" step="0.01" name="peso_carcaca_medio" value="{{ batch.peso_carcaca_medio }}" required>
          <label>% carne magra</label>
          <input type="number" step="0.01" name="carne_magra_pct" value="{{ batch.carne_magra_pct }}" required>
        {% endif %}

        <button class="btn btn-ok" type="submit">Salvar Alterações</button>
        <a class="btn btn-ghost" href="{{ url_for(cadeia) }}">Cancelar</a>
      </form>
    </div>
    """
    return page(html_content, title=f"AP360 | Editar Lote {cadeia.capitalize()}", batch=batch, cadeia=cadeia)


@app.route("/<string:cadeia>/excluir/<int:batch_id>", methods=["POST"])
@login_required
def excluir_lote(cadeia, batch_id):
    batch = Batch.query.filter_by(id=batch_id, user_id=current_user.id, cadeia=cadeia).first_or_404()
    db.session.delete(batch)
    db.session.commit()
    flash(f"Lote de {cadeia} excluído com sucesso!")
    return redirect(url_for(cadeia))


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
            if Bovino.query.filter_by(brinco=brinco, user_id=current_user.id).first(): # Adicionado user_id para unicidade por usuário
                flash("Brinco já cadastrado para este usuário.")
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
            flash("Animal cadastrado.")
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
            flash("Evento registrado.")
            return redirect(url_for("bovinocultura", animal=bov.id))

    animais = Bovino.query.filter_by(user_id=current_user.id).order_by(Bovino.criado_em.desc()).all()
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
            chart = {"labels": [p.data for p in pesos], "vals": [p.peso for p in pesos]}

    html_content = """
    <h2>Bovinocultura</h2>

    <div class="grid">
      <div class="card">
        <h3>Novo animal</h3>
        <form method="post">
          <input type="hidden" name="form_type" value="novo_bovino">
          <input name="brinco" placeholder="Brinco (único)" required>
          <input name="nome" placeholder="Nome">
          <select name="sexo">
            <option value="M">M</option>
            <option value="F">F</option>
          </select>
          <input name="raca" placeholder="Raça">
          <label>Nascimento</label><input type="date" name="nascimento">
          <input name="origem" placeholder="Origem">
          <input name="lote" placeholder="Lote">
          <select name="status">
            <option value="ativo">Ativo</option>
            <option value="vendido">Vendido</option>
            <option value="descartado">Descartado</option>
          </select>
          <input type="number" step="0.01" name="peso_atual" placeholder="Peso atual (kg)">
          <label>Última pesagem</label><input type="date" name="ultima_pesagem">
          <textarea name="observacoes" placeholder="Observações"></textarea>
          <button class="btn btn-ok" type="submit">Salvar</button>
        </form>
      </div>

      <div class="card">
        <h3>Animais</h3>
        <table>
          <tr><th>Brinco</th><th>Nome</th><th>Peso</th><th>Status</th><th>Ações</th></tr>
          {% for a in animais %}
            <tr>
              <td>{{ a.brinco }}</td>
              <td>{{ a.nome or "-" }}</td>
              <td>{{ a.peso_atual }}</td>
              <td>{{ a.status|capitalize }}</td>
              <td>
                <a class="btn btn-ghost" href="{{ url_for('bovinocultura', animal=a.id) }}">Ficha</a>
                <a class="btn btn-ghost" href="{{ url_for('editar_bovino', bovino_id=a.id) }}">Editar</a>
                <form method="post" action="{{ url_for('excluir_bovino', bovino_id=a.id) }}" style="display:inline;">
                  <button type="submit" class="btn btn-ghost" onclick="return confirm('Tem certeza que deseja excluir este bovino e todos os seus registros?');">Excluir</button>
                </form>
              </td>
            </tr>
          {% endfor %}
        </table>
      </div>
    </div>

    {% if animal %}
    <div class="card">
      <h3>Ficha: {{ animal.brinco }} - {{ animal.nome or "-" }}</h3>
      <p class="muted">Raça: {{ animal.raca or "-" }} | Sexo: {{ animal.sexo or "-" }} | Nascimento: {{ animal.nascimento or "-" }}</p>
      <p class="muted">Peso atual: <b>{{ animal.peso_atual }} kg</b> | Última pesagem: <b>{{ animal.ultima_pesagem or "-" }}</b></p>
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
          new Chart(document.getElementById("pesoChart"), {
            type: "line",
            data: { labels: pData.labels, datasets: [{ label: "Peso (kg)", data: pData.vals, tension: 0.2 }] },
            options: { responsive: true }
          });
        </script>
        {% endif %}
      </div>

      <div class="card">
        <h3>Registrar evento</h3>
        <form method="post">
          <input type="hidden" name="form_type" value="novo_evento">
          <input type="hidden" name="bovino_id" value="{{ animal.id }}">
          <select name="tipo">
            <option value="vacina">Vacina</option>
            <option value="vermifugo">Vermífugo</option>
            <option value="manejo">Manejo</option>
            <option value="inseminacao">Inseminação</option>
            <option value="parto">Parto</option>
            <option value="tratamento">Tratamento</option>
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
        <tr><th>Data</th><th>Peso (kg)</th><th>Ações</th></tr>
        {% for p in pesos %}
          <tr>
            <td>{{ p.data }}</td>
            <td>{{ p.peso }}</td>
            <td>
              <form method="post" action="{{ url_for('excluir_pesagem', peso_id=p.id) }}" style="display:inline;">
                <button type="submit" class="btn btn-ghost" onclick="return confirm('Tem certeza que deseja excluir esta pesagem?');">Excluir</button>
              </form>
            </td>
          </tr>
        {% endfor %}
      </table>
    </div>

    <div class="card">
      <h3>Histórico de eventos</h3>
      <table>
        <tr><th>Data</th><th>Tipo</th><th>Descrição</th><th>Ações</th></tr>
        {% for e in eventos %}
          <tr>
            <td>{{ e.data }}</td>
            <td>{{ e.tipo|capitalize }}</td>
            <td>{{ e.descricao }}</td>
            <td>
              <form method="post" action="{{ url_for('excluir_evento', evento_id=e.id) }}" style="display:inline;">
                <button type="submit" class="btn btn-ghost" onclick="return confirm('Tem certeza que deseja excluir este evento?');">Excluir</button>
              </form>
            </td>
          </tr>
        {% endfor %}
      </table>
    </div>
    {% endif %}
    """
    return page(html_content, title="AP360 | Bovinocultura", animais=animais, animal=animal, pesos=pesos, eventos=eventos, chart=chart)


@app.route("/bovinocultura/editar/<int:bovino_id>", methods=["GET", "POST"])
@login_required
def editar_bovino(bovino_id):
    bovino = Bovino.query.filter_by(id=bovino_id, user_id=current_user.id).first_or_404()

    if request.method == "POST":
        bovino.brinco = request.form.get("brinco", "").strip()
        bovino.nome = request.form.get("nome", "").strip()
        bovino.sexo = request.form.get("sexo", "").strip()
        bovino.raca = request.form.get("raca", "").strip()
        bovino.nascimento = request.form.get("nascimento", "").strip()
        bovino.origem = request.form.get("origem", "").strip()
        bovino.lote = request.form.get("lote", "").strip()
        bovino.status = request.form.get("status", "ativo")
        bovino.observacoes = request.form.get("observacoes", "").strip()

        db.session.commit()
        flash("Dados do bovino atualizados com sucesso!")
        return redirect(url_for("bovinocultura", animal=bovino.id))

    html_content = """
    <h2>Editar Bovino</h2>
    <div class="card" style="max-width:700px;margin:0 auto">
      <form method="post">
        <label>Brinco</label>
        <input name="brinco" value="{{ bovino.brinco }}" required>
        <label>Nome</label>
        <input name="nome" value="{{ bovino.nome or '' }}">
        <label>Sexo</label>
        <select name="sexo">
          <option value="M" {% if bovino.sexo == 'M' %}selected{% endif %}>M</option>
          <option value="F" {% if bovino.sexo == 'F' %}selected{% endif %}>F</option>
        </select>
        <label>Raça</label>
        <input name="raca" value="{{ bovino.raca or '' }}">
        <label>Nascimento</label>
        <input type="date" name="nascimento" value="{{ bovino.nascimento or '' }}">
        <label>Origem</label>
        <input name="origem" value="{{ bovino.origem or '' }}">
        <label>Lote</label>
        <input name="lote" value="{{ bovino.lote or '' }}">
        <label>Status</label>
        <select name="status">
          <option value="ativo" {% if bovino.status == 'ativo' %}selected{% endif %}>Ativo</option>
          <option value="vendido" {% if bovino.status == 'vendido' %}selected{% endif %}>Vendido</option>
          <option value="descartado" {% if bovino.status == 'descartado' %}selected{% endif %}>Descartado</option>
        </select>
        <label>Observações</label>
        <textarea name="observacoes">{{ bovino.observacoes or '' }}</textarea>
        <button class="btn btn-ok" type="submit">Salvar Alterações</button>
        <a class="btn btn-ghost" href="{{ url_for('bovinocultura', animal=bovino.id) }}">Cancelar</a>
      </form>
    </div>
    """
    return page(html_content, title="AP360 | Editar Bovino", bovino=bovino)


@app.route("/bovinocultura/excluir/<int:bovino_id>", methods=["POST"])
@login_required
def excluir_bovino(bovino_id):
    bovino = Bovino.query.filter_by(id=bovino_id, user_id=current_user.id).first_or_404()
    # Excluir pesagens e eventos relacionados primeiro
    BovinoPeso.query.filter_by(bovino_id=bovino.id).delete()
    BovinoEvento.query.filter_by(bovino_id=bovino.id).delete()
    db.session.delete(bovino)
    db.session.commit()
    flash("Bovino e todos os seus registros excluídos com sucesso!")
    return redirect(url_for("bovinocultura"))


@app.route("/bovinocultura/pesagem/excluir/<int:peso_id>", methods=["POST"])
@login_required
def excluir_pesagem(peso_id):
    peso_registro = BovinoPeso.query.get_or_404(peso_id)
    bovino_id = peso_registro.bovino_id
    bovino = Bovino.query.filter_by(id=bovino_id, user_id=current_user.id).first_or_404()

    db.session.delete(peso_registro)
    db.session.commit()

    # Atualiza o peso_atual e ultima_pesagem do bovino
    ultima_pesagem = BovinoPeso.query.filter_by(bovino_id=bovino.id).order_by(BovinoPeso.data.desc()).first()
    if ultima_pesagem:
        bovino.peso_atual = ultima_pesagem.peso
        bovino.ultima_pesagem = ultima_pesagem.data
    else:
        bovino.peso_atual = 0.0
        bovino.ultima_pesagem = None
    db.session.commit()

    flash("Registro de pesagem excluído com sucesso!")
    return redirect(url_for("bovinocultura", animal=bovino_id))


@app.route("/bovinocultura/evento/excluir/<int:evento_id>", methods=["POST"])
@login_required
def excluir_evento(evento_id):
    evento_registro = BovinoEvento.query.get_or_404(evento_id)
    bovino_id = evento_registro.bovino_id
    bovino = Bovino.query.filter_by(id=bovino_id, user_id=current_user.id).first_or_404()

    db.session.delete(evento_registro)
    db.session.commit()
    flash("Registro de evento excluído com sucesso!")
    return redirect(url_for("bovinocultura", animal=bovino_id))


# =========================================================
# IA LOCAL
# =========================================================
def ia_local(msg: str):
    txt = (msg or "").strip()
    if not txt:
        return "Digite sua pergunta."
    dicas = [
        "Monitore GPD e CAA semanalmente para agir antes da perda de margem.",
        "Padronize coleta por estrutura/lote para comparação justa.",
        "Na agricultura, compare sempre margem líquida por tonelada."
    ]
    return f"AP360 IA: {txt[:170]}. Dica: {dicas[len(txt) % len(dicas)]}"


@app.route("/ia")
@login_required
def ia_page():
    html_content = """
    <h2>Assistente IA</h2>
    <div class="card">
      <form id="iaForm">
        <textarea name="mensagem" id="mensagem" placeholder="Pergunte sobre manejo, indicadores, estratégia..." required></textarea>
        <button class="btn btn-pri" type="submit">Enviar</button>
      </form>
      <div id="resp" class="card" style="display:none"></div>
    </div>
    <script>
      document.getElementById("iaForm").addEventListener("submit", async (e) => {
        e.preventDefault();
        const fd = new FormData(e.target);
        const r = await fetch("{{ url_for('ia_chat') }}", { method:"POST", body: fd });
        const data = await r.json();
        const box = document.getElementById("resp");
        box.style.display = "block";
        box.innerText = data.resposta || "Sem resposta";
      });
    </script>
    """
    return page(html_content, title="AP360 | IA")


@app.route("/ia/chat", methods=["POST"])
@login_required
def ia_chat():
    return jsonify({"resposta": ia_local(request.form.get("mensagem", ""))})


# =========================================================
# ERRORS
# =========================================================
@app.errorhandler(403)
def e403(_):
    return page("<div class='card'><h2>403</h2><p>Acesso negado.</p></div>", title="403"), 403


@app.errorhandler(404)
def e404(_):
    return page("<div class='card'><h2>404</h2><p>Página não encontrada.</p></div>", title="404"), 404


@app.errorhandler(500)
def e500(_):
    return page("<div class='card'><h2>500</h2><p>Erro interno do servidor.</p></div>", title="500"), 500


# =========================================================
# RUN
# =========================================================
if __name__ == "__main__":
    app.run(debug=True)