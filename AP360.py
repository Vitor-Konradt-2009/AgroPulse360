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

    # Benchmarks personalizados do produtor
    gpd_produtor_avicultura = db.Column(db.Float, default=0.0)
    ca_produtor_avicultura = db.Column(db.Float, default=0.0)
    gpd_produtor_suinocultura = db.Column(db.Float, default=0.0)
    ca_produtor_suinocultura = db.Column(db.Float, default=0.0)

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
        score += 0.4
    elif carne_magra_pct >= 54:
        score += 0.2

    if rendimento_carcaca_pct >= 78:
        score += 0.5
    elif rendimento_carcaca_pct >= 76:
        score += 0.3
    elif rendimento_carcaca_pct >= 74:
        score += 0.1

    return round(score * base_rs, 2)


def calc_bonificacao(gpd: float, ca_ajustada: float, coop_gpd: float, coop_ca: float, bonus_base: float) -> float:
    if coop_gpd <= 0 or coop_ca <= 0:
        return 0.0

    score_gpd = (gpd / coop_gpd) - 1
    score_ca = (coop_ca / ca_ajustada) - 1

    bonus = (score_gpd + score_ca) / 2 * bonus_base
    return round(max(0.0, bonus), 2)


def get_benchmark(cadeia: str, user: User):
    coop_benchmark = CoopBenchmark.query.filter_by(cadeia=cadeia, cooperativa=user.cooperativa).first()
    if coop_benchmark:
        return coop_benchmark.media_gpd, coop_benchmark.media_ca, coop_benchmark.bonus_base
    return 0.0, 0.0, 0.0


