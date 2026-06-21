import os
import io
import csv
import secrets
from datetime import datetime
from functools import wraps

from flask import (
    Flask, request, redirect, url_for, flash,
    render_template, render_template_string, jsonify, Response, abort
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

app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "ap360vitor")
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


def get_benchmark(cadeia, user: User):
    # Prioriza benchmarks do produtor, se existirem
    if cadeia == "avicultura":
        if user.gpd_produtor_avicultura > 0 and user.ca_produtor_avicultura > 0:
            return user.gpd_produtor_avicultura, user.ca_produtor_avicultura, 1000.0 # Bônus base padrão para produtor
    elif cadeia == "suinocultura":
        if user.gpd_produtor_suinocultura > 0 and user.ca_produtor_suinocultura > 0:
            return user.gpd_produtor_suinocultura, user.ca_produtor_suinocultura, 1200.0 # Bônus base padrão para produtor

    # Se não houver benchmark do produtor, busca o da cooperativa
    if user.cooperativa:
        row = CoopBenchmark.query.filter_by(cadeia=cadeia, cooperativa=user.cooperativa).first()
        if row:
            return row.media_gpd or 0.065, row.media_ca or 1.70, row.bonus_base or 1000.0

    # Se não houver nenhum, retorna valores padrão
    if cadeia == "avicultura":
        return 0.065, 1.70, 1000.0
    elif cadeia == "suinocultura":
        return 0.72, 2.45, 1200.0
    return 0.065, 1.70, 1000.0 # Fallback genérico