# =========================================================
# BASE HTML E FUNÇÃO PAGE
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
    /* CSS para as opções de seleção - CORRIGIDO */
    select {
      color: var(--text); /* Garante que o texto do select seja visível */
    }
    select option {
      background-color: #2a3a5b; /* Fundo escuro para as opções */
      color: var(--text);            /* Texto claro para as opções */
    }
    select option:checked {
      background-color: var(--pri); /* Fundo azul para a opção selecionada */
      color: #fff;                  /* Texto branco para a opção selecionada */
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
          <a href="{{ url_for('avicultura', cadeia='avicultura') }}">Avicultura</a>
          <a href="{{ url_for('suinocultura', cadeia='suinocultura') }}">Suinocultura</a>
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

    {% with messages = get_flashed_messages() %}
      {% if messages %}
        {% for m in messages %}
          <div class="flash">{{ m }}</div>
        {% endfor %}
      {% endif %}
    {% endwith %}

    {{ content|safe }}
  </div>
</div>
</body>
</html>
"""


def page(content, **kwargs):
    """Renderiza o conteúdo dentro do BASE_HTML."""
    ctx = dict(
        current_user=current_user,
        url_for=url_for,
        get_flashed_messages=get_flashed_messages, # CORRIGIDO AQUI
        **kwargs
    )
    return render_template_string(BASE_HTML, content=content, **ctx)


# =========================================================
# ROUTES
# =========================================================
@app.route("/")
def index():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    html_content = """
    <div class="hero">
      <h1>Bem-vindo ao AP360</h1>
      <p class="muted">Sua plataforma completa para gestão e análise de dados no agronegócio.</p>
      <p>
        <a href="{{ url_for('login') }}" class="btn btn-pri">Login</a>
        <a href="{{ url_for('signup_request') }}" class="btn btn-ghost">Solicitar Acesso</a>
      </p>
    </div>
    """
    return page(html_content, title="AP360 | Início")


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "").strip()
        user = User.query.filter_by(email=email).first()

        if user and user.check_password(password):
            if user.status == "ativo":
                login_user(user)
                flash(f"Bem-vindo de volta, {user.nome}!")
                return redirect(url_for("dashboard"))
            else:
                flash("Sua conta está bloqueada ou inativa. Entre em contato com o administrador.")
        else:
            flash("Email ou senha inválidos.")
    html_content = """
    <div class="card" style="max-width:400px;margin:0 auto">
      <h2>Login</h2>
      <form method="post">
        <input type="email" name="email" placeholder="Email" required>
        <input type="password" name="password" placeholder="Senha" required>
        <button class="btn btn-pri" type="submit">Entrar</button>
      </form>
      <p class="muted">Não tem uma conta? <a href="{{ url_for('signup_request') }}">Solicite acesso</a></p>
    </div>
    """
    return page(html_content, title="AP360 | Login")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("Você foi desconectado.")
    return redirect(url_for("index"))


@app.route("/signup_request", methods=["GET", "POST"])
def signup_request():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        nome = request.form.get("nome", "").strip()
        cpf = request.form.get("cpf", "").strip()
        telefone = request.form.get("telefone", "").strip()
        email = request.form.get("email", "").strip().lower()
        segmento = request.form.get("segmento", "").strip()
        cooperativa = request.form.get("cooperativa", "").strip()

        if User.query.filter_by(email=email).first() or AccessRequest.query.filter_by(email=email, status="pendente").first():
            flash("Já existe uma solicitação ou conta com este email.")
        else:
            new_request = AccessRequest(
                nome=nome,
                cpf=cpf,
                telefone=telefone,
                email=email,
                segmento=segmento,
                cooperativa=cooperativa
            )
            db.session.add(new_request)
            db.session.commit()
            flash("Sua solicitação de acesso foi enviada e será revisada pelo administrador.")
            return redirect(url_for("index"))
    html_content = """
    <div class="card" style="max-width:600px;margin:0 auto">
      <h2>Solicitar Acesso</h2>
      <form method="post">
        <input name="nome" placeholder="Nome Completo" required>
        <input name="cpf" placeholder="CPF (opcional)">
        <input name="telefone" placeholder="Telefone (opcional)">
        <input type="email" name="email" placeholder="Email" required>
        <label>Segmento</label>
        <select name="segmento">
          <option value="agricultura">Agricultura</option>
          <option value="avicultura">Avicultura</option>
          <option value="suinocultura">Suinocultura</option>
          <option value="bovinocultura">Bovinocultura</option>
        </select>
        <input name="cooperativa" placeholder="Nome da Cooperativa (opcional)">
        <button class="btn btn-pri" type="submit">Enviar Solicitação</button>
      </form>
      <p class="muted">Já tem um convite? <a href="{{ url_for('signup_invite') }}">Ativar convite</a></p>
    </div>
    """
    return page(html_content, title="AP360 | Solicitar Acesso")


@app.route("/signup_invite", methods=["GET", "POST"])
def signup_invite():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        token = request.form.get("token", "").strip()
        password = request.form.get("password", "").strip()
        nome = request.form.get("nome", "").strip()
        cpf = request.form.get("cpf", "").strip()
        telefone = request.form.get("telefone", "").strip()
        segmento = request.form.get("segmento", "").strip()
        cooperativa = request.form.get("cooperativa", "").strip()

        invite = AccessInvite.query.filter_by(email=email, token=token, status="convidado").first()
        if not invite:
            flash("Email ou token de convite inválidos ou já utilizados.")
            return redirect(url_for("signup_invite"))

        if User.query.filter_by(email=email).first():
            flash("Já existe uma conta com este email.")
            return redirect(url_for("signup_invite"))

        new_user = User(
            nome=nome,
            cpf=cpf,
            telefone=telefone,
            email=email,
            segmento=segmento,
            cooperativa=cooperativa,
            perfil="produtor",
            status="ativo"
        )
        new_user.set_password(password)
        db.session.add(new_user)

        invite.status = "ativado"
        invite.ativado_em = datetime.utcnow()
        db.session.commit()

        login_user(new_user)
        flash("Sua conta foi criada e você está logado!")
        return redirect(url_for("dashboard"))

    html_content = """
    <div class="card" style="max-width:600px;margin:0 auto">
      <h2>Ativar Convite</h2>
      <form method="post">
        <input type="email" name="email" placeholder="Email do convite" required>
        <input name="token" placeholder="Token de convite" required>
        <input type="password" name="password" placeholder="Crie sua senha" required>
        <input name="nome" placeholder="Nome Completo" required>
        <input name="cpf" placeholder="CPF (opcional)">
        <input name="telefone" placeholder="Telefone (opcional)">
        <label>Segmento</label>
        <select name="segmento">
          <option value="agricultura">Agricultura</option>
          <option value="avicultura">Avicultura</option>
          <option value="suinocultura">Suinocultura</option>
          <option value="bovinocultura">Bovinocultura</option>
        </select>
        <input name="cooperativa" placeholder="Nome da Cooperativa (opcional)">
        <button class="btn btn-pri" type="submit">Ativar Convite</button>
      </form>
    </div>
    """
    return page(html_content, title="AP360 | Ativar Convite")


@app.route("/dashboard")
@login_required
def dashboard():
    html_content = f"""
    <h2>Dashboard</h2>
    <div class="card">
      <h3>Bem-vindo, <span class="welcome-name">{current_user.nome}</span>!</h3>
      <p class="muted">Seu perfil: {current_user.perfil.capitalize()}</p>
      <p class="muted">Seu segmento: {current_user.segmento.capitalize() if current_user.segmento else 'Não informado'}</p>
      <p class="muted">Sua cooperativa: {current_user.cooperativa if current_user.cooperativa else 'Não informado'}</p>
    </div>
    """
    return page(html_content, title="AP360 | Dashboard")


# =========================================================
# AGRICULTURA
# =========================================================
@app.route("/agricultura", methods=["GET", "POST"])
@login_required
def agricultura():
    if request.method == "POST":
        produto = request.form.get("produto", "").strip()
        quantidade_ton = float(request.form.get("quantidade_ton", 0) or 0)
        origem = request.form.get("origem", "").strip()
        porto = request.form.get("porto", "").strip()

        export_rs_ton, cbot_usd_bushel = cbot_para_rs_ton(produto)
        usd_brl = fx_usd_brl()
        frete_rs_ton = frete_medio(origem, porto)
        liquido_rs_ton = export_rs_ton - frete_rs_ton
        total_rs = liquido_rs_ton * quantidade_ton

        new_quote = AgricultureQuote(
            user_id=current_user.id,
            produto=produto,
            quantidade_ton=quantidade_ton,
            origem=origem,
            porto=porto,
            cbot_usd_bushel=cbot_usd_bushel,
            usd_brl=usd_brl,
            export_rs_ton=export_rs_ton,
            frete_rs_ton=frete_rs_ton,
            liquido_rs_ton=liquido_rs_ton,
            total_rs=total_rs
        )
        db.session.add(new_quote)
        db.session.commit()
        flash("Cotação registrada com sucesso!")
        return redirect(url_for("agricultura"))

    latest_quotes = AgricultureQuote.query.filter_by(user_id=current_user.id).order_by(AgricultureQuote.criado_em.desc()).limit(10).all()

    html_content = """
    <h2>Agricultura</h2>

    <div class="card">
      <h3>Nova Cotação</h3>
      <form method="post">
        <label>Produto</label>
        <select name="produto">
          <option value="soja">Soja</option>
          <option value="milho">Milho</option>
          <option value="trigo">Trigo</option>
          <option value="aveia">Aveia</option>
          <option value="arroz">Arroz</option>
        </select>
        <label>Quantidade (ton)</label>
        <input type="number" step="0.01" name="quantidade_ton" required>
        <label>Origem (Cidade-UF)</label>
        <input name="origem" placeholder="Ex: Cascavel-PR" required>
        <label>Porto</label>
        <select name="porto">
          <option value="Paranaguá">Paranaguá</option>
          <option value="Santos">Santos</option>
          <option value="Rio Grande">Rio Grande</option>
          <option value="Itajaí">Itajaí</option>
        </select>
        <button class="btn btn-pri" type="submit">Calcular e Salvar</button>
      </form>
    </div>

    <div class="card">
      <h3>Últimas Cotações</h3>
      <table>
        <tr><th>Produto</th><th>Origem</th><th>Porto</th><th>Líquido R$/ton</th><th>Total R$</th><th>Data</th></tr>
        {% for quote in latest_quotes %}
          <tr>
            <td>{{ quote.produto|capitalize }}</td>
            <td>{{ quote.origem }}</td>
            <td>{{ quote.porto }}</td>
            <td>{{ "%.2f"|format(quote.liquido_rs_ton) }}</td>
            <td>{{ "%.2f"|format(quote.total_rs) }}</td>
            <td>{{ quote.criado_em.strftime("%d/%m %H:%M") }}</td>
          </tr>
        {% endfor %}
      </table>
    </div>
    """
    return page(html_content, title="AP360 | Agricultura", latest_quotes=latest_quotes)


# =========================================================
# AVICULTURA E SUINOCULTURA (MÓDULO DE LOTES)
# =========================================================
def modulo_lotes(cadeia: str):
    if request.method == "POST":
        form_type = request.form.get("form_type")

        if form_type == "novo_lote":
            estrutura = request.form.get("estrutura", "").strip()
            lote_nome = request.form.get("lote", "").strip()

            if Batch.query.filter_by(user_id=current_user.id, cadeia=cadeia, estrutura=estrutura, lote=lote_nome).first():
                flash(f"Já existe um lote '{lote_nome}' na estrutura '{estrutura}' para {cadeia}.")
                return redirect(url_for(cadeia))

            peso_inicial = float(request.form.get("peso_inicial", 0) or 0)
            peso_final = float(request.form.get("peso_final", 0) or 0)
            dias = int(request.form.get("dias", 0) or 0)
            racao_total_kg = float(request.form.get("racao_total_kg", 0) or 0)
            animais_iniciais = int(request.form.get("animais_iniciais", 0) or 0)
            animais_final = int(request.form.get("animais_final", 0) or 0)

            gpd = calc_gpd(peso_inicial, peso_final, dias)
            ca = calc_ca(racao_total_kg, peso_inicial, peso_final)
            viabilidade_pct = calc_viabilidade(animais_iniciais, animais_final)
            mortalidade_pct = calc_mortalidade(animais_iniciais, animais_final)

            coop_media_gpd, coop_media_ca, bonus_base = get_benchmark(cadeia, current_user)

            ca_ajustada = ca
            iep = 0.0
            indice_lote = 0.0
            bonificacao = 0.0
            bonus_tipificacao = 0.0

            if cadeia == "avicultura":
                peso_meta_coop = float(request.form.get("peso_meta_coop", 0) or 0)
                idade_meta_coop = int(request.form.get("idade_meta_coop", 0) or 0)
                fator_peso_caa = float(request.form.get("fator_peso_caa", 0.30) or 0.30)
                fator_idade_caa = float(request.form.get("fator_idade_caa", 0.01) or 0.01)
                ca_ajustada = calc_ca_ajustada_avicultura(ca, peso_final, dias,
                                                           peso_meta_coop, idade_meta_coop,
                                                           fator_peso_caa, fator_idade_caa)
                iep = calc_iep_avicultura(viabilidade_pct, peso_final, dias, ca_ajustada)
                bonificacao = calc_bonificacao(gpd, ca_ajustada, coop_media_gpd, coop_media_ca, bonus_base)
            elif cadeia == "suinocultura":
                peso_vivo_medio = float(request.form.get("peso_vivo_medio", 0) or 0)
                peso_carcaca_medio = float(request.form.get("peso_carcaca_medio", 0) or 0)
                carne_magra_pct = float(request.form.get("carne_magra_pct", 0) or 0)
                rendimento_carcaca_pct = calc_rendimento_carcaca(peso_vivo_medio, peso_carcaca_medio)
                bonus_tipificacao = calc_bonus_tipificacao(carne_magra_pct, rendimento_carcaca_pct)
                indice_lote = calc_indice_lote_suino(gpd, viabilidade_pct, ca_ajustada)
                bonificacao = calc_bonificacao(gpd, ca_ajustada, coop_media_gpd, coop_media_ca, bonus_base) + bonus_tipificacao

            new_batch = Batch(
                user_id=current_user.id,
                cadeia=cadeia,
                estrutura=estrutura,
                lote=lote_nome,
                peso_inicial=peso_inicial,
                peso_final=peso_final,
                dias=dias,
                racao_total_kg=racao_total_kg,
                animais_iniciais=animais_iniciais,
                animais_final=animais_final,
                viabilidade_pct=viabilidade_pct,
                mortalidade_pct=mortalidade_pct,
                gpd=gpd,
                ca=ca,
                ca_ajustada=ca_ajustada,
                iep=iep,
                indice_lote=indice_lote,
                bonificacao=bonificacao,
                coop_media_gpd=coop_media_gpd,
                coop_media_ca=coop_media_ca,
                ca_coop_ref=float(request.form.get("ca_coop_ref", 0) or 0),
                gpd_coop_ref=float(request.form.get("gpd_coop_ref", 0) or 0),
                peso_meta_coop=float(request.form.get("peso_meta_coop", 0) or 0),
                idade_meta_coop=int(request.form.get("idade_meta_coop", 0) or 0),
                fator_peso_caa=float(request.form.get("fator_peso_caa", 0.30) or 0.30),
                fator_idade_caa=float(request.form.get("fator_idade_caa", 0.01) or 0.01),
                peso_vivo_medio=float(request.form.get("peso_vivo_medio", 0) or 0),
                peso_carcaca_medio=float(request.form.get("peso_carcaca_medio", 0) or 0),
                rendimento_carcaca_pct=rendimento_carcaca_pct,
                carne_magra_pct=float(request.form.get("carne_magra_pct", 0) or 0),
                bonus_tipificacao=bonus_tipificacao
            )
            db.session.add(new_batch)
            db.session.commit()
            flash(f"Lote de {cadeia} registrado com sucesso!")
            return redirect(url_for(cadeia))

        if form_type == "atualizar_benchmark_pessoal":
            if cadeia == "avicultura":
                current_user.gpd_produtor_avicultura = float(request.form.get("gpd_produtor_avicultura", 0) or 0)
                current_user.ca_produtor_avicultura = float(request.form.get("ca_produtor_avicultura", 0) or 0)
            elif cadeia == "suinocultura":
                current_user.gpd_produtor_suinocultura = float(request.form.get("gpd_produtor_suinocultura", 0) or 0)
                current_user.ca_produtor_suinocultura = float(request.form.get("ca_produtor_suinocultura", 0) or 0)
            db.session.commit()
            flash("Benchmark pessoal atualizado!")
            return redirect(url_for(cadeia))

    hist = Batch.query.filter_by(user_id=current_user.id, cadeia=cadeia).order_by(Batch.criado_em.desc()).all()

    # Lógica de comparação de lotes
    c1_id = request.args.get("c1", type=int)
    c2_id = request.args.get("c2", type=int)
    compare_data = None
    if c1_id and c2_id:
        lote_a = Batch.query.get(c1_id)
        lote_b = Batch.query.get(c2_id)
        if lote_a and lote_b and lote_a.user_id == current_user.id and lote_b.user_id == current_user.id:
            labels = ["GPD", "CA", "Viabilidade%", "Mortalidade%", "Bonificação"]
            a_vals = [lote_a.gpd, lote_a.ca, lote_a.viabilidade_pct, lote_a.mortalidade_pct, lote_a.bonificacao]
            b_vals = [lote_b.gpd, lote_b.ca, lote_b.viabilidade_pct, lote_b.mortalidade_pct, lote_b.bonificacao]
            if cadeia == "avicultura":
                labels.append("IEP")
                a_vals.append(lote_a.iep)
                b_vals.append(lote_b.iep)
            elif cadeia == "suinocultura":
                labels.append("Índice Lote")
                a_vals.append(lote_a.indice_lote)
                b_vals.append(lote_b.indice_lote)

            compare_data = {
                "labels": labels,
                "a_name": f"{lote_a.estrutura}/{lote_a.lote}",
                "a_vals": a_vals,
                "b_name": f"{lote_b.estrutura}/{lote_b.lote}",
                "b_vals": b_vals,
            }

    html_content = """
    <h2>""" + cadeia.capitalize() + """</h2>

    <div class="card">
      <h3>Novo Lote</h3>
      <form method="post">
        <input type="hidden" name="form_type" value="novo_lote">
        <label>Estrutura</label>
        <input name="estrutura" placeholder="Ex: Galpão 1, Baia 3" required>
        <label>Lote</label>
        <input name="lote" placeholder="Ex: Lote 2024-01" required>
        <label>Peso inicial (kg)</label>
        <input type="number" step="0.0001" name="peso_inicial" required>
        <label>Peso final (kg)</label>
        <input type="number" step="0.0001" name="peso_final" required>
        <label>Dias de alojamento</label>
        <input type="number" name="dias" required>
        <label>Ração total (kg)</label>
        <input type="number" step="0.0001" name="racao_total_kg" required>
        <label>Animais iniciais</label>
        <input type="number" name="animais_iniciais" required>
        <label>Animais finais (abatidos/vendidos)</label>
        <input type="number" name="animais_final" required>

        <h4>Benchmark Pessoal (opcional, prioridade sobre cooperativa)</h4>
        {% if cadeia == 'avicultura' %}
          <label>Seu GPD médio ideal (Avicultura)</label>
          <input type="number" step="0.0001" name="gpd_produtor_avicultura" value="{{ current_user.gpd_produtor_avicultura if current_user.gpd_produtor_avicultura > 0 else '' }}">
          <label>Sua CA média ideal (Avicultura)</label>
          <input type="number" step="0.0001" name="ca_produtor_avicultura" value="{{ current_user.ca_produtor_avicultura if current_user.ca_produtor_avicultura > 0 else '' }}">
        {% elif cadeia == 'suinocultura' %}
          <label>Seu GPD médio ideal (Suinocultura)</label>
          <input type="number" step="0.0001" name="gpd_produtor_suinocultura" value="{{ current_user.gpd_produtor_suinocultura if current_user.gpd_produtor_suinocultura > 0 else '' }}">
          <label>Sua CA média ideal (Suinocultura)</label>
          <input type="number" step="0.0001" name="ca_produtor_suinocultura" value="{{ current_user.ca_produtor_suinocultura if current_user.ca_produtor_suinocultura > 0 else '' }}">
        {% endif %}
        <button class="btn btn-ghost" type="submit" name="form_type" value="atualizar_benchmark_pessoal">Atualizar Benchmark Pessoal</button>

        <h4>Referência cooperativa (informada pelo produtor)</h4>
        <label>GPD médio cooperativa (opcional)</label>
        <input type="number" step="0.0001" name="gpd_coop_ref">
        <label>CA média cooperativa (opcional)</label>
        <input type="number" step="0.0001" name="ca_coop_ref">

        {% if cadeia == 'avicultura' %}
          <h4>Parâmetros CAA (Avicultura)</h4>
          <label>Peso meta coop (kg)</label>
          <input type="number" step="0.0001" name="peso_meta_coop" required>
          <label>Idade meta coop (dias)</label>
          <input type="number" name="idade_meta_coop" required>
          <label>Fator peso CAA</label>
          <input type="number" step="0.0001" name="fator_peso_caa" value="0.30">
          <label>Fator idade CAA</label>
          <input type="number" step="0.0001" name="fator_idade_caa" value="0.01">
        {% endif %}

        {% if cadeia == 'suinocultura' %}
          <h4>Carcaça e tipificação (Suínos)</h4>
          <label>Peso vivo médio (kg/cab)</label>
          <input type="number" step="0.01" name="peso_vivo_medio" required>
          <label>Peso carcaça médio (kg/cab)</label>
          <input type="number" step="0.01" name="peso_carcaca_medio" required>
          <label>% carne magra</label>
          <input type="number" step="0.01" name="carne_magra_pct" required>
        {% endif %}

        <button class="btn btn-ok" type="submit">Salvar Lote</button>
      </form>
    </div>

    <div class="card">
      <h3>Histórico de Lotes</h3>
      <table>
        <tr>
          <th>Estrutura/Lote</th>
          <th>GPD</th>
          <th>CA</th>
          <th>Viabilidade%</th>
          <th>Mortalidade%</th>
          {% if cadeia == 'avicultura' %}
            <th>IEP</th>
          {% elif cadeia == 'suinocultura' %}
            <th>Índice Lote</th>
          {% endif %}
          <th>Bonificação R$</th>
          <th>Data</th>
          <th>Ações</th>
        </tr>
        {% for b in hist %}
          <tr>
            <td>{{ b.estrutura }}/{{ b.lote }}</td>
            <td>{{ "%.4f"|format(b.gpd) }}</td>
            <td>{{ "%.4f"|format(b.ca) }}</td>
            <td>{{ "%.2f"|format(b.viabilidade_pct) }}</td>
            <td>{{ "%.2f"|format(b.mortalidade_pct) }}</td>
            {% if cadeia == 'avicultura' %}
              <td>{{ "%.2f"|format(b.iep) }}</td>
            {% elif cadeia == 'suinocultura' %}
              <td>{{ "%.2f"|format(b.indice_lote) }}</td>
            {% endif %}
            <td>{{ "%.2f"|format(b.bonificacao) }}</td>
            <td>{{ b.criado_em.strftime("%d/%m %H:%M") }}</td>
            <td>
              <a class="btn btn-ghost" href="{{ url_for('editar_lote', cadeia=cadeia, batch_id=b.id) }}">Editar</a>
              <form method="post" action="{{ url_for('excluir_lote', cadeia=cadeia, batch_id=b.id) }}" style="display:inline;">
                <button type="submit" class="btn btn-ghost" onclick="return confirm('Tem certeza que deseja excluir este lote?');">Excluir</button>
              </form>
              <a class="btn btn-ghost" href="{{ url_for(cadeia, c1=b.id, c2=request.args.get('c2')) }}">Comparar A</a>
              <a class="btn btn-ghost" href="{{ url_for(cadeia, c1=request.args.get('c1'), c2=b.id) }}">Comparar B</a>
            </td>
          </tr>
        {% endfor %}
      </table>
    </div>

    {% if compare_data %}
    <div class="card">
      <h3>Comparativo de Lotes</h3>
      <canvas id="compareChart" height="90"></canvas>
      <script>
        const compareData = {{ compare_data | tojson }};
        new Chart(document.getElementById("compareChart"), {
          type: "bar",
          data: {
            labels: compareData.labels,
            datasets: [
              { label: compareData.a_name, data: compareData.a_vals, backgroundColor: 'rgba(59, 185, 255, 0.6)' },
              { label: compareData.b_name, data: compareData.b_vals, backgroundColor: 'rgba(70, 221, 152, 0.6)' }
            ]
          },
          options: { responsive: true, scales: { y: { beginAtZero: true } } }
        });
      </script>
    </div>
    {% endif %}
    """
    return page(html_content, title=f"AP360 | {cadeia.capitalize()}", hist=hist, cadeia=cadeia, compare_data=compare_data)


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
        batch.peso_inicial = float(request.form.get("peso_inicial", 0) or 0)
        batch.peso_final = float(request.form.get("peso_final", 0) or 0)
        batch.dias = int(request.form.get("dias", 0) or 0)
        batch.racao_total_kg = float(request.form.get("racao_total_kg", 0) or 0)
        batch.animais_iniciais = int(request.form.get("animais_iniciais", 0) or 0)
        batch.animais_final = int(request.form.get("animais_final", 0) or 0)

        batch.gpd = calc_gpd(batch.peso_inicial, batch.peso_final, batch.dias)
        batch.ca = calc_ca(batch.racao_total_kg, batch.peso_inicial, batch.peso_final)
        batch.viabilidade_pct = calc_viabilidade(batch.animais_iniciais, batch.animais_final)
        batch.mortalidade_pct = calc_mortalidade(batch.animais_iniciais, batch.animais_final)

        batch.ca_coop_ref = float(request.form.get("ca_coop_ref", 0) or 0)
        batch.gpd_coop_ref = float(request.form.get("gpd_coop_ref", 0) or 0)

        coop_media_gpd, coop_media_ca, bonus_base = get_benchmark(cadeia, current_user)

        if cadeia == "avicultura":
            batch.peso_meta_coop = float(request.form.get("peso_meta_coop", 0) or 0)
            batch.idade_meta_coop = int(request.form.get("idade_meta_coop", 0) or 0)
            batch.fator_peso_caa = float(request.form.get("fator_peso_caa", 0.30) or 0.30)
            batch.fator_idade_caa = float(request.form.get("fator_idade_caa", 0.01) or 0.01)
            batch.ca_ajustada = calc_ca_ajustada_avicultura(batch.ca, batch.peso_final, batch.dias,
                                                             batch.peso_meta_coop, batch.idade_meta_coop,
                                                             batch.fator_peso_caa, batch.fator_idade_caa)
            batch.iep = calc_iep_avicultura(batch.viabilidade_pct, batch.peso_final, batch.dias, batch.ca_ajustada)
            batch.bonificacao = calc_bonificacao(batch.gpd, batch.ca_ajustada, coop_media_gpd, coop_media_ca, bonus_base)
        elif cadeia == "suinocultura":
            batch.peso_vivo_medio = float(request.form.get("peso_vivo_medio", 0) or 0)
            batch.peso_carcaca_medio = float(request.form.get("peso_carcaca_medio", 0) or 0)
            batch.carne_magra_pct = float(request.form.get("carne_magra_pct", 0) or 0)
            batch.rendimento_carcaca_pct = calc_rendimento_carcaca(batch.peso_vivo_medio, batch.peso_carcaca_medio)
            batch.bonus_tipificacao = calc_bonus_tipificacao(batch.carne_magra_pct, batch.rendimento_carcaca_pct)
            batch.ca_ajustada = batch.ca # Suínos geralmente não usam CAA da mesma forma que aves
            batch.indice_lote = calc_indice_lote_suino(batch.gpd, batch.viabilidade_pct, batch.ca_ajustada)
            batch.bonificacao = calc_bonificacao(batch.gpd, batch.ca_ajustada, coop_media_gpd, coop_media_ca, bonus_base) + batch.bonus_tipificacao

        db.session.commit()
        flash(f"Lote de {cadeia} atualizado com sucesso!")
        return redirect(url_for(cadeia))

    html_content = """
    <h2>Editar Lote de """ + cadeia.capitalize() + """</h2>
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
          <h4>Parâmetros CAA (Avicultura)</h4>
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
# ADMIN
# =========================================================
@app.route("/admin")
@admin_required
def admin_panel():
    solicitacoes = AccessRequest.query.filter_by(status="pendente").order_by(AccessRequest.criado_em.desc()).all()
    convites = AccessInvite.query.filter_by(status="convidado").order_by(AccessInvite.criado_em.desc()).all()
    usuarios = User.query.order_by(User.criado_em.desc()).all()
    benchmarks_coop = CoopBenchmark.query.all()

    html_content = """
    <h2>Painel Administrativo</h2>

    <div class="card">
      <h3>Solicitações de Acesso</h3>
      <table>
        <tr><th>Data</th><th>Nome</th><th>Email</th><th>Segmento</th><th>Status</th><th>Ação</th></tr>
        {% for s in solicitacoes %}
          <tr>
            <td>{{ s.criado_em.strftime("%d/%m %H:%M") }}</td>
            <td>{{ s.nome }}</td>
            <td>{{ s.email }}</td>
            <td>{{ s.segmento|capitalize }}</td>
            <td>{{ s.status|capitalize }}</td>
            <td>
              <form method="post" action="{{ url_for('aprovar_solicitacao', request_id=s.id) }}" style="display:inline;">
                <button type="submit" class="btn btn-ok">Aprovar</button>
              </form>
              <form method="post" action="{{ url_for('negar_solicitacao', request_id=s.id) }}" style="display:inline;">
                <button type="submit" class="btn btn-ghost">Negar</button>
              </form>
            </td>
          </tr>
        {% endfor %}
      </table>
    </div>

    <div class="card">
      <h3>Convites Pendentes</h3>
      <table>
        <tr><th>Email</th><th>Status</th><th>Token</th></tr>
        {% for c in convites %}
          <tr>
            <td>{{ c.email }}</td>
            <td>{{ c.status|capitalize }}</td>
            <td>{{ c.token }}</td>
          </tr>
        {% endfor %}
      </table>
    </div>

    <div class="card">
      <h3>Usuários</h3>
      <table>
        <tr><th>Nome</th><th>Email</th><th>Perfil</th><th>Status</th><th>Segmento</th><th>Ações</th></tr>
        {% for u in usuarios %}
          <tr>
            <td>{{ u.nome }}</td>
            <td>{{ u.email }}</td>
            <td>{{ u.perfil|capitalize }}</td>
            <td>{{ u.status|capitalize }}</td>
            <td>{{ u.segmento|capitalize if u.segmento else '-' }}</td>
            <td>
              {% if u.perfil != 'admin' %}
                <form method="post" action="{{ url_for('bloquear_usuario', user_id=u.id) }}" style="display:inline;">
                  <button type="submit" class="btn btn-ghost">Bloquear</button>
                </form>
                <form method="post" action="{{ url_for('desbloquear_usuario', user_id=u.id) }}" style="display:inline;">
                  <button type="submit" class="btn btn-ok">Desbloquear</button>
                </form>
              {% else %}
                <span>Admin</span>
              {% endif %}
            </td>
          </tr>
        {% endfor %}
      </table>
    </div>

    <div class="card">
      <h3>Benchmark cooperativa</h3>
      <form method="post" action="{{ url_for('atualizar_benchmark_coop') }}">
        <label>Cadeia</label>
        <select name="cadeia">
          <option value="avicultura">Avicultura</option>
          <option value="suinocultura">Suinocultura</option>
        </select>
        <label>Cooperativa</label>
        <input name="cooperativa" placeholder="Nome da cooperativa" required>
        <label>Média GPD</label>
        <input type="number" step="0.0001" name="media_gpd" required>
        <label>Média CA</label>
        <input type="number" step="0.0001" name="media_ca" required>
        <label>Base bônus R$</label>
        <input type="number" step="0.01" name="bonus_base" required>
        <button class="btn btn-pri" type="submit">Salvar</button>
      </form>
      <table>
        <tr><th>Cadeia</th><th>Cooperativa</th><th>GPD</th><th>CA</th><th>Base bônus</th></tr>
        {% for b in benchmarks_coop %}
          <tr>
            <td>{{ b.cadeia|capitalize }}</td>
            <td>{{ b.cooperativa }}</td>
            <td>{{ b.media_gpd }}</td>
            <td>{{ b.media_ca }}</td>
            <td>{{ b.bonus_base }}</td>
          </tr>
        {% endfor %}
      </table>
    </div>
    """
    return page(html_content, title="AP360 | Admin", solicitacoes=solicitacoes, convites=convites, usuarios=usuarios, benchmarks_coop=benchmarks_coop)


@app.route("/admin/aprovar_solicitacao/<int:request_id>", methods=["POST"])
@admin_required
def aprovar_solicitacao(request_id):
    solicitacao = AccessRequest.query.get_or_404(request_id)
    solicitacao.status = "liberado"
    token = secrets.token_urlsafe(16)
    invite = AccessInvite(email=solicitacao.email, token=token, request_id=solicitacao.id)
    db.session.add(invite)
    db.session.commit()
    flash(f"Solicitação de {solicitacao.email} aprovada. Convite gerado: {token}")
    return redirect(url_for("admin_panel"))


@app.route("/admin/negar_solicitacao/<int:request_id>", methods=["POST"])
@admin_required
def negar_solicitacao(request_id):
    solicitacao = AccessRequest.query.get_or_404(request_id)
    solicitacao.status = "negado"
    db.session.commit()
    flash(f"Solicitação de {solicitacao.email} negada.")
    return redirect(url_for("admin_panel"))


@app.route("/admin/bloquear_usuario/<int:user_id>", methods=["POST"])
@admin_required
def bloquear_usuario(user_id):
    user = User.query.get_or_404(user_id)
    if user.perfil == "admin":
        flash("Não é possível bloquear um administrador.")
    else:
        user.status = "bloqueado"
        db.session.commit()
        flash(f"Usuário {user.email} bloqueado.")
    return redirect(url_for("admin_panel"))


@app.route("/admin/desbloquear_usuario/<int:user_id>", methods=["POST"])
@admin_required
def desbloquear_usuario(user_id):
    user = User.query.get_or_404(user_id)
    user.status = "ativo"
    db.session.commit()
    flash(f"Usuário {user.email} desbloqueado.")
    return redirect(url_for("admin_panel"))


@app.route("/admin/atualizar_benchmark_coop", methods=["POST"])
@admin_required
def atualizar_benchmark_coop():
    cadeia = request.form.get("cadeia", "").strip()
    cooperativa = request.form.get("cooperativa", "").strip()
    media_gpd = float(request.form.get("media_gpd", 0) or 0)
    media_ca = float(request.form.get("media_ca", 0) or 0)
    bonus_base = float(request.form.get("bonus_base", 0) or 0)

    benchmark = CoopBenchmark.query.filter_by(cadeia=cadeia, cooperativa=cooperativa).first()
    if benchmark:
        benchmark.media_gpd = media_gpd
        benchmark.media_ca = media_ca
        benchmark.bonus_base = bonus_base
        benchmark.atualizado_em = datetime.utcnow()
    else:
        benchmark = CoopBenchmark(
            cadeia=cadeia,
            cooperativa=cooperativa,
            media_gpd=media_gpd,
            media_ca=media_ca,
            bonus_base=bonus_base
        )
        db.session.add(benchmark)
    db.session.commit()
    flash(f"Benchmark da cooperativa {cooperativa} ({cadeia}) atualizado com sucesso!")
    return redirect(url_for("admin_panel"))


# =========================================================
# RUN
# =========================================================
if __name__ == "__main__":
    app.run(debug=True)