# =========================================================
# UI BASE (agora usa render_template)
# =========================================================
def page(content: str, **ctx):
    # Renderiza o conteúdo específico da página com o contexto fornecido
    # Usamos render_template_string para o 'content' que ainda é uma string HTML
    processed_content = render_template_string(content, **ctx)
    # Renderiza o template base.html, injetando o conteúdo processado
    return render_template("base.html", content=processed_content, **ctx)


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
      <p class="muted">Agricultura, avicultura, suinocultura e bovinocultura na palma da sua mão.</p>
      <p>
        <a class="btn btn-pri" href="{{ url_for('signup_request') }}">Inscreva-se</a>
        <a class="btn btn-ghost" href="{{ url_for('login') }}">Login</a>
      </p>
    </section>
    """
    return page(html_content, title="AP360 | Início")


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        senha = request.form.get("senha", "")
        user = User.query.filter_by(email=email).first()

        if user and user.check_password(senha):
            login_user(user)
            flash(f"Bem-vindo(a) de volta, {user.nome.split(' ')[0]}!")
            return redirect(url_for("dashboard"))
        else:
            flash("E-mail ou senha inválidos.")

    html_content = """
    <div class="card" style="max-width:400px;margin:0 auto">
      <h2 style="margin-top:0">Login</h2>
      <form method="post">
        <input name="email" type="email" placeholder="E-mail" required>
        <input name="senha" type="password" placeholder="Senha" required>
        <button class="btn btn-pri" type="submit">Entrar</button>
      </form>
      <p class="muted">
        Não tem uma conta? <a href="{{ url_for('signup_request') }}">Inscreva-se</a>
      </p>
    </div>
    """
    return page(html_content, title="AP360 | Login")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("Você foi desconectado(a).")
    return redirect(url_for("index"))


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
            segmento=req.segmento if req and req.segmento else "agricultura",
            cooperativa=req.cooperativa if req and req.cooperativa else None
        )
        user.set_password(senha)
        db.session.add(user)

        inv.status = "ativado"
        inv.ativado_em = datetime.utcnow()
        db.session.add(inv)

        db.session.commit()
        flash("Conta ativada com sucesso! Faça login para continuar.")
        return redirect(url_for("login"))

    html_content = """
    <div class="card" style="max-width:400px;margin:0 auto">
      <h2 style="margin-top:0">Ativar conta</h2>
      <p class="muted">E-mail: {{ inv.email }}</p>
      <form method="post">
        <input name="senha" type="password" placeholder="Nova senha" required>
        <input name="confirmar_senha" type="password" placeholder="Confirmar senha" required>
        <button class="btn btn-pri" type="submit">Ativar</button>
      </form>
    </div>
    """
    return page(html_content, title="AP360 | Ativar conta", inv=inv)


# =========================================================
# ADMIN
# =========================================================
@app.route("/admin")
@login_required
@admin_required
def admin_panel():
    reqs = AccessRequest.query.filter_by(status="pendente").order_by(AccessRequest.criado_em.desc()).all()
    invites = AccessInvite.query.filter_by(status="convidado").order_by(AccessInvite.criado_em.desc()).all()
    users = User.query.order_by(User.criado_em.desc()).all()
    benches = CoopBenchmark.query.order_by(CoopBenchmark.cadeia, CoopBenchmark.cooperativa).all()

    html_content = """
    <h2>Painel Administrativo</h2>

    <div class="card">
      <h3>Solicitações de Acesso Pendentes</h3>
      <table>
        <tr><th>Nome</th><th>E-mail</th><th>Segmento</th><th>Cooperativa</th><th>Ações</th></tr>
        {% for r in reqs %}
          <tr>
            <td>{{ r.nome }}</td>
            <td>{{ r.email }}</td>
            <td>{{ r.segmento|capitalize }}</td>
            <td>{{ r.cooperativa or "Indefinida" }}</td>
            <td>
              <form method="post" action="{{ url_for('admin_approve_request', req_id=r.id) }}" style="display:inline;">
                <button type="submit" class="btn btn-ok">Aprovar</button>
              </form>
              <form method="post" action="{{ url_for('admin_deny_request', req_id=r.id) }}" style="display:inline;">
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
        <tr><th>E-mail</th><th>Token</th><th>Ações</th></tr>
        {% for i in invites %}
          <tr>
            <td>{{ i.email }}</td>
            <td>{{ i.token }}</td>
            <td>
              <form method="post" action="{{ url_for('admin_revoke_invite', invite_id=i.id) }}" style="display:inline;">
                <button type="submit" class="btn btn-ghost">Revogar</button>
              </form>
            </td>
          </tr>
        {% endfor %}
      </table>
    </div>

    <div class="card">
      <h3>Usuários Ativos</h3>
      <table>
        <tr><th>Nome</th><th>E-mail</th><th>Perfil</th><th>Segmento</th><th>Cooperativa</th><th>Status</th><th>Ações</th></tr>
        {% for u in users %}
          <tr>
            <td>{{ u.nome }}</td>
            <td>{{ u.email }}</td>
            <td>{{ u.perfil|capitalize }}</td>
            <td>{{ u.segmento|capitalize }}</td>
            <td>{{ u.cooperativa or "Indefinida" }}</td>
            <td>{{ u.status|capitalize }}</td>
            <td>
              {% if u.perfil != "admin" %}
                <form method="post" action="{{ url_for('admin_toggle_user_status', user_id=u.id) }}" style="display:inline;">
                  <button type="submit" class="btn btn-ghost">
                    {% if u.status == "ativo" %}Bloquear{% else %}Ativar{% endif %}
                  </button>
                </form>
                <form method="post" action="{{ url_for('admin_delete_user', user_id=u.id) }}" style="display:inline;">
                  <button type="submit" class="btn btn-ghost" onclick="return confirm('Tem certeza que deseja excluir este usuário?');">Excluir</button>
                </form>
              {% endif %}
            </td>
          </tr>
        {% endfor %}
      </table>
    </div>

    <div class="card">
      <h3>Benchmarks de Cooperativas</h3>
      <form method="post">
        <input type="hidden" name="form_type" value="novo_benchmark">
        <select name="cadeia" required>
          <option value="avicultura">Avicultura</option>
          <option value="suinocultura">Suinocultura</option>
        </select>
        <input name="cooperativa" placeholder="Nome da Cooperativa" required>
        <input type="number" step="0.001" name="media_gpd" placeholder="Média GPD" required>
        <input type="number" step="0.01" name="media_ca" placeholder="Média CA" required>
        <input type="number" step="0.01" name="bonus_base" placeholder="Bônus Base" required>
        <button class="btn btn-pri" type="submit">Adicionar Benchmark</button>
      </form>
      <table>
        <tr><th>Cadeia</th><th>Cooperativa</th><th>Média GPD</th><th>Média CA</th><th>Bônus Base</th><th>Ações</th></tr>
        {% for b in benches %}
          <tr>
            <td>{{ b.cadeia|capitalize }}</td>
            <td>{{ b.cooperativa }}</td>
            <td>{{ b.media_gpd }}</td>
            <td>{{ b.media_ca }}</td>
            <td>R$ {{ b.bonus_base }}</td>
            <td>
              <form method="post" action="{{ url_for('admin_delete_benchmark', bench_id=b.id) }}" style="display:inline;">
                <button type="submit" class="btn btn-ghost" onclick="return confirm('Tem certeza que deseja excluir este benchmark?');">Excluir</button>
              </form>
            </td>
          </tr>
        {% endfor %}
      </table>
    </div>
    """
    return page(html_content, title="AP360 | Admin", reqs=reqs, invites=invites, users=users, benches=benches)


@app.route("/admin/approve_request/<int:req_id>", methods=["POST"])
@login_required
@admin_required
def admin_approve_request(req_id):
    req = AccessRequest.query.get_or_404(req_id)
    token = secrets.token_urlsafe(32)
    inv = AccessInvite(email=req.email, token=token, request_id=req.id)
    db.session.add(inv)
    req.status = "liberado"
    db.session.commit()
    flash(f"Solicitação de {req.email} aprovada. Convite gerado.")
    return redirect(url_for("admin_panel"))


@app.route("/admin/deny_request/<int:req_id>", methods=["POST"])
@login_required
@admin_required
def admin_deny_request(req_id):
    req = AccessRequest.query.get_or_404(req_id)
    req.status = "negado"
    db.session.commit()
    flash(f"Solicitação de {req.email} negada.")
    return redirect(url_for("admin_panel"))


@app.route("/admin/revoke_invite/<int:invite_id>", methods=["POST"])
@login_required
@admin_required
def admin_revoke_invite(invite_id):
    inv = AccessInvite.query.get_or_404(invite_id)
    db.session.delete(inv)
    db.session.commit()
    flash(f"Convite para {inv.email} revogado.")
    return redirect(url_for("admin_panel"))


@app.route("/admin/toggle_user_status/<int:user_id>", methods=["POST"])
@login_required
@admin_required
def admin_toggle_user_status(user_id):
    user = User.query.get_or_404(user_id)
    if user.perfil == "admin":
        flash("Não é possível alterar o status de um administrador.")
    else:
        user.status = "bloqueado" if user.status == "ativo" else "ativo"
        db.session.commit()
        flash(f"Status do usuário {user.email} alterado para {user.status}.")
    return redirect(url_for("admin_panel"))


@app.route("/admin/delete_user/<int:user_id>", methods=["POST"])
@login_required
@admin_required
def admin_delete_user(user_id):
    user = User.query.get_or_404(user_id)
    if user.perfil == "admin":
        flash("Não é possível excluir um administrador.")
    else:
        # Excluir todos os registros relacionados ao usuário
        AgricultureQuote.query.filter_by(user_id=user.id).delete()
        Batch.query.filter_by(user_id=user.id).delete()
        Bovino.query.filter_by(user_id=user.id).delete() # Isso também excluirá Pesos e Eventos via CASCADE se configurado, ou precisaria ser manual
        db.session.delete(user)
        db.session.commit()
        flash(f"Usuário {user.email} e todos os seus dados excluídos.")
    return redirect(url_for("admin_panel"))


@app.route("/admin/add_benchmark", methods=["POST"])
@login_required
@admin_required
def admin_add_benchmark():
    form_type = request.form.get("form_type")
    if form_type == "novo_benchmark":
        cadeia = request.form.get("cadeia", "").strip()
        cooperativa = request.form.get("cooperativa", "").strip()
        media_gpd = float(request.form.get("media_gpd", 0))
        media_ca = float(request.form.get("media_ca", 0))
        bonus_base = float(request.form.get("bonus_base", 0))

        existing_bench = CoopBenchmark.query.filter_by(cadeia=cadeia, cooperativa=cooperativa).first()
        if existing_bench:
            flash(f"Benchmark para {cadeia.capitalize()} da cooperativa {cooperativa} já existe. Atualizado.")
            existing_bench.media_gpd = media_gpd
            existing_bench.media_ca = media_ca
            existing_bench.bonus_base = bonus_base
        else:
            db.session.add(CoopBenchmark(
                cadeia=cadeia, cooperativa=cooperativa,
                media_gpd=media_gpd, media_ca=media_ca, bonus_base=bonus_base
            ))
            flash(f"Benchmark para {cadeia.capitalize()} da cooperativa {cooperativa} adicionado.")
        db.session.commit()
    return redirect(url_for("admin_panel"))


@app.route("/admin/delete_benchmark/<int:bench_id>", methods=["POST"])
@login_required
@admin_required
def admin_delete_benchmark(bench_id):
    bench = CoopBenchmark.query.get_or_404(bench_id)
    db.session.delete(bench)
    db.session.commit()
    flash(f"Benchmark para {bench.cadeia.capitalize()} da cooperativa {bench.cooperativa} excluído.")
    return redirect(url_for("admin_panel"))


# =========================================================
# DASHBOARD
# =========================================================
@app.route("/dashboard")
@login_required
def dashboard():
    html_content = f"""
    <h2>Dashboard</h2>
    <div class="card">
      <p>Bem-vindo(a), <span class="welcome-name">{current_user.nome.split(' ')[0]}</span>!</p>
      <p>Seu perfil: <b>{current_user.perfil.capitalize()}</b></p>
      <p>Segmento: <b>{current_user.segmento.capitalize() if current_user.segmento else 'Não definido'}</b></p>
      <p>Cooperativa: <b>{current_user.cooperativa or 'Indefinida'}</b></p>
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
        quantidade_ton = float(request.form.get("quantidade_ton", 0))
        origem = request.form.get("origem", "").strip()
        porto = request.form.get("porto", "").strip()

        rs_ton, usd_bushel = cbot_para_rs_ton(produto)
        frete = frete_medio(origem, porto)
        liquido_rs_ton = rs_ton - frete
        total_rs = liquido_rs_ton * quantidade_ton

        quote = AgricultureQuote(
            user_id=current_user.id,
            produto=produto,
            quantidade_ton=quantidade_ton,
            origem=origem,
            porto=porto,
            cbot_usd_bushel=usd_bushel,
            usd_brl=fx_usd_brl(),
            export_rs_ton=rs_ton,
            frete_rs_ton=frete,
            liquido_rs_ton=liquido_rs_ton,
            total_rs=total_rs
        )
        db.session.add(quote)
        db.session.commit()
        flash("Cotação registrada com sucesso!")
        return redirect(url_for("agricultura"))

    quotes = AgricultureQuote.query.filter_by(user_id=current_user.id).order_by(AgricultureQuote.criado_em.desc()).all()

    html_content = """
    <h2>Agricultura</h2>

    <div class="card">
      <h3>Nova Cotação</h3>
      <form method="post">
        <select name="produto" required>
          <option value="soja">Soja</option>
          <option value="milho">Milho</option>
          <option value="trigo">Trigo</option>
          <option value="aveia">Aveia</option>
          <option value="arroz">Arroz</option>
        </select>
        <input type="number" step="0.01" name="quantidade_ton" placeholder="Quantidade (ton)" required>
        <input name="origem" placeholder="Origem (Ex: Cascavel-PR)" required>
        <select name="porto" required>
          <option value="Paranaguá">Paranaguá</option>
          <option value="Santos">Santos</option>
          <option value="Rio Grande">Rio Grande</option>
          <option value="Itajaí">Itajaí</option>
        </select>
        <button class="btn btn-pri" type="submit">Calcular e Salvar</button>
      </form>
    </div>

    <div class="card">
      <h3>Histórico de Cotações</h3>
      <table>
        <tr>
          <th>Data</th><th>Produto</th><th>Origem</th><th>Porto</th><th>CBOT (USD/bu)</th>
          <th>USD/BRL</th><th>Export (R$/ton)</th><th>Frete (R$/ton)</th>
          <th>Líquido (R$/ton)</th><th>Total (R$)</th>
        </tr>
        {% for q in quotes %}
        <tr>
          <td>{{ q.criado_em.strftime("%d/%m %H:%M") }}</td>
          <td>{{ q.produto|capitalize }}</td>
          <td>{{ q.origem }}</td>
          <td>{{ q.porto }}</td>
          <td>{{ q.cbot_usd_bushel }}</td>
          <td>{{ q.usd_brl }}</td>
          <td>{{ q.export_rs_ton }}</td>
          <td>{{ q.frete_rs_ton }}</td>
          <td>{{ q.liquido_rs_ton }}</td>
          <td>{{ q.total_rs }}</td>
        </tr>
        {% endfor %}
      </table>
    </div>
    """
    return page(html_content, title="AP360 | Agricultura")


# =========================================================
# AVICULTURA / SUINOCULTURA (Módulo Genérico de Lotes)
# =========================================================
@app.route("/<string:cadeia>", methods=["GET", "POST"])
@login_required
def modulo_lotes(cadeia):
    if cadeia not in ["avicultura", "suinocultura"]:
        abort(404)

    if request.method == "POST":
        form_type = request.form.get("form_type")

        if form_type == "novo_lote":
            peso_inicial = float(request.form.get("peso_inicial", 0))
            peso_final = float(request.form.get("peso_final", 0))
            dias = int(request.form.get("dias", 0))
            racao_total_kg = float(request.form.get("racao_total_kg", 0))
            animais_iniciais = int(request.form.get("animais_iniciais", 0) or 0)
            animais_final = int(request.form.get("animais_final", 0) or 0)

            gpd = calc_gpd(peso_inicial, peso_final, dias)
            ca = calc_ca(racao_total_kg, peso_inicial, peso_final)
            viabilidade_pct = calc_viabilidade(animais_iniciais, animais_final)
            mortalidade_pct = calc_mortalidade(animais_iniciais, animais_final)

            # Benchmarks do produtor (se informados, têm prioridade)
            gpd_produtor = float(request.form.get(f"gpd_produtor_{cadeia}", 0) or 0)
            ca_produtor = float(request.form.get(f"ca_produtor_{cadeia}", 0) or 0)

            # Atualiza os benchmarks do produtor no modelo User
            if cadeia == "avicultura":
                current_user.gpd_produtor_avicultura = gpd_produtor
                current_user.ca_produtor_avicultura = ca_produtor
            elif cadeia == "suinocultura":
                current_user.gpd_produtor_suinocultura = gpd_produtor
                current_user.ca_produtor_suinocultura = ca_produtor
            db.session.add(current_user) # Adiciona o current_user para salvar as alterações
            db.session.commit() # Commit aqui para garantir que os benchmarks do user sejam salvos antes de get_benchmark

            # Pega os benchmarks (priorizando os do produtor)
            meta_gpd, meta_ca, bonus_base = get_benchmark(cadeia, current_user)

            ca_ajustada = ca # Default
            iep = 0.0
            indice_lote = 0.0
            peso_vivo_medio = 0.0
            peso_carcaca_medio = 0.0
            rendimento_carcaca_pct = 0.0
            carne_magra_pct = 0.0
            bonus_tipificacao = 0.0

            if cadeia == "avicultura":
                peso_meta_coop = float(request.form.get("peso_meta_coop", 0) or 0)
                idade_meta_coop = int(request.form.get("idade_meta_coop", 0) or 0)
                fator_peso_caa = float(request.form.get("fator_peso_caa", 0.30) or 0.30)
                fator_idade_caa = float(request.form.get("fator_idade_caa", 0.01) or 0.01)

                if peso_meta_coop > 0 and idade_meta_coop > 0:
                    ca_ajustada = calc_ca_ajustada_avicultura(
                        ca_observada=ca,
                        peso_real=peso_final,
                        idade_real=dias,
                        peso_meta=peso_meta_coop,
                        idade_meta=idade_meta_coop,
                        fator_peso=fator_peso_caa,
                        fator_idade=fator_idade_caa
                    )
                iep = calc_iep_avicultura(viabilidade_pct, peso_final, dias, ca_ajustada)

            if cadeia == "suinocultura":
                peso_vivo_medio = float(request.form.get("peso_vivo_medio", 0) or 0)
                peso_carcaca_medio = float(request.form.get("peso_carcaca_medio", 0) or 0)
                carne_magra_pct = float(request.form.get("carne_magra_pct", 0) or 0)

                rendimento_carcaca_pct = calc_rendimento_carcaca(peso_vivo_medio, peso_carcaca_medio)

                if peso_vivo_medio > 0:
                    ajuste_peso = 0.003 * (peso_vivo_medio - 120.0)
                    ca_ajustada = round(max(ca + ajuste_peso, 0.01), 4)

                indice_lote = calc_indice_lote_suino(gpd, viabilidade_pct, ca_ajustada)
                bonus_tipificacao = calc_bonus_tipificacao(carne_magra_pct, rendimento_carcaca_pct)

            bonificacao = calc_bonificacao(gpd, ca_ajustada, meta_gpd, meta_ca, bonus_base)
            bonificacao = round(bonificacao + bonus_tipificacao, 2)

            batch = Batch(
                user_id=current_user.id,
                cadeia=cadeia,
                estrutura=request.form.get("estrutura", "").strip(),
                lote=request.form.get("lote", "").strip(),
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
                ca_coop_ref=float(request.form.get("ca_coop_ref", 0) or 0),
                gpd_coop_ref=float(request.form.get("gpd_coop_ref", 0) or 0),
                peso_meta_coop=peso_meta_coop,
                idade_meta_coop=idade_meta_coop,
                fator_peso_caa=fator_peso_caa,
                fator_idade_caa=fator_idade_caa,
                iep=iep,
                indice_lote=indice_lote,
                peso_vivo_medio=peso_vivo_medio,
                peso_carcaca_medio=peso_carcaca_medio,
                rendimento_carcaca_pct=rendimento_carcaca_pct,
                carne_magra_pct=carne_magra_pct,
                bonus_tipificacao=bonus_tipificacao,
                bonificacao=bonificacao,
                coop_media_gpd=meta_gpd,
                coop_media_ca=meta_ca
            )
            db.session.add(batch)
            db.session.commit()
            flash(f"Lote de {cadeia} registrado com sucesso!")
            return redirect(url_for(cadeia))

    hist = Batch.query.filter_by(user_id=current_user.id, cadeia=cadeia).order_by(Batch.criado_em.desc()).all()
    resultado = hist[0] if hist else None

    c1 = request.args.get("c1", type=int)
    c2 = request.args.get("c2", type=int)
    compare_data = None

    if c1 and c2:
        batch1 = Batch.query.get(c1)
        batch2 = Batch.query.get(c2)
        if batch1 and batch2 and batch1.user_id == current_user.id and batch2.user_id == current_user.id:
            labels = ["GPD", "CA", "Viabilidade%", "Mortalidade%"]
            a_vals = [batch1.gpd, batch1.ca, batch1.viabilidade_pct, batch1.mortalidade_pct]
            b_vals = [batch2.gpd, batch2.ca, batch2.viabilidade_pct, batch2.mortalidade_pct]
            if cadeia == "avicultura":
                labels.append("IEP")
                a_vals.append(batch1.iep)
                b_vals.append(batch2.iep)
            elif cadeia == "suinocultura":
                labels.append("Índice Lote")
                a_vals.append(batch1.indice_lote)
                b_vals.append(batch2.indice_lote)

            compare_data = {
                "labels": labels,
                "a_name": f"{batch1.estrutura}/{batch1.lote}",
                "a_vals": a_vals,
                "b_name": f"{batch2.estrutura}/{batch2.lote}",
                "b_vals": b_vals
            }

    html_content = f"""
    <h2>{cadeia.capitalize()}</h2>

    <div class="card">
      <h3>Novo Lote</h3>
      <form method="post">
        <input type="hidden" name="form_type" value="novo_lote">
        <input name="estrutura" placeholder="Estrutura (Ex: Galpão 1)" required>
        <input name="lote" placeholder="Lote (Ex: Lote 001)" required>
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
          <input type="number" step="0.0001" name="gpd_produtor_avicultura" value="{current_user.gpd_produtor_avicultura if current_user.gpd_produtor_avicultura > 0 else ''}">
          <label>Sua CA média ideal (Avicultura)</label>
          <input type="number" step="0.0001" name="ca_produtor_avicultura" value="{current_user.ca_produtor_avicultura if current_user.ca_produtor_avicultura > 0 else ''}">
        {% elif cadeia == 'suinocultura' %}
          <label>Seu GPD médio ideal (Suinocultura)</label>
          <input type="number" step="0.0001" name="gpd_produtor_suinocultura" value="{current_user.gpd_produtor_suinocultura if current_user.gpd_produtor_suinocultura > 0 else ''}">
          <label>Sua CA média ideal (Suinocultura)</label>
          <input type="number" step="0.0001" name="ca_produtor_suinocultura" value="{current_user.ca_produtor_suinocultura if current_user.ca_produtor_suinocultura > 0 else ''}">
        {% endif %}

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

        <button class="btn btn-pri" type="submit">Calcular e Salvar</button>
      </form>
    </div>

    {% if resultado %}
      <div class="card">
        <h3>Último Lote Registrado ({{ resultado.estrutura }} / {{ resultado.lote }})</h3>
        <div class="grid3">
          <div><div class="muted">GPD</div><div class="kpi">{{ resultado.gpd }}</div></div>
          <div><div class="muted">CA</div><div class="kpi">{{ resultado.ca }}</div></div>
          <div><div class="muted">CA Ajustada</div><div class="kpi">{{ resultado.ca_ajustada }}</div></div>
        </div>
        <div class="grid3">
          <div><div class="muted">Viabilidade</div><div class="kpi">{{ resultado.viabilidade_pct }}%</div></div>
          <div><div class="muted">Mortalidade</div><div class="kpi">{{ resultado.mortalidade_pct }}%</div></div>
          <div><div class="muted">Bonificação</div><div class="kpi">R$ {{ resultado.bonificacao }}</div></div>
        </div>
        {% if cadeia == 'avicultura' %}
        <div class="grid3">
          <div><div class="muted">IEP</div><div class="kpi">{{ resultado.iep }}</div></div>
          <div><div class="muted">Peso meta coop</div><div class="kpi">{{ resultado.peso_meta_coop }}</div></div>
          <div><div class="muted">Idade meta coop</div><div class="kpi">{{ resultado.idade_meta_coop }}</div></div>
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
                cadeia=cadeia, resultado=resultado, hist=hist, compare_data=compare_data, c1=c1, c2=c2,
                current_user=current_user) # Passa current_user para o template


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

        # Benchmarks do produtor (se informados, têm prioridade)
        gpd_produtor = float(request.form.get(f"gpd_produtor_{cadeia}", 0) or 0)
        ca_produtor = float(request.form.get(f"ca_produtor_{cadeia}", 0) or 0)

        # Atualiza os benchmarks do produtor no modelo User
        if cadeia == "avicultura":
            current_user.gpd_produtor_avicultura = gpd_produtor
            current_user.ca_produtor_avicultura = ca_produtor
        elif cadeia == "suinocultura":
            current_user.gpd_produtor_suinocultura = gpd_produtor
            current_user.ca_produtor_suinocultura = ca_produtor
        db.session.add(current_user) # Adiciona o current_user para salvar as alterações
        db.session.commit() # Commit aqui para garantir que os benchmarks do user sejam salvos antes de get_benchmark

        # Pega os benchmarks (priorizando os do produtor)
        meta_gpd, meta_ca, bonus_base = get_benchmark(cadeia, current_user)

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
        batch.coop_media_gpd = meta_gpd # Armazena o benchmark efetivamente usado
        batch.coop_media_ca = meta_ca # Armazena o benchmark efetivamente usado

        db.session.add(batch)
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
    return page(html_content, title=f"AP360 | Editar Lote {cadeia.capitalize()}", batch=batch, cadeia=cadeia,
                current_user=current_user) # Passa current_user para o template


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
def bovinocultura_index(): # Rota para a lista de animais
    if request.method == "POST":
        form_type = request.form.get("form_type")

        if form_type == "novo_bovino":
            brinco = request.form.get("brinco", "").strip()
            if Bovino.query.filter_by(brinco=brinco, user_id=current_user.id).first():
                flash("Brinco já cadastrado para este usuário.")
                return redirect(url_for("bovinocultura_index"))

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
            return redirect(url_for("bovinocultura_index"))

    animais = Bovino.query.filter_by(user_id=current_user.id).order_by(Bovino.criado_em.desc()).all()

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
                <a class="btn btn-ghost" href="{{ url_for('bovinocultura_ficha', animal_id=a.id) }}">Ficha</a>
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
    """
    return page(html_content, title="AP360 | Bovinocultura", animais=animais)


@app.route("/bovinocultura/ficha/<int:animal_id>", methods=["GET", "POST"])
@login_required
def bovinocultura_ficha(animal_id):
    animal = Bovino.query.filter_by(id=animal_id, user_id=current_user.id).first_or_404()

    if request.method == "POST":
        form_type = request.form.get("form_type")

        if form_type == "novo_peso":
            data = request.form.get("data", "")
            peso = float(request.form.get("peso", 0))
            db.session.add(BovinoPeso(bovino_id=animal.id, data=data, peso=peso))
            animal.peso_atual = peso
            animal.ultima_pesagem = data
            db.session.commit()
            flash("Pesagem registrada.")
            return redirect(url_for("bovinocultura_ficha", animal_id=animal.id))

        if form_type == "novo_evento":
            db.session.add(BovinoEvento(
                bovino_id=animal.id,
                tipo=request.form.get("tipo", "manejo"),
                descricao=request.form.get("descricao", ""),
                data=request.form.get("data", "")
            ))
            db.session.commit()
            flash("Evento registrado.")
            return redirect(url_for("bovinocultura_ficha", animal_id=animal.id))

    pesos = BovinoPeso.query.filter_by(bovino_id=animal.id).order_by(BovinoPeso.data.asc()).all()
    eventos = BovinoEvento.query.filter_by(bovino_id=animal.id).order_by(BovinoEvento.data.desc()).all()
    chart = {"labels": [p.data for p in pesos], "vals": [p.peso for p in pesos]} if pesos else None

    html_content = """
    <h2>Bovinocultura - Ficha do Animal</h2>

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
    """
    return page(html_content, title=f"AP360 | Ficha {animal.brinco}", animal=animal, pesos=pesos, eventos=eventos, chart=chart)


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
        return redirect(url_for("bovinocultura_ficha", animal_id=bovino.id))

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
        <a class="btn btn-ghost" href="{{ url_for('bovinocultura_ficha', animal_id=bovino.id) }}">Cancelar</a>
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
    return redirect(url_for("bovinocultura_index"))


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
    return redirect(url_for("bovinocultura_ficha", animal_id=bovino_id))


@app.route("/bovinocultura/evento/excluir/<int:evento_id>", methods=["POST"])
@login_required
def excluir_evento(evento_id):
    evento_registro = BovinoEvento.query.get_or_404(evento_id)
    bovino_id = evento_registro.bovino_id
    bovino = Bovino.query.filter_by(id=bovino_id, user_id=current_user.id).first_or_404()

    db.session.delete(evento_registro)
    db.session.commit()
    flash("Registro de evento excluído com sucesso!")
    return redirect(url_for("bovinocultura_ficha", animal_id=bovino_id))


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
    return render_template("base.html", content="<div class='card'><h2>403</h2><p>Acesso negado.</p></div>", title="403"), 403


@app.errorhandler(404)
def e404(_):
    return render_template("base.html", content="<div class='card'><h2>404</h2><p>Página não encontrada.</p></div>", title="404"), 404


@app.errorhandler(500)
def e500(_):
    return render_template("base.html", content="<div class='card'><h2>500</h2><p>Erro interno do servidor.</p></div>", title="500"), 500


# =========================================================
# RUN
# =========================================================
if __name__ == "__main__":
    app.run(debug=True)