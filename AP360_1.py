import os
import io
import csv
import secrets
from datetime import datetime, timedelta
from functools import wraps
import pytz # Para lidar com fusos horários

from flask import (
    Flask, request, redirect, url_for, flash,
    render_template, jsonify, Response, abort # render_template agora carrega de arquivos
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

# Configuração do fuso horário de Brasília
BRASILIA_TZ = pytz.timezone("America/Sao_Paulo")


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

    # Novos campos para benchmarks pessoais do usuário
    user_avicultura_gpd = db.Column(db.Float, default=0.0)
    user_avicultura_ca = db.Column(db.Float, default=0.0)
    user_avicultura_bonus_base = db.Column(db.Float, default=0.0)

    user_suinocultura_gpd = db.Column(db.Float, default=0.0)
    user_suinocultura_ca = db.Column(db.Float, default=0.0)
    user_suinocultura_bonus_base = db.Column(db.Float, default=0.0)


    def set_password(self, raw: str):
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw: str):
        return check_password_hash(self.password_hash, raw)

    def is_active(self):
        return self.status == "ativo"


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
    status = db.Column(db.String(20), default="convidado")  # convidado/ativado/revogado
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

    # Base produtiva (agora peso_inicial e peso_final são MÉDIOS por animal)
    peso_inicial = db.Column(db.Float, nullable=False) # Peso médio por animal no início
    peso_final = db.Column(db.Float, nullable=False)   # Peso médio por animal no final

    # Novos campos para data/hora de alojamento e carregamento/abate
    data_alojamento = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    data_carregamento = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    dias = db.Column(db.Float, nullable=False) # Agora float para incluir vírgula

    racao_total_kg = db.Column(db.Float, nullable=False) # Ração TOTAL do lote

    # Plantel/lote
    animais_iniciais = db.Column(db.Integer, default=0)
    animais_final = db.Column(db.Integer, default=0)
    viabilidade_pct = db.Column(db.Float, default=0.0)
    mortalidade_pct = db.Column(db.Float, default=0.0)

    # Indicadores clássicos
    gpd = db.Column(db.Float, nullable=False) # GPD por animal
    ca = db.Column(db.Float, nullable=False)  # CA do lote
    ca_ajustada = db.Column(db.Float, default=0.0)

    # Referências cooperativa (manual)
    ca_coop_ref = db.Column(db.Float, default=0.0)
    gpd_coop_ref = db.Column(db.Float, default=0.0)

    # Parâmetros CAA (avicultura)
    peso_meta_coop = db.Column(db.Float, default=0.0)
    idade_meta_coop = db.Column(db.Float, default=0.0) # Agora float para incluir vírgula
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


# NOVO MODELO: BatchDailyRecord para acompanhamento diário/semanal de lotes
class BatchDailyRecord(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    batch_id = db.Column(db.Integer, db.ForeignKey("batch.id"), nullable=False)
    data_registro = db.Column(db.Date, nullable=False)
    peso_medio = db.Column(db.Float, default=0.0)
    consumo_racao_dia = db.Column(db.Float, default=0.0) # Consumo no dia
    mortalidade_dia = db.Column(db.Integer, default=0) # Mortalidade no dia
    observacoes = db.Column(db.Text, default="")
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint('batch_id', 'data_registro', name='_batch_daily_record_uc'),)


class Bovino(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

    brinco = db.Column(db.String(40), nullable=False) # Removido unique=True para permitir brincos iguais entre usuários
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

    __table_args__ = (db.UniqueConstraint('user_id', 'brinco', name='_user_brinco_uc'),) # Garante brinco único por usuário


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


# NOVO MODELO: AgricultureField para campos de agricultura
class AgricultureField(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    nome_campo = db.Column(db.String(120), nullable=False)
    cultura = db.Column(db.String(80), nullable=False)
    area_ha = db.Column(db.Float, nullable=False)
    data_plantio = db.Column(db.Date, nullable=False)
    data_colheita_prevista = db.Column(db.Date, nullable=True)
    produtividade_esperada_ton_ha = db.Column(db.Float, default=0.0)
    observacoes = db.Column(db.Text, default="")
    status = db.Column(db.String(30), default="plantado") # plantado/crescendo/colhendo/colhido
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint('user_id', 'nome_campo', name='_user_field_uc'),)


# NOVO MODELO: AgricultureDailyRecord para registros diários de campos
class AgricultureDailyRecord(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    field_id = db.Column(db.Integer, db.ForeignKey("agriculture_field.id"), nullable=False)
    data_registro = db.Column(db.Date, nullable=False)
    chuva_mm = db.Column(db.Float, default=0.0)
    temperatura_c = db.Column(db.Float, default=0.0)
    insumo_aplicado = db.Column(db.String(120), nullable=True)
    quantidade_insumo = db.Column(db.Float, default=0.0)
    produtividade_parcial_ton_ha = db.Column(db.Float, default=0.0) # Para colheitas parciais
    observacoes = db.Column(db.Text, default="")
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint('field_id', 'data_registro', name='_field_daily_record_uc'),)


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


# --- FUNÇÕES DE CÁLCULO ATUALIZADAS ---
def calc_gpd(peso_inicial_medio: float, peso_final_medio: float, dias: float) -> float:
    """Calcula o Ganho de Peso Diário (GPD) por animal."""
    if dias <= 0:
        return 0.0
    return round((peso_final_medio - peso_inicial_medio) / dias, 4)


def calc_ca(racao_total_lote: float, peso_inicial_medio: float, peso_final_medio: float, animais_final: int) -> float:
    """Calcula a Conversão Alimentar (CA) para o lote."""
    ganho_peso_total_lote = (peso_final_medio - peso_inicial_medio) * animais_final
    if ganho_peso_total_lote <= 0: # Evita divisão por zero ou CA negativa/infinita
        return 0.0
    return round(racao_total_lote / ganho_peso_total_lote, 4)
# --- FIM DAS FUNÇÕES DE CÁLCULO ATUALIZADAS ---


def calc_viabilidade(animais_iniciais: int, animais_final: int) -> float:
    if animais_iniciais <= 0:
        return 0.0
    return round((animais_final / animais_iniciais) * 100.0, 2)


def calc_mortalidade(animais_iniciais: int, animais_final: int) -> float:
    if animais_iniciais <= 0:
        return 0.0
    mortos = max(0, animais_iniciais - animais_final)
    return round((mortos / animais_iniciais) * 100.0, 2)


def calc_ca_ajustada_avicultura(ca_observada: float, peso_real: float, idade_real: float,
                                peso_meta: float, idade_meta: float,
                                fator_peso: float = 0.30, fator_idade: float = 0.01) -> float:
    """
    Calcula a CA Ajustada para avicultura.
    peso_real: peso final médio do lote.
    idade_real: dias de alojamento do lote (pode ter vírgula).
    """
    caa = ca_observada + (fator_peso * (peso_meta - peso_real)) + (fator_idade * (idade_real - idade_meta))
    return round(max(caa, 0.01), 4)


def calc_iep_avicultura(viabilidade_pct: float, peso_medio: float, idade_dias: float, ca_ajustada: float) -> float:
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


def get_effective_benchmark(cadeia: str, cooperativa: str, user: User):
    # 1. Tenta pegar os benchmarks pessoais do usuário
    if cadeia == "avicultura" and user.user_avicultura_gpd > 0 and user.user_avicultura_ca > 0:
        return user.user_avicultura_gpd, user.user_avicultura_ca, user.user_avicultura_bonus_base
    elif cadeia == "suinocultura" and user.user_suinocultura_gpd > 0 and user.user_suinocultura_ca > 0:
        return user.user_suinocultura_gpd, user.user_suinocultura_ca, user.user_suinocultura_bonus_base

    # 2. Se não tiver pessoal, tenta pegar da cooperativa
    if cooperativa:
        row = CoopBenchmark.query.filter_by(cadeia=cadeia, cooperativa=cooperativa).first()
        if row:
            return row.media_gpd or 0.065, row.media_ca or 1.70, row.bonus_base or 1000.0

    # 3. Se nada disso, usa os padrões globais
    # Padrões globais para avicultura e suinocultura
    if cadeia == "avicultura":
        return 0.065, 1.70, 1000.0
    elif cadeia == "suinocultura":
        return 0.72, 2.45, 1200.0
    return 0.0, 0.0, 0.0 # Caso a cadeia não seja reconhecida


# =========================================================
# UI BASE (Agora renderiza um template de arquivo)
# =========================================================
def page(content_html, **kwargs):
    """Renderiza o template base.html com o conteúdo e variáveis passadas."""
    return render_template("base.html", content=content_html, **kwargs)


# =========================================================
# ROUTES
# =========================================================
@app.route("/")
def index():
    html_content = """
    <div class="hero">
      <h2>Bem-vindo ao AP360 - AgroPulse 360!</h2>
      <p>Sua plataforma completa para gestão e otimização da produção agrícola e pecuária.</p>
      <p>Com o AP360, você transforma dados em decisões inteligentes, acompanhando seus lotes e campos em tempo real, analisando indicadores de performance e maximizando seus resultados.</p>
      <p><b>Funcionalidades:</b></p>
      <ul>
        <li>Acompanhamento detalhado de lotes de avicultura e suinocultura, com indicadores de GPD, CA, CAA e bonificação.</li>
        <li>Gestão de campos agrícolas, com registros diários de clima, insumos e produtividade.</li>
        <li>Simulações "Agrosim" para projeção de resultados na agricultura.</li>
        <li>Módulo de bovinocultura para registro de animais, pesagens e eventos.</li>
        <li>Assistente de IA para tirar dúvidas e fornecer dicas de manejo.</li>
        <li>Benchmarks personalizados e da cooperativa para comparar sua performance.</li>
      </ul>
      <p>Junte-se a nós e leve sua produção para o próximo nível!</p>
      <p>Para mais informações ou suporte, entre em contato via WhatsApp: <a href="https://wa.me/5545999999999" target="_blank" class="btn btn-ok">Fale Conosco no WhatsApp!</a></p>
    </div>
    """
    return page(html_content, title="AP360 | Início")


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        email = request.form.get("email").strip().lower()
        password = request.form.get("password")
        user = User.query.filter_by(email=email).first()

        if user and user.check_password(password):
            if user.is_active():
                login_user(user)
                flash(f"Bem-vindo, {user.nome}!")
                return redirect(url_for("dashboard"))
            else:
                flash("Sua conta está bloqueada. Entre em contato com o suporte.")
        else:
            flash("Email ou senha inválidos.")

    html_content = """
    <h2>Login</h2>
    <div class="card" style="max-width:400px;margin:0 auto">
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
    if request.method == "POST":
        email = request.form.get("email").strip().lower()
        # Verifica se o email já está em uso
        if User.query.filter_by(email=email).first():
            flash("Este email já está cadastrado. Tente fazer login.")
            return redirect(url_for("login"))
        if AccessRequest.query.filter_by(email=email, status="pendente").first():
            flash("Já existe uma solicitação de acesso pendente para este email.")
            return redirect(url_for("signup_request"))

        new_request = AccessRequest(
            nome=request.form.get("nome").strip(),
            cpf=request.form.get("cpf").strip(),
            telefone=request.form.get("telefone").strip(),
            email=email,
            segmento=request.form.get("segmento"),
            cooperativa=request.form.get("cooperativa").strip()
        )
        db.session.add(new_request)
        db.session.commit()
        flash("Sua solicitação de acesso foi enviada! Aguarde a aprovação do administrador.")
        return redirect(url_for("index"))

    html_content = """
    <h2>Solicitar Acesso</h2>
    <div class="card" style="max-width:500px;margin:0 auto">
      <form method="post">
        <input name="nome" placeholder="Seu Nome Completo" required>
        <input name="cpf" placeholder="CPF (somente números)" pattern="[0-9]{11}" title="CPF deve conter 11 números" required>
        <input name="telefone" placeholder="Telefone (com DDD)" pattern="[0-9]{10,11}" title="Telefone deve conter 10 ou 11 números" required>
        <input type="email" name="email" placeholder="Seu Melhor Email" required>
        <label>Segmento de Atuação</label>
        <select name="segmento" required>
          <option value="agricultura">Agricultura</option>
          <option value="avicultura">Avicultura</option>
          <option value="suinocultura">Suinocultura</option>
          <option value="bovinocultura">Bovinocultura</option>
        </select>
        <input name="cooperativa" placeholder="Nome da Cooperativa (se houver)">
        <button class="btn btn-pri" type="submit">Enviar Solicitação</button>
      </form>
      <p class="muted">Já tem uma conta? <a href="{{ url_for('login') }}">Faça login</a></p>
      <p class="muted">Para mais informações ou suporte, entre em contato via WhatsApp: <a href="https://wa.me/5545999999999" target="_blank">Fale Conosco!</a></p>
    </div>
    """
    return page(html_content, title="AP360 | Solicitar Acesso")


@app.route("/register/<token>", methods=["GET", "POST"])
def register(token):
    invite = AccessInvite.query.filter_by(token=token, status="convidado").first()
    if not invite:
        flash("Token de convite inválido ou expirado.")
        return redirect(url_for("index"))

    if request.method == "POST":
        password = request.form.get("password")
        confirm_password = request.form.get("confirm_password")

        if password != confirm_password:
            flash("As senhas não coincidem.")
            return redirect(url_for("register", token=token))

        # Busca a solicitação original para preencher os dados do usuário
        original_request = AccessRequest.query.get(invite.request_id)
        if not original_request:
            flash("Erro ao encontrar os dados da sua solicitação. Contate o suporte.")
            return redirect(url_for("index"))

        new_user = User(
            nome=original_request.nome,
            cpf=original_request.cpf,
            telefone=original_request.telefone,
            email=invite.email,
            segmento=original_request.segmento,
            cooperativa=original_request.cooperativa
        )
        new_user.set_password(password)
        db.session.add(new_user)

        invite.status = "ativado"
        invite.ativado_em = datetime.utcnow()
        db.session.commit()

        flash("Sua conta foi criada com sucesso! Faça login para começar.")
        return redirect(url_for("login"))

    html_content = f"""
    <h2>Finalizar Cadastro</h2>
    <div class="card" style="max-width:400px;margin:0 auto">
      <p>Bem-vindo(a), <b>{invite.email}</b>! Crie sua senha para ativar sua conta.</p>
      <form method="post">
        <input type="password" name="password" placeholder="Nova Senha" required>
        <input type="password" name="confirm_password" placeholder="Confirmar Senha" required>
        <button class="btn btn-pri" type="submit">Ativar Conta</button>
      </form>
    </div>
    """
    return page(html_content, title="AP360 | Registrar")


@app.route("/dashboard")
@login_required
def dashboard():
    # Exemplo de dados para o dashboard (pode ser expandido)
    total_lotes = Batch.query.filter_by(user_id=current_user.id).count()
    total_campos = AgricultureField.query.filter_by(user_id=current_user.id).count()
    total_bovinos = Bovino.query.filter_by(user_id=current_user.id).count()

    html_content = f"""
    <h2>Dashboard</h2>
    <div class="card">
      <p>Olá, <span class="welcome-name">{current_user.nome}</span>! Seja bem-vindo(a) ao seu painel de controle.</p>
      <p>Aqui você encontra um resumo rápido das suas atividades.</p>
    </div>

    <div class="grid3">
      <div class="card">
        <h3>Lotes de Pecuária</h3>
        <p class="kpi">{{ total_lotes }}</p>
        <p class="muted">Lotes cadastrados</p>
        <a href="{{ url_for('modulo_lotes', cadeia='avicultura') }}" class="btn btn-ghost">Ver Avicultura</a>
        <a href="{{ url_for('modulo_lotes', cadeia='suinocultura') }}" class="btn btn-ghost">Ver Suinocultura</a>
      </div>
      <div class="card">
        <h3>Campos Agrícolas</h3>
        <p class="kpi">{{ total_campos }}</p>
        <p class="muted">Campos cadastrados</p>
        <a href="{{ url_for('agricultura_modulo') }}" class="btn btn-ghost">Ver Agricultura</a>
      </div>
      <div class="card">
        <h3>Bovinos</h3>
        <p class="kpi">{{ total_bovinos }}</p>
        <p class="muted">Animais cadastrados</p>
        <a href="{{ url_for('bovinocultura') }}" class="btn btn-ghost">Ver Bovinocultura</a>
      </div>
    </div>

    <div class="card">
      <h3>Meus Benchmarks Pessoais</h3>
      <form method="post" action="{{ url_for('update_user_benchmarks') }}">
        <h4>Avicultura</h4>
        <label>GPD Médio</label>
        <input type="number" step="0.0001" name="user_avicultura_gpd" value="{{ current_user.user_avicultura_gpd }}" placeholder="Ex: 0.065">
        <label>CA Média</label>
        <input type="number" step="0.0001" name="user_avicultura_ca" value="{{ current_user.user_avicultura_ca }}" placeholder="Ex: 1.70">
        <label>Bônus Base (R$)</label>
        <input type="number" step="0.01" name="user_avicultura_bonus_base" value="{{ current_user.user_avicultura_bonus_base }}" placeholder="Ex: 1000.00">

        <h4>Suinocultura</h4>
        <label>GPD Médio</label>
        <input type="number" step="0.0001" name="user_suinocultura_gpd" value="{{ current_user.user_suinocultura_gpd }}" placeholder="Ex: 0.72">
        <label>CA Média</label>
        <input type="number" step="0.0001" name="user_suinocultura_ca" value="{{ current_user.user_suinocultura_ca }}" placeholder="Ex: 2.45">
        <label>Bônus Base (R$)</label>
        <input type="number" step="0.01" name="user_suinocultura_bonus_base" value="{{ current_user.user_suinocultura_bonus_base }}" placeholder="Ex: 1200.00">

        <button class="btn btn-ok" type="submit">Salvar Benchmarks Pessoais</button>
      </form>
    </div>
    """
    return page(html_content, title="AP360 | Dashboard", total_lotes=total_lotes, total_campos=total_campos, total_bovinos=total_bovinos)


@app.route("/update_user_benchmarks", methods=["POST"])
@login_required
def update_user_benchmarks():
    try:
        current_user.user_avicultura_gpd = float(request.form.get("user_avicultura_gpd", 0) or 0)
        current_user.user_avicultura_ca = float(request.form.get("user_avicultura_ca", 0) or 0)
        current_user.user_avicultura_bonus_base = float(request.form.get("user_avicultura_bonus_base", 0) or 0)

        current_user.user_suinocultura_gpd = float(request.form.get("user_suinocultura_gpd", 0) or 0)
        current_user.user_suinocultura_ca = float(request.form.get("user_suinocultura_ca", 0) or 0)
        current_user.user_suinocultura_bonus_base = float(request.form.get("user_suinocultura_bonus_base", 0) or 0)

        db.session.commit()
        flash("Benchmarks pessoais atualizados com sucesso!")
    except ValueError:
        flash("Erro: Verifique se os valores numéricos estão corretos.")
    except Exception as e:
        flash(f"Ocorreu um erro inesperado: {e}")
    return redirect(url_for("dashboard"))


@app.route("/cotacao_agricola", methods=["GET", "POST"])
@login_required
def cotacao_agricola():
    if current_user.segmento != "agricultura":
        flash("Seu perfil não tem acesso ao módulo de agricultura.")
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        try:
            produto = request.form.get("produto")
            quantidade_ton = float(request.form.get("quantidade_ton"))
            origem = request.form.get("origem")
            porto = request.form.get("porto")

            cbot_rs_ton, cbot_usd_bushel = cbot_para_rs_ton(produto)
            usd_brl = fx_usd_brl()
            frete_rs_ton = frete_medio(origem, porto)

            export_rs_ton = cbot_rs_ton
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
            flash("Cotação salva com sucesso!")
        except ValueError:
            flash("Erro: Verifique se os valores numéricos estão corretos.")
        except Exception as e:
            flash(f"Ocorreu um erro inesperado: {e}")
        return redirect(url_for("cotacao_agricola"))

    quotes = AgricultureQuote.query.filter_by(user_id=current_user.id).order_by(AgricultureQuote.criado_em.desc()).all()

    html_content = """
    <h2>Cotação Agrícola</h2>
    <div class="card">
      <h3>Nova Cotação</h3>
      <form method="post">
        <label>Produto</label>
        <select name="produto" required>
          {% for p in CBOT.keys() %}
            <option value="{{ p }}">{{ p|capitalize }}</option>
          {% endfor %}
        </select>
        <label>Quantidade (toneladas)</label>
        <input type="number" step="0.01" name="quantidade_ton" placeholder="Ex: 100.5" required>
        <label>Origem (Cidade - UF)</label>
        <input name="origem" placeholder="Ex: Cascavel - PR" required>
        <label>Porto de Destino</label>
        <select name="porto" required>
          {% for p in PORTOS %}
            <option value="{{ p }}">{{ p }}</option>
          {% endfor %}
        </select>
        <button class="btn btn-pri" type="submit">Calcular e Salvar Cotação</button>
      </form>
    </div>

    <div class="card">
      <h3>Minhas Cotações</h3>
      <table>
        <tr>
          <th>Data</th>
          <th>Produto</th>
          <th>Qtd (ton)</th>
          <th>Origem</th>
          <th>Porto</th>
          <th>CBOT (USD/bu)</th>
          <th>USD/BRL</th>
          <th>Export (R$/ton)</th>
          <th>Frete (R$/ton)</th>
          <th>Líquido (R$/ton)</th>
          <th>Total (R$)</th>
        </tr>
        {% for q in quotes %}
          <tr>
            <td>{{ q.criado_em.strftime('%d/%m/%Y %H:%M') }}</td>
            <td>{{ q.produto|capitalize }}</td>
            <td>{{ "%.2f"|format(q.quantidade_ton) }}</td>
            <td>{{ q.origem }}</td>
            <td>{{ q.porto }}</td>
            <td>{{ "%.2f"|format(q.cbot_usd_bushel) }}</td>
            <td>{{ "%.2f"|format(q.usd_brl) }}</td>
            <td>{{ "%.2f"|format(q.export_rs_ton) }}</td>
            <td>{{ "%.2f"|format(q.frete_rs_ton) }}</td>
            <td>{{ "%.2f"|format(q.liquido_rs_ton) }}</td>
            <td>{{ "%.2f"|format(q.total_rs) }}</td>
          </tr>
        {% else %}
          <tr><td colspan="11">Nenhuma cotação registrada ainda.</td></tr>
        {% endfor %}
      </table>
    </div>
    """
    return page(html_content, title="AP360 | Cotação Agrícola", CBOT=CBOT, PORTOS=PORTOS)


@app.route("/<string:cadeia>", methods=["GET", "POST"])
@login_required
def modulo_lotes(cadeia):
    if cadeia not in ["avicultura", "suinocultura"]:
        abort(404) # Ou redirecionar para dashboard com flash

    if current_user.segmento != cadeia:
        flash(f"Seu perfil não tem acesso ao módulo de {cadeia}.")
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        try:
            # Parse datas e horas
            data_alojamento_str = request.form.get("data_alojamento")
            hora_alojamento_str = request.form.get("hora_alojamento")
            data_carregamento_str = request.form.get("data_carregamento")
            hora_carregamento_str = request.form.get("hora_carregamento")

            dt_alojamento_naive = datetime.strptime(f"{data_alojamento_str} {hora_alojamento_str}", "%Y-%m-%d %H:%M")
            dt_carregamento_naive = datetime.strptime(f"{data_carregamento_str} {hora_carregamento_str}", "%Y-%m-%d %H:%M")

            # Localizar para Brasília
            dt_alojamento = BRASILIA_TZ.localize(dt_alojamento_naive)
            dt_carregamento = BRASILIA_TZ.localize(dt_carregamento_naive)

            # Calcular dias com vírgula
            dias_td = dt_carregamento - dt_alojamento
            dias = round(dias_td.total_seconds() / (24 * 3600), 2) # Dias com 2 casas decimais

            peso_inicial = float(request.form.get("peso_inicial"))
            peso_final = float(request.form.get("peso_final"))
            racao_total_kg = float(request.form.get("racao_total_kg"))
            animais_iniciais = int(request.form.get("animais_iniciais"))
            animais_final = int(request.form.get("animais_final"))

            # Calcular indicadores básicos
            gpd = calc_gpd(peso_inicial, peso_final, dias)
            ca = calc_ca(racao_total_kg, peso_inicial, peso_final, animais_final)
            viabilidade_pct = calc_viabilidade(animais_iniciais, animais_final)
            mortalidade_pct = calc_mortalidade(animais_iniciais, animais_final)

            # Benchmarks
            meta_gpd, meta_ca, bonus_base = get_effective_benchmark(cadeia, current_user.cooperativa, current_user)

            # Referências do usuário para o lote (se preenchidas)
            gpd_coop_ref_form = float(request.form.get("gpd_coop_ref", 0) or 0)
            ca_coop_ref_form = float(request.form.get("ca_coop_ref", 0) or 0)

            if gpd_coop_ref_form > 0:
                meta_gpd = gpd_coop_ref_form
            if ca_coop_ref_form > 0:
                meta_ca = ca_coop_ref_form

            ca_ajustada = ca # Valor inicial, pode ser ajustado abaixo
            iep = 0.0
            indice_lote = 0.0
            bonificacao = 0.0
            peso_vivo_medio = 0.0
            peso_carcaca_medio = 0.0
            rendimento_carcaca_pct = 0.0
            carne_magra_pct = 0.0
            bonus_tipificacao = 0.0

            if cadeia == "avicultura":
                peso_meta_coop = float(request.form.get("peso_meta_coop", 0) or 0)
                idade_meta_coop = float(request.form.get("idade_meta_coop", 0) or 0)
                fator_peso_caa = float(request.form.get("fator_peso_caa", 0.30) or 0.30)
                fator_idade_caa = float(request.form.get("fator_idade_caa", 0.01) or 0.01)

                if peso_meta_coop > 0 and idade_meta_coop > 0:
                    ca_ajustada = calc_ca_ajustada_avicultura(
                        ca_observada=ca,
                        peso_real=peso_final, # Aqui usamos o peso_final do lote
                        idade_real=dias,
                        peso_meta=peso_meta_coop,
                        idade_meta=idade_meta_coop,
                        fator_peso=fator_peso_caa,
                        fator_idade=fator_idade_caa
                    )
                iep = calc_iep_avicultura(viabilidade_pct, peso_final, dias, ca_ajustada)
                bonificacao = calc_bonificacao(gpd, ca_ajustada, meta_gpd, meta_ca, bonus_base)

            elif cadeia == "suinocultura":
                peso_vivo_medio = float(request.form.get("peso_vivo_medio", 0) or 0)
                peso_carcaca_medio = float(request.form.get("peso_carcaca_medio", 0) or 0)
                carne_magra_pct = float(request.form.get("carne_magra_pct", 0) or 0)
                rendimento_carcaca_pct = calc_rendimento_carcaca(peso_vivo_medio, peso_carcaca_medio)

                if peso_vivo_medio > 0:
                    ajuste_peso = 0.003 * (peso_vivo_medio - 120.0) # Exemplo de ajuste para suínos
                    ca_ajustada = round(max(ca + ajuste_peso, 0.01), 4)

                indice_lote = calc_indice_lote_suino(gpd, viabilidade_pct, ca_ajustada)
                bonus_tipificacao = calc_bonus_tipificacao(carne_magra_pct, rendimento_carcaca_pct)
                bonificacao = calc_bonificacao(gpd, ca_ajustada, meta_gpd, meta_ca, bonus_base)
                bonificacao = round(bonificacao + bonus_tipificacao, 2)


            new_batch = Batch(
                user_id=current_user.id,
                cadeia=cadeia,
                estrutura=request.form.get("estrutura").strip(),
                lote=request.form.get("lote").strip(),
                data_alojamento=dt_alojamento,
                data_carregamento=dt_carregamento,
                dias=dias,
                peso_inicial=peso_inicial,
                peso_final=peso_final,
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
                coop_media_gpd=meta_gpd,
                coop_media_ca=meta_ca,
                peso_meta_coop=float(request.form.get("peso_meta_coop", 0) or 0) if cadeia == "avicultura" else 0.0,
                idade_meta_coop=float(request.form.get("idade_meta_coop", 0) or 0) if cadeia == "avicultura" else 0.0,
                fator_peso_caa=float(request.form.get("fator_peso_caa", 0.30) or 0.30) if cadeia == "avicultura" else 0.0,
                fator_idade_caa=float(request.form.get("fator_idade_caa", 0.01) or 0.01) if cadeia == "avicultura" else 0.0,
                peso_vivo_medio=peso_vivo_medio,
                peso_carcaca_medio=peso_carcaca_medio,
                rendimento_carcaca_pct=rendimento_carcaca_pct,
                carne_magra_pct=carne_magra_pct,
                bonus_tipificacao=bonus_tipificacao,
                gpd_coop_ref=gpd_coop_ref_form,
                ca_coop_ref=ca_coop_ref_form
            )
            db.session.add(new_batch)
            db.session.commit()
            flash(f"Lote de {cadeia} adicionado com sucesso!")
        except ValueError as e:
            flash(f"Erro nos dados de entrada: {e}. Verifique se todos os campos numéricos e de data/hora estão corretos.")
        except Exception as e:
            flash(f"Ocorreu um erro inesperado: {e}")
        return redirect(url_for("modulo_lotes", cadeia=cadeia))

    batches = Batch.query.filter_by(user_id=current_user.id, cadeia=cadeia).order_by(Batch.criado_em.desc()).all()

    # Obter benchmarks efetivos para exibir no formulário
    meta_gpd_form, meta_ca_form, bonus_base_form = get_effective_benchmark(cadeia, current_user.cooperativa, current_user)

    html_content = f"""
    <h2>Módulo de {cadeia.capitalize()}</h2>

    <div class="grid">
      <div class="card">
        <h3>Novo Lote</h3>
        <form method="post">
          <label>Estrutura</label>
          <input name="estrutura" placeholder="Ex: Galpão 1" required>
          <label>Lote</label>
          <input name="lote" placeholder="Ex: Lote 2024-06" required>

          <label>Data e Hora de Alojamento</label>
          <input type="date" name="data_alojamento" value="{datetime.now(BRASILIA_TZ).strftime('%Y-%m-%d')}" required>
          <input type="time" name="hora_alojamento" value="{datetime.now(BRASILIA_TZ).strftime('%H:%M')}" required>

          <label>Data e Hora de Carregamento/Abate</label>
          <input type="date" name="data_carregamento" value="{datetime.now(BRASILIA_TZ).strftime('%Y-%m-%d')}" required>
          <input type="time" name="hora_carregamento" value="{datetime.now(BRASILIA_TZ).strftime('%H:%M')}" required>

          <label>Peso inicial médio por animal (kg)</label>
          <input type="number" step="0.0001" name="peso_inicial" placeholder="Ex: 0.04 (pintinho)" required>
          <label>Peso final médio por animal (kg)</label>
          <input type="number" step="0.0001" name="peso_final" placeholder="Ex: 2.85 (ave abatida)" required>

          <label>Ração TOTAL consumida pelo lote (kg)</label>
          <input type="number" step="0.0001" name="racao_total_kg" placeholder="Ex: 4800 (para 1000 aves)" required>
          <label>Animais iniciais</label>
          <input type="number" name="animais_iniciais" placeholder="Ex: 1000" required>
          <label>Animais finais (abatidos/vendidos)</label>
          <input type="number" name="animais_final" placeholder="Ex: 970" required>

          <h4>Minhas referências para este lote (opcional)</h4>
          <p class="muted">Se preenchido, estes valores sobrescrevem seus benchmarks pessoais e os da cooperativa para este lote.</p>
          <label>GPD médio (opcional)</label>
          <input type="number" step="0.0001" name="gpd_coop_ref" placeholder="GPD médio (opcional)">
          <label>CA média (opcional)</label>
          <input type="number" step="0.0001" name="ca_coop_ref" placeholder="CA média (opcional)">

          {% if cadeia == 'avicultura' %}
            <h4>Parâmetros CA Ajustada (Avicultura)</h4>
            <label>Peso meta coop (kg)</label>
            <input type="number" step="0.0001" name="peso_meta_coop" value="{{ meta_gpd_form }}" placeholder="Peso meta coop (kg), ex: 2.90">
            <label>Idade meta coop (dias)</label>
            <input type="number" step="0.01" name="idade_meta_coop" value="{{ meta_ca_form }}" placeholder="Idade meta coop (dias), ex: 42.5">
            <label>Fator peso CAA</label>
            <input type="number" step="0.0001" name="fator_peso_caa" value="0.30" placeholder="Fator peso CAA, ex: 0.30">
            <label>Fator idade CAA</label>
            <input type="number" step="0.0001" name="fator_idade_caa" value="0.01" placeholder="Fator idade CAA, ex: 0.01">
          {% endif %}

          {% if cadeia == 'suinocultura' %}
            <h4>Carcaça e tipificação (Suínos)</h4>
            <label>Peso vivo médio (kg/cab)</label>
            <input type="number" step="0.01" name="peso_vivo_medio" placeholder="Ex: 120.0" required>
            <label>Peso carcaça médio (kg/cab)</label>
            <input type="number" step="0.01" name="peso_carcaca_medio" placeholder="Ex: 90.0" required>
            <label>% carne magra</label>
            <input type="number" step="0.01" name="carne_magra_pct" placeholder="Ex: 58.5" required>
          {% endif %}

          <button class="btn btn-ok" type="submit">Salvar Lote</button>
        </form>
      </div>

      <div class="card">
        <h3>Meus Lotes de {{ cadeia|capitalize }}</h3>
        <table>
          <tr>
            <th>Estrutura</th>
            <th>Lote</th>
            <th>Dias</th>
            <th>GPD</th>
            <th>CA</th>
            <th>CAA</th>
            <th>IEP/EPEF</th>
            <th>Bônus (R$)</th>
            <th>Ações</th>
          </tr>
          {% for batch in batches %}
            <tr>
              <td>{{ batch.estrutura }}</td>
              <td>{{ batch.lote }}</td>
              <td>{{ "%.2f"|format(batch.dias) }}</td>
              <td>{{ "%.4f"|format(batch.gpd) }}</td>
              <td>{{ "%.4f"|format(batch.ca) }}</td>
              <td>{{ "%.4f"|format(batch.ca_ajustada) }}</td>
              <td>{{ "%.2f"|format(batch.iep) }}</td>
              <td>{{ "%.2f"|format(batch.bonificacao) }}</td>
              <td>
                <a class="btn btn-ghost" href="{{ url_for('detalhes_lote_ao_vivo', cadeia=cadeia, batch_id=batch.id) }}">Acomp.</a>
                <a class="btn btn-ghost" href="{{ url_for('editar_lote', cadeia=cadeia, batch_id=batch.id) }}">Editar</a>
                <form method="post" action="{{ url_for('excluir_lote', cadeia=cadeia, batch_id=batch.id) }}" style="display:inline;">
                  <button type="submit" class="btn btn-danger" onclick="return confirm('Tem certeza que deseja excluir este lote e todos os seus registros diários?');">Excluir</button>
                </form>
              </td>
            </tr>
          {% else %}
            <tr><td colspan="9">Nenhum lote de {{ cadeia }} cadastrado ainda.</td></tr>
          {% endfor %}
        </table>
      </div>
    </div>
    """
    return page(html_content, title=f"AP360 | {cadeia.capitalize()}", cadeia=cadeia, batches=batches,
                datetime=datetime, BRASILIA_TZ=BRASILIA_TZ, meta_gpd_form=meta_gpd_form, meta_ca_form=meta_ca_form)


@app.route("/<string:cadeia>/editar/<int:batch_id>", methods=["GET", "POST"])
@login_required
def editar_lote(cadeia, batch_id):
    if cadeia not in ["avicultura", "suinocultura"]:
        abort(404)

    batch = Batch.query.filter_by(id=batch_id, user_id=current_user.id, cadeia=cadeia).first_or_404()

    if request.method == "POST":
        try:
            # Parse datas e horas
            data_alojamento_str = request.form.get("data_alojamento")
            hora_alojamento_str = request.form.get("hora_alojamento")
            data_carregamento_str = request.form.get("data_carregamento")
            hora_carregamento_str = request.form.get("hora_carregamento")

            dt_alojamento_naive = datetime.strptime(f"{data_alojamento_str} {hora_alojamento_str}", "%Y-%m-%d %H:%M")
            dt_carregamento_naive = datetime.strptime(f"{data_carregamento_str} {hora_carregamento_str}", "%Y-%m-%d %H:%M")

            # Localizar para Brasília
            batch.data_alojamento = BRASILIA_TZ.localize(dt_alojamento_naive)
            batch.data_carregamento = BRASILIA_TZ.localize(dt_carregamento_naive)

            # Calcular dias com vírgula
            dias_td = batch.data_carregamento - batch.data_alojamento
            batch.dias = round(dias_td.total_seconds() / (24 * 3600), 2) # Dias com 2 casas decimais

            batch.estrutura = request.form.get("estrutura").strip()
            batch.lote = request.form.get("lote").strip()
            batch.peso_inicial = float(request.form.get("peso_inicial"))
            batch.peso_final = float(request.form.get("peso_final"))
            batch.racao_total_kg = float(request.form.get("racao_total_kg"))
            batch.animais_iniciais = int(request.form.get("animais_iniciais"))
            batch.animais_final = int(request.form.get("animais_final"))

            # Recalcular indicadores
            batch.gpd = calc_gpd(batch.peso_inicial, batch.peso_final, batch.dias)
            batch.ca = calc_ca(batch.racao_total_kg, batch.peso_inicial, batch.peso_final, batch.animais_final)
            batch.viabilidade_pct = calc_viabilidade(batch.animais_iniciais, batch.animais_final)
            batch.mortalidade_pct = calc_mortalidade(batch.animais_iniciais, batch.animais_final)

            # Benchmarks
            meta_gpd, meta_ca, bonus_base = get_effective_benchmark(cadeia, current_user.cooperativa, current_user)

            # Referências do usuário para o lote (se preenchidas)
            gpd_coop_ref_form = float(request.form.get("gpd_coop_ref", 0) or 0)
            ca_coop_ref_form = float(request.form.get("ca_coop_ref", 0) or 0)
            batch.gpd_coop_ref = gpd_coop_ref_form
            batch.ca_coop_ref = ca_coop_ref_form

            if gpd_coop_ref_form > 0:
                meta_gpd = gpd_coop_ref_form
            if ca_coop_ref_form > 0:
                meta_ca = ca_coop_ref_form

            batch.ca_ajustada = batch.ca # Reset para o cálculo
            batch.iep = 0.0
            batch.indice_lote = 0.0
            batch.bonificacao = 0.0
            bonus_tipificacao = 0.0

            if cadeia == "avicultura":
                batch.peso_meta_coop = float(request.form.get("peso_meta_coop", 0) or 0)
                batch.idade_meta_coop = float(request.form.get("idade_meta_coop", 0) or 0)
                batch.fator_peso_caa = float(request.form.get("fator_peso_caa", 0.30) or 0.30)
                batch.fator_idade_caa = float(request.form.get("fator_idade_caa", 0.01) or 0.01)

                if batch.peso_meta_coop > 0 and batch.idade_meta_coop > 0:
                    batch.ca_ajustada = calc_ca_ajustada_avicultura(
                        ca_observada=batch.ca,
                        peso_real=batch.peso_final, # Este é o valor que precisamos verificar!
                        idade_real=batch.dias,
                        peso_meta=batch.peso_meta_coop,
                        idade_meta=batch.idade_meta_coop,
                        fator_peso=batch.fator_peso_caa,
                        fator_idade=batch.fator_idade_caa
                    )
                batch.iep = calc_iep_avicultura(batch.viabilidade_pct, batch.peso_final, batch.dias, batch.ca_ajustada)
                batch.bonificacao = calc_bonificacao(batch.gpd, batch.ca_ajustada, meta_gpd, meta_ca, bonus_base)

            elif cadeia == "suinocultura":
                batch.peso_vivo_medio = float(request.form.get("peso_vivo_medio", 0) or 0)
                batch.peso_carcaca_medio = float(request.form.get("peso_carcaca_medio", 0) or 0)
                batch.carne_magra_pct = float(request.form.get("carne_magra_pct", 0) or 0)
                batch.rendimento_carcaca_pct = calc_rendimento_carcaca(batch.peso_vivo_medio, batch.peso_carcaca_medio)

                if batch.peso_vivo_medio > 0:
                    ajuste_peso = 0.003 * (batch.peso_vivo_medio - 120.0)
                    batch.ca_ajustada = round(max(batch.ca + ajuste_peso, 0.01), 4)

                batch.indice_lote = calc_indice_lote_suino(batch.gpd, batch.viabilidade_pct, batch.ca_ajustada)
                bonus_tipificacao = calc_bonus_tipificacao(batch.carne_magra_pct, batch.rendimento_carcaca_pct)
                batch.bonificacao = calc_bonificacao(batch.gpd, batch.ca_ajustada, meta_gpd, meta_ca, bonus_base)
                batch.bonificacao = round(batch.bonificacao + bonus_tipificacao, 2) # Soma o bônus de tipificação

            db.session.commit()
            flash(f"Lote de {cadeia} atualizado com sucesso!")
            return redirect(url_for("modulo_lotes", cadeia=cadeia))
        except ValueError as e:
            flash(f"Erro nos dados de entrada: {e}. Verifique se todos os campos numéricos e de data/hora estão corretos.")
        except Exception as e:
            flash(f"Ocorreu um erro inesperado: {e}")

    html_content = f"""
    <h2>Editar Lote de {cadeia.capitalize()}</h2>
    <div class="card" style="max-width:700px;margin:0 auto">
      <form method="post">
        <label>Estrutura</label>
        <input name="estrutura" value="{batch.estrutura}" required>
        <label>Lote</label>
        <input name="lote" value="{batch.lote}" required>

        <label>Data e Hora de Alojamento</label>
        <input type="date" name="data_alojamento" value="{batch.data_alojamento.astimezone(BRASILIA_TZ).strftime('%Y-%m-%d')}" required>
        <input type="time" name="hora_alojamento" value="{batch.data_alojamento.astimezone(BRASILIA_TZ).strftime('%H:%M')}" required>

        <label>Data e Hora de Carregamento/Abate</label>
        <input type="date" name="data_carregamento" value="{batch.data_carregamento.astimezone(BRASILIA_TZ).strftime('%Y-%m-%d')}" required>
        <input type="time" name="hora_carregamento" value="{batch.data_carregamento.astimezone(BRASILIA_TZ).strftime('%H:%M')}" required>

        <label>Peso inicial médio por animal (kg)</label>
        <input type="number" step="0.0001" name="peso_inicial" value="{batch.peso_inicial}" required>
        <label>Peso final médio por animal (kg)</label>
        <input type="number" step="0.0001" name="peso_final" value="{batch.peso_final}" required>

        <label>Ração TOTAL consumida pelo lote (kg)</label>
        <input type="number" step="0.0001" name="racao_total_kg" value="{batch.racao_total_kg}" required>
        <label>Animais iniciais</label>
        <input type="number" name="animais_iniciais" value="{batch.animais_iniciais}" required>
        <label>Animais finais (abatidos/vendidos)</label>
        <input type="number" name="animais_final" value="{batch.animais_final}" required>

        <h4>Minhas referências para este lote (opcional)</h4>
        <p class="muted">Se preenchido, estes valores sobrescrevem seus benchmarks pessoais e os da cooperativa para este lote.</p>
        <label>GPD médio (opcional)</label>
        <input type="number" step="0.0001" name="gpd_coop_ref" value="{batch.gpd_coop_ref}" placeholder="GPD médio (opcional)">
        <label>CA média (opcional)</label>
        <input type="number" step="0.0001" name="ca_coop_ref" value="{batch.ca_coop_ref}" placeholder="CA média (opcional)">

        {% if cadeia == 'avicultura' %}
          <h4>Parâmetros CA Ajustada (Avicultura)</h4>
          <label>Peso meta coop (kg)</label>
          <input type="number" step="0.0001" name="peso_meta_coop" value="{batch.peso_meta_coop}" placeholder="Peso meta coop (kg), ex: 2.90">
          <label>Idade meta coop (dias)</label>
          <input type="number" step="0.01" name="idade_meta_coop" value="{batch.idade_meta_coop}" placeholder="Idade meta coop (dias), ex: 42.5">
          <label>Fator peso CAA</label>
          <input type="number" step="0.0001" name="fator_peso_caa" value="{batch.fator_peso_caa}" placeholder="Fator peso CAA, ex: 0.30">
          <label>Fator idade CAA</label>
          <input type="number" step="0.0001" name="fator_idade_caa" value="{batch.fator_idade_caa}" placeholder="Fator idade CAA, ex: 0.01">
        {% endif %}

        {% if cadeia == 'suinocultura' %}
          <h4>Carcaça e tipificação (Suínos)</h4>
          <label>Peso vivo médio (kg/cab)</label>
          <input type="number" step="0.01" name="peso_vivo_medio" value="{batch.peso_vivo_medio}" required>
          <label>Peso carcaça médio (kg/cab)</label>
          <input type="number" step="0.01" name="peso_carcaca_medio" value="{batch.peso_carcaca_medio}" required>
          <label>% carne magra</label>
          <input type="number" step="0.01" name="carne_magra_pct" value="{batch.carne_magra_pct}" required>
        {% endif %}

        <button class="btn btn-ok" type="submit">Salvar Alterações</button>
        <a class="btn btn-ghost" href="{{ url_for('modulo_lotes', cadeia=cadeia) }}">Cancelar</a>
      </form>
    </div>
    """
    return page(html_content, title=f"AP360 | Editar Lote {cadeia.capitalize()}", batch=batch, cadeia=cadeia,
                datetime=datetime, BRASILIA_TZ=BRASILIA_TZ)


@app.route("/<string:cadeia>/excluir/<int:batch_id>", methods=["POST"])
@login_required
def excluir_lote(cadeia, batch_id):
    batch = Batch.query.filter_by(id=batch_id, user_id=current_user.id, cadeia=cadeia).first_or_404()
    BatchDailyRecord.query.filter_by(batch_id=batch.id).delete() # Exclui registros diários do lote
    db.session.delete(batch)
    db.session.commit()
    flash(f"Lote de {cadeia} excluído com sucesso!")
    return redirect(url_for("modulo_lotes", cadeia=cadeia))


@app.route("/<string:cadeia>/lote/<int:batch_id>", methods=["GET", "POST"])
@login_required
def detalhes_lote_ao_vivo(cadeia, batch_id):
    batch = Batch.query.filter_by(id=batch_id, user_id=current_user.id, cadeia=cadeia).first_or_404()
    daily_records = BatchDailyRecord.query.filter_by(batch_id=batch.id).order_by(BatchDailyRecord.data_registro.asc()).all()

    if request.method == "POST":
        form_type = request.form.get("form_type")
        if form_type == "novo_registro_diario_lote":
            try:
                data_registro = datetime.strptime(request.form.get("data_registro"), "%Y-%m-%d").date()
                if BatchDailyRecord.query.filter_by(batch_id=batch.id, data_registro=data_registro).first():
                    flash("Já existe um registro para esta data neste lote.")
                    return redirect(url_for("detalhes_lote_ao_vivo", cadeia=cadeia, batch_id=batch.id))

                record = BatchDailyRecord(
                    batch_id=batch.id,
                    data_registro=data_registro,
                    peso_medio=float(request.form.get("peso_medio", 0) or 0),
                    consumo_racao_dia=float(request.form.get("consumo_racao_dia", 0) or 0),
                    mortalidade_dia=int(request.form.get("mortalidade_dia", 0) or 0),
                    observacoes=request.form.get("observacoes", "").strip()
                )
                db.session.add(record)
                db.session.commit()
                flash("Registro diário adicionado com sucesso!")
            except ValueError:
                flash("Erro: Verifique se os valores numéricos e de data estão corretos.")
            except Exception as e:
                flash(f"Ocorreu um erro ao adicionar o registro: {e}")
            return redirect(url_for("detalhes_lote_ao_vivo", cadeia=cadeia, batch_id=batch.id))

    # Preparar dados para gráficos
    chart_labels = [r.data_registro.strftime('%d/%m') for r in daily_records]
    chart_peso_medio = [r.peso_medio for r in daily_records]
    chart_consumo_racao = [r.consumo_racao_dia for r in daily_records]
    chart_mortalidade = [r.mortalidade_dia for r in daily_records]

    html_content = f"""
    <h2>Acompanhamento ao Vivo: {batch.estrutura} - {batch.lote} ({cadeia.capitalize()})</h2>
    <div class="card">
      <p><b>Alojamento:</b> {batch.data_alojamento.astimezone(BRASILIA_TZ).strftime('%d/%m/%Y %H:%M')}</p>
      <p><b>Carregamento:</b> {batch.data_carregamento.astimezone(BRASILIA_TZ).strftime('%d/%m/%Y %H:%M')}</p>
      <p><b>Dias de Alojamento:</b> {"%.2f"|format(batch.dias)}</p>
      <p><b>Peso Inicial Médio:</b> {"%.4f"|format(batch.peso_inicial)} kg</p>
      <p><b>Peso Final Médio:</b> {"%.4f"|format(batch.peso_final)} kg</p>
      <p><b>Ração Total:</b> {"%.4f"|format(batch.racao_total_kg)} kg</p>
      <p><b>Animais Iniciais:</b> {batch.animais_iniciais}</p>
      <p><b>Animais Finais:</b> {batch.animais_final}</p>
      <p><b>Viabilidade:</b> {"%.2f"|format(batch.viabilidade_pct)}%</p>
      <p><b>Mortalidade:</b> {"%.2f"|format(batch.mortalidade_pct)}%</p>
      <p><b>GPD:</b> {"%.4f"|format(batch.gpd)}</p>
      <p><b>CA:</b> {"%.4f"|format(batch.ca)}</p>
      <p><b>CA Ajustada:</b> {"%.4f"|format(batch.ca_ajustada)}</p>
      <p><b>IEP/EPEF:</b> {"%.2f"|format(batch.iep)}</p>
      <p><b>Bônus Total:</b> R$ {"%.2f"|format(batch.bonificacao)}</p>
      <a class="btn btn-ghost" href="{{ url_for('modulo_lotes', cadeia=cadeia) }}">Voltar para Lotes</a>
    </div>

    <div class="grid">
      <div class="card">
        <h3>Adicionar Registro Diário</h3>
        <form method="post">
          <input type="hidden" name="form_type" value="novo_registro_diario_lote">
          <label>Data do Registro</label>
          <input type="date" name="data_registro" required>
          <input type="number" step="0.01" name="peso_medio" placeholder="Peso Médio (kg)">
          <input type="number" step="0.01" name="consumo_racao_dia" placeholder="Consumo Ração (kg/dia)">
          <input type="number" name="mortalidade_dia" placeholder="Mortalidade (animais no dia)">
          <textarea name="observacoes" placeholder="Observações"></textarea>
          <button class="btn btn-ok" type="submit">Salvar Registro</button>
        </form>
      </div>
      <div class="card">
        <h3>Gráficos de Acompanhamento</h3>
        <div class="grid">
          {% if chart_labels %}
          <div>
            <h4>Peso Médio (kg)</h4>
            <canvas id="pesoMedioChart" height="150"></canvas>
          </div>
          <div>
            <h4>Consumo Ração (kg/dia)</h4>
            <canvas id="consumoRacaoChart" height="150"></canvas>
          </div>
          <div>
            <h4>Mortalidade (animais/dia)</h4>
            <canvas id="mortalidadeChart" height="150"></canvas>
          </div>
          {% else %}
            <p class="muted">Adicione registros diários para ver os gráficos.</p>
          {% endif %}
        </div>
        <script>
          const chartLabels = {{ chart_labels | tojson }};
          const chartPesoMedio = {{ chart_peso_medio | tojson }};
          const chartConsumoRacao = {{ chart_consumo_racao | tojson }};
          const chartMortalidade = {{ chart_mortalidade | tojson }};

          if (chartLabels.length > 0) {
            new Chart(document.getElementById("pesoMedioChart"), {
              type: "line",
              data: { labels: chartLabels, datasets: [{ label: "Peso Médio (kg)", data: chartPesoMedio, borderColor: 'rgba(59, 185, 255, 1)', tension: 0.2 }] },
              options: { responsive: true, scales: { y: { beginAtZero: true } } }
            });
            new Chart(document.getElementById("consumoRacaoChart"), {
              type: "bar",
              data: { labels: chartLabels, datasets: [{ label: "Consumo Ração (kg/dia)", data: chartConsumoRacao, backgroundColor: 'rgba(70, 221, 152, 0.5)' }] },
              options: { responsive: true, scales: { y: { beginAtZero: true } } }
            });
            new Chart(document.getElementById("mortalidadeChart"), {
              type: "bar",
              data: { labels: chartLabels, datasets: [{ label: "Mortalidade (animais/dia)", data: chartMortalidade, backgroundColor: 'rgba(255, 99, 132, 0.5)' }] },
              options: { responsive: true, scales: { y: { beginAtZero: true } } }
            });
          }
        </script>
      </div>
    </div>

    <div class="card">
      <h3>Histórico de Registros Diários</h3>
      <table>
        <tr><th>Data</th><th>Peso Médio (kg)</th><th>Consumo Ração (kg/dia)</th><th>Mortalidade (animais/dia)</th><th>Observações</th><th>Ações</th></tr>
        {% for record in daily_records %}
          <tr>
            <td>{{ record.data_registro.strftime('%d/%m/%Y') }}</td>
            <td>{{ record.peso_medio }}</td>
            <td>{{ record.consumo_racao_dia }}</td>
            <td>{{ record.mortalidade_dia }}</td>
            <td>{{ record.observacoes or '-' }}</td>
            <td>
              <form method="post" action="{{ url_for('excluir_registro_diario_lote', cadeia=cadeia, record_id=record.id) }}" style="display:inline;">
                <button type="submit" class="btn btn-danger" onclick="return confirm('Tem certeza que deseja excluir este registro?');">Excluir</button>
              </form>
            </td>
          </tr>
        {% else %}
          <tr><td colspan="6">Nenhum registro diário para este lote ainda.</td></tr>
        {% endfor %}
      </table>
    </div>
    """
    return page(html_content, title=f"AP360 | Acompanhamento Lote {batch.lote}", batch=batch, cadeia=cadeia,
                daily_records=daily_records, chart_labels=chart_labels, chart_peso_medio=chart_peso_medio,
                chart_consumo_racao=chart_consumo_racao, chart_mortalidade=chart_mortalidade)


@app.route("/<string:cadeia>/lote/registro_diario/excluir/<int:record_id>", methods=["POST"])
@login_required
def excluir_registro_diario_lote(cadeia, record_id):
    record = BatchDailyRecord.query.get_or_404(record_id)
    batch_id = record.batch_id
    batch = Batch.query.filter_by(id=batch_id, user_id=current_user.id, cadeia=cadeia).first_or_404() # Garante que o usuário é o dono do lote
    db.session.delete(record)
    db.session.commit()
    flash("Registro diário excluído com sucesso!")
    return redirect(url_for("detalhes_lote_ao_vivo", cadeia=cadeia, batch_id=batch_id))


# =========================================================
# BOVINOCULTURA
# =========================================================
@app.route("/bovinocultura", methods=["GET", "POST"])
@login_required
def bovinocultura():
    if current_user.segmento != "bovinocultura":
        flash("Seu perfil não tem acesso ao módulo de bovinocultura.")
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        form_type = request.form.get("form_type")

        if form_type == "novo_bovino":
            brinco = request.form.get("brinco", "").strip()
            # Verifica se o brinco já está cadastrado para o usuário atual
            if Bovino.query.filter_by(brinco=brinco, user_id=current_user.id).first():
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
            # Garante que o usuário só pode adicionar peso aos seus próprios bovinos
            bov = Bovino.query.filter_by(id=bovino_id, user_id=current_user.id).first_or_404()
            data = request.form.get("data", "")
            peso = float(request.form.get("peso", 0))

            db.session.add(BovinoPeso(bovino_id=bov.id, data=data, peso=peso))
            bov.peso_atual = peso
            bov.ultima_pesagem = data
            db.session.commit()
            flash("Pesagem registrada.")
            return redirect(url_for("bovinocultura", animal=bov.id))

        if form_type == "novo_evento":
            bovino_id = int(request.form.get("bovino_id"))
            # Garante que o usuário só pode adicionar eventos aos seus próprios bovinos
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

    html_content = f"""
    <h2>Bovinocultura</h2>

    <div class="grid">
      <div class="card">
        <h3>Novo animal</h3>
        <form method="post">
          <input type="hidden" name="form_type" value="novo_bovino">
          <input name="brinco" placeholder="Brinco (único para você)" required>
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
              <td>{a.brinco}</td>
              <td>{a.nome or "-"}</td>
              <td>{a.peso_atual}</td>
              <td>{a.status|capitalize}</td>
              <td>
                <a class="btn btn-ghost" href="{{ url_for('bovinocultura', animal=a.id) }}">Ficha</a>
                <a class="btn btn-ghost" href="{{ url_for('editar_bovino', bovino_id=a.id) }}">Editar</a>
                <form method="post" action="{{ url_for('excluir_bovino', bovino_id=a.id) }}" style="display:inline;">
                  <button type="submit" class="btn btn-danger" onclick="return confirm('Tem certeza que deseja excluir este bovino e todos os seus registros?');">Excluir</button>
                </form>
              </td>
            </tr>
          {% endfor %}
        </table>
      </div>
    </div>

    {% if animal %}
    <div class="card">
      <h3>Ficha: {animal.brinco} - {animal.nome or "-"}</h3>
      <p class="muted">Raça: {animal.raca or "-"} | Sexo: {animal.sexo or "-"} | Nascimento: {animal.nascimento or "-"}</p>
      <p class="muted">Peso atual: <b>{animal.peso_atual} kg</b> | Última pesagem: <b>{animal.ultima_pesagem or "-"}</b></p>
    </div>

    <div class="grid">
      <div class="card">
        <h3>Registrar pesagem</h3>
        <form method="post">
          <input type="hidden" name="form_type" value="novo_peso">
          <input type="hidden" name="bovino_id" value="{animal.id}">
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
          <input type="hidden" name="bovino_id" value="{animal.id}">
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
            <td>{p.data}</td>
            <td>{p.peso}</td>
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
            <td>{e.data}</td>
            <td>{e.tipo|capitalize}</td>
            <td>{e.descricao}</td>
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
        new_brinco = request.form.get("brinco", "").strip()
        # Verifica se o novo brinco já existe para o usuário, excluindo o próprio bovino que está sendo editado
        existing_bovino_with_brinco = Bovino.query.filter(
            Bovino.user_id == current_user.id,
            Bovino.brinco == new_brinco,
            Bovino.id != bovino_id
        ).first()
        if existing_bovino_with_brinco:
            flash("Brinco já cadastrado para este usuário em outro animal.")
            return redirect(url_for("editar_bovino", bovino_id=bovino_id))

        bovino.brinco = new_brinco
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

    html_content = f"""
    <h2>Editar Bovino</h2>
    <div class="card" style="max-width:700px;margin:0 auto">
      <form method="post">
        <label>Brinco</label>
        <input name="brinco" value="{bovino.brinco}" required>
        <label>Nome</label>
        <input name="nome" value="{bovino.nome or ''}">
        <label>Sexo</label>
        <select name="sexo">
          <option value="M" {% if bovino.sexo == 'M' %}selected{% endif %}>M</option>
          <option value="F" {% if bovino.sexo == 'F' %}selected{% endif %}>F</option>
        </select>
        <label>Raça</label>
        <input name="raca" value="{bovino.raca or ''}">
        <label>Nascimento</label>
        <input type="date" name="nascimento" value="{bovino.nascimento or ''}">
        <label>Origem</label>
        <input name="origem" value="{bovino.origem or ''}">
        <label>Lote</label>
        <input name="lote" value="{bovino.lote or ''}">
        <label>Status</label>
        <select name="status">
          <option value="ativo" {% if bovino.status == 'ativo' %}selected{% endif %}>Ativo</option>
          <option value="vendido" {% if bovino.status == 'vendido' %}selected{% endif %}>Vendido</option>
          <option value="descartado" {% if bovino.status == 'descartado' %}selected{% endif %}>Descartado</option>
        </select>
        <label>Observações</label>
        <textarea name="observacoes">{bovino.observacoes or ''}</textarea>
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
    bovino = Bovino.query.filter_by(id=bovino_id, user_id=current_user.id).first_or_404() # Garante que o usuário é o dono do bovino

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
    bovino = Bovino.query.filter_by(id=bovino_id, user_id=current_user.id).first_or_404() # Garante que o usuário é o dono do bovino

    db.session.delete(evento_registro)
    db.session.commit()
    flash("Registro de evento excluído com sucesso!")
    return redirect(url_for("bovinocultura", animal=bovino_id))


# =========================================================
# ADMIN
# =========================================================
@app.route("/admin")
@admin_required
def admin_panel():
    access_requests = AccessRequest.query.filter_by(status="pendente").order_by(AccessRequest.criado_em.asc()).all()
    denied_requests = AccessRequest.query.filter_by(status="negado").order_by(AccessRequest.criado_em.desc()).all()
    active_users = User.query.filter(User.perfil != "admin").order_by(User.criado_em.desc()).all()
    invited_tokens = AccessInvite.query.filter_by(status="convidado").order_by(AccessInvite.criado_em.desc()).all()
    coop_benchmarks = CoopBenchmark.query.order_by(CoopBenchmark.cooperativa, CoopBenchmark.cadeia).all()

    html_content = """
    <h2>Painel Administrativo</h2>

    <div class="card">
      <h3>Solicitações de Acesso Pendentes</h3>
      <table>
        <tr><th>Data</th><th>Nome</th><th>Email</th><th>Segmento</th><th>Cooperativa</th><th>Ações</th></tr>
        {% for req in access_requests %}
          <tr>
            <td>{{ req.criado_em.strftime('%d/%m/%Y %H:%M') }}</td>
            <td>{{ req.nome }}</td>
            <td>{{ req.email }}</td>
            <td>{{ req.segmento|capitalize }}</td>
            <td>{{ req.cooperativa or '-' }}</td>
            <td>
              <form method="post" action="{{ url_for('approve_request', request_id=req.id) }}" style="display:inline;">
                <button type="submit" class="btn btn-ok">Aprovar</button>
              </form>
              <form method="post" action="{{ url_for('deny_request', request_id=req.id) }}" style="display:inline;">
                <button type="submit" class="btn btn-danger">Negar</button>
              </form>
            </td>
          </tr>
        {% else %}
          <tr><td colspan="6">Nenhuma solicitação pendente.</td></tr>
        {% endfor %}
      </table>
    </div>

    <div class="card">
      <h3>Solicitações Negadas</h3>
      <table>
        <tr><th>Data</th><th>Nome</th><th>Email</th><th>Ações</th></tr>
        {% for req in denied_requests %}
          <tr>
            <td>{{ req.criado_em.strftime('%d/%m/%Y %H:%M') }}</td>
            <td>{{ req.nome }}</td>
            <td>{{ req.email }}</td>
            <td>
              <form method="post" action="{{ url_for('remove_denied_request', request_id=req.id) }}" style="display:inline;">
                <button type="submit" class="btn btn-danger" onclick="return confirm('Tem certeza que deseja remover esta solicitação negada?');">Remover</button>
              </form>
            </td>
          </tr>
        {% else %}
          <tr><td colspan="4">Nenhuma solicitação negada.</td></tr>
        {% endfor %}
      </table>
    </div>

    <div class="card">
      <h3>Convites Pendentes</h3>
      <table>
        <tr><th>Email</th><th>Token</th><th>Ações</th></tr>
        {% for invite in invited_tokens %}
          <tr>
            <td>{{ invite.email }}</td>
            <td>{{ invite.token }}</td>
            <td>
              <form method="post" action="{{ url_for('revoke_invite', invite_id=invite.id) }}" style="display:inline;">
                <button type="submit" class="btn btn-danger" onclick="return confirm('Tem certeza que deseja revogar este convite?');">Revogar</button>
              </form>
            </td>
          </tr>
        {% else %}
          <tr><td colspan="3">Nenhum convite pendente.</td></tr>
        {% endfor %}
      </table>
    </div>

    <div class="card">
      <h3>Usuários Ativos</h3>
      <table>
        <tr><th>Nome</th><th>Email</th><th>Segmento</th><th>Cooperativa</th><th>Status</th><th>Ações</th></tr>
        {% for user in active_users %}
          <tr>
            <td>{{ user.nome }}</td>
            <td>{{ user.email }}</td>
            <td>{{ user.segmento|capitalize }}</td>
            <td>{{ user.cooperativa or '-' }}</td>
            <td>{{ user.status|capitalize }}</td>
            <td>
              {% if user.status == 'ativo' %}
                <form method="post" action="{{ url_for('block_user', user_id=user.id) }}" style="display:inline;">
                  <button type="submit" class="btn btn-danger" onclick="return confirm('Tem certeza que deseja bloquear este usuário?');">Bloquear</button>
                </form>
              {% else %}
                <span class="muted">Bloqueado</span>
              {% endif %}
            </td>
          </tr>
        {% endfor %}
      </table>
    </div>

    <div class="card">
      <h3>Benchmarks da Cooperativa</h3>
      <form method="post" action="{{ url_for('add_coop_benchmark') }}">
        <label>Cadeia</label>
        <select name="cadeia">
          <option value="avicultura">Avicultura</option>
          <option value="suinocultura">Suinocultura</option>
        </select>
        <label>Cooperativa</label>
        <input name="cooperativa" placeholder="Nome da Cooperativa" required>
        <label>GPD Médio</label>
        <input type="number" step="0.0001" name="media_gpd" placeholder="Ex: 0.065" required>
        <label>CA Média</label>
        <input type="number" step="0.0001" name="media_ca" placeholder="Ex: 1.70" required>
        <label>Bônus Base (R$)</label>
        <input type="number" step="0.01" name="bonus_base" placeholder="Ex: 1000.00" required>
        <button class="btn btn-ok" type="submit">Adicionar Benchmark</button>
      </form>
      <br>
      <table>
        <tr><th>Cooperativa</th><th>Cadeia</th><th>GPD Médio</th><th>CA Média</th><th>Bônus Base</th><th>Ações</th></tr>
        {% for bm in coop_benchmarks %}
          <tr>
            <td>{{ bm.cooperativa }}</td>
            <td>{{ bm.cadeia|capitalize }}</td>
            <td>{{ bm.media_gpd }}</td>
            <td>{{ bm.media_ca }}</td>
            <td>{{ bm.bonus_base }}</td>
            <td>
              <form method="post" action="{{ url_for('delete_coop_benchmark', bm_id=bm.id) }}" style="display:inline;">
                <button type="submit" class="btn btn-danger" onclick="return confirm('Tem certeza que deseja excluir este benchmark?');">Excluir</button>
              </form>
            </td>
          </tr>
        {% endfor %}
      </table>
    </div>
    """
    return page(html_content, title="AP360 | Admin", access_requests=access_requests, denied_requests=denied_requests,
                active_users=active_users, invited_tokens=invited_tokens, coop_benchmarks=coop_benchmarks)


@app.route("/admin/approve/<int:request_id>", methods=["POST"])
@admin_required
def approve_request(request_id):
    access_request = AccessRequest.query.get_or_404(request_id)
    if access_request.status == "pendente":
        # Cria um token de convite
        token = secrets.token_urlsafe(32)
        new_invite = AccessInvite(email=access_request.email, token=token, request_id=access_request.id)
        db.session.add(new_invite)

        access_request.status = "liberado" # Marca a solicitação como liberada
        db.session.commit()
        flash(f"Solicitação de {access_request.email} aprovada. Um convite foi enviado para registro: {url_for('register', token=token, _external=True)}")
    else:
        flash("Esta solicitação já foi processada.")
    return redirect(url_for("admin_panel"))


@app.route("/admin/deny/<int:request_id>", methods=["POST"])
@admin_required
def deny_request(request_id):
    access_request = AccessRequest.query.get_or_404(request_id)
    if access_request.status == "pendente":
        access_request.status = "negado"
        db.session.commit()
        flash(f"Solicitação de {access_request.email} negada.")
    else:
        flash("Esta solicitação já foi processada.")
    return redirect(url_for("admin_panel"))


@app.route("/admin/remove_denied/<int:request_id>", methods=["POST"])
@admin_required
def remove_denied_request(request_id):
    access_request = AccessRequest.query.get_or_404(request_id)
    if access_request.status == "negado":
        db.session.delete(access_request)
        db.session.commit()
        flash(f"Solicitação negada de {access_request.email} removida.")
    else:
        flash("Esta solicitação não está no status 'negado' ou não existe.")
    return redirect(url_for("admin_panel"))


@app.route("/admin/revoke_invite/<int:invite_id>", methods=["POST"])
@admin_required
def revoke_invite(invite_id):
    invite = AccessInvite.query.get_or_404(invite_id)
    if invite.status == "convidado":
        invite.status = "revogado"
        db.session.commit()
        flash(f"Convite para {invite.email} revogado.")
    else:
        flash("Este convite não está pendente ou não existe.")
    return redirect(url_for("admin_panel"))


@app.route("/admin/block_user/<int:user_id>", methods=["POST"])
@admin_required
def block_user(user_id):
    user = User.query.get_or_404(user_id)
    if user.perfil != "admin": # Não permite bloquear o próprio admin
        user.status = "bloqueado"
        db.session.commit()
        flash(f"Usuário {user.email} bloqueado.")
    else:
        flash("Não é possível bloquear um administrador.")
    return redirect(url_for("admin_panel"))


@app.route("/admin/add_coop_benchmark", methods=["POST"])
@admin_required
def add_coop_benchmark():
    try:
        cadeia = request.form.get("cadeia")
        cooperativa = request.form.get("cooperativa").strip()
        media_gpd = float(request.form.get("media_gpd"))
        media_ca = float(request.form.get("media_ca"))
        bonus_base = float(request.form.get("bonus_base"))

        existing_bm = CoopBenchmark.query.filter_by(cadeia=cadeia, cooperativa=cooperativa).first()
        if existing_bm:
            existing_bm.media_gpd = media_gpd
            existing_bm.media_ca = media_ca
            existing_bm.bonus_base = bonus_base
            existing_bm.atualizado_em = datetime.utcnow()
            flash(f"Benchmark para {cooperativa} ({cadeia}) atualizado com sucesso!")
        else:
            new_bm = CoopBenchmark(
                cadeia=cadeia,
                cooperativa=cooperativa,
                media_gpd=media_gpd,
                media_ca=media_ca,
                bonus_base=bonus_base
            )
            db.session.add(new_bm)
            flash(f"Benchmark para {cooperativa} ({cadeia}) adicionado com sucesso!")
        db.session.commit()
    except ValueError:
        flash("Erro: Verifique se os valores numéricos estão corretos para o benchmark.")
    except Exception as e:
        flash(f"Ocorreu um erro ao adicionar/atualizar o benchmark: {e}")
    return redirect(url_for("admin_panel"))


@app.route("/admin/delete_coop_benchmark/<int:bm_id>", methods=["POST"])
@admin_required
def delete_coop_benchmark(bm_id):
    bm = CoopBenchmark.query.get_or_404(bm_id)
    db.session.delete(bm)
    db.session.commit()
    flash(f"Benchmark de {bm.cooperativa} ({bm.cadeia}) excluído.")
    return redirect(url_for("admin_panel"))


# =========================================================
# AGRICULTURA
# =========================================================
@app.route("/agricultura")
@login_required
def agricultura_modulo():
    if current_user.segmento != "agricultura":
        flash("Seu perfil não tem acesso ao módulo de agricultura.")
        return redirect(url_for("dashboard"))

    fields = AgricultureField.query.filter_by(user_id=current_user.id).order_by(AgricultureField.data_plantio.desc()).all()

    html_content = """
    <h2>Agricultura</h2>

    <div class="grid">
      <div class="card">
        <h3>Novo Campo de Produção</h3>
        <form method="post" action="{{ url_for('add_agriculture_field') }}">
          <input name="nome_campo" placeholder="Nome do Campo (Ex: Fazenda A - Talhão 1)" required>
          <input name="cultura" placeholder="Cultura (Ex: Soja, Milho)" required>
          <label>Área (hectares)</label>
          <input type="number" step="0.01" name="area_ha" placeholder="Ex: 50.5" required>
          <label>Data de Plantio</label>
          <input type="date" name="data_plantio" required>
          <label>Data de Colheita Prevista</label>
          <input type="date" name="data_colheita_prevista">
          <label>Produtividade Esperada (ton/ha)</label>
          <input type="number" step="0.01" name="produtividade_esperada_ton_ha" placeholder="Ex: 3.5">
          <textarea name="observacoes" placeholder="Observações"></textarea>
          <button class="btn btn-ok" type="submit">Salvar Campo</button>
        </form>
      </div>

      <div class="card">
        <h3>Meus Campos de Produção</h3>
        <table>
          <tr><th>Campo</th><th>Cultura</th><th>Área (ha)</th><th>Plantio</th><th>Status</th><th>Ações</th></tr>
          {% for field in fields %}
            <tr>
              <td>{{ field.nome_campo }}</td>
              <td>{{ field.cultura }}</td>
              <td>{{ "%.2f"|format(field.area_ha) }}</td>
              <td>{{ field.data_plantio.strftime('%d/%m/%Y') }}</td>
              <td>{{ field.status|capitalize }}</td>
              <td>
                <a class="btn btn-ghost" href="{{ url_for('detalhes_agriculture_field', field_id=field.id) }}">Detalhes</a>
                <a class="btn btn-ghost" href="{{ url_for('editar_agriculture_field', field_id=field.id) }}">Editar</a>
                <form method="post" action="{{ url_for('excluir_agriculture_field', field_id=field.id) }}" style="display:inline;">
                  <button type="submit" class="btn btn-danger" onclick="return confirm('Tem certeza que deseja excluir este campo e todos os seus registros?');">Excluir</button>
                </form>
              </td>
            </tr>
          {% else %}
            <tr><td colspan="6">Nenhum campo de produção cadastrado ainda.</td></tr>
          {% endfor %}
        </table>
      </div>
    </div>
    """
    return page(html_content, title="AP360 | Agricultura", fields=fields)


@app.route("/agricultura/add_field", methods=["POST"])
@login_required
def add_agriculture_field():
    if current_user.segmento != "agricultura":
        flash("Seu perfil não tem acesso ao módulo de agricultura.")
        return redirect(url_for("dashboard"))
    try:
        data_plantio_str = request.form.get("data_plantio")
        data_colheita_prevista_str = request.form.get("data_colheita_prevista")

        new_field = AgricultureField(
            user_id=current_user.id,
            nome_campo=request.form.get("nome_campo").strip(),
            cultura=request.form.get("cultura").strip(),
            area_ha=float(request.form.get("area_ha")),
            data_plantio=datetime.strptime(data_plantio_str, "%Y-%m-%d").date(),
            data_colheita_prevista=datetime.strptime(data_colheita_prevista_str, "%Y-%m-%d").date() if data_colheita_prevista_str else None,
            produtividade_esperada_ton_ha=float(request.form.get("produtividade_esperada_ton_ha", 0) or 0),
            observacoes=request.form.get("observacoes", "").strip()
        )
        db.session.add(new_field)
        db.session.commit()
        flash("Campo de produção adicionado com sucesso!")
    except ValueError as e:
        flash(f"Erro nos dados de entrada: {e}. Verifique se todos os campos numéricos e de data estão corretos.")
    except Exception as e:
        flash(f"Ocorreu um erro inesperado: {e}")
    return redirect(url_for("agricultura_modulo"))


@app.route("/agricultura/editar_field/<int:field_id>", methods=["GET", "POST"])
@login_required
def editar_agriculture_field(field_id):
    if current_user.segmento != "agricultura":
        flash("Seu perfil não tem acesso ao módulo de agricultura.")
        return redirect(url_for("dashboard"))

    field = AgricultureField.query.filter_by(id=field_id, user_id=current_user.id).first_or_404()

    if request.method == "POST":
        try:
            field.nome_campo = request.form.get("nome_campo").strip()
            field.cultura = request.form.get("cultura").strip()
            field.area_ha = float(request.form.get("area_ha"))
            field.data_plantio = datetime.strptime(request.form.get("data_plantio"), "%Y-%m-%d").date()
            data_colheita_prevista_str = request.form.get("data_colheita_prevista")
            field.data_colheita_prevista = datetime.strptime(data_colheita_prevista_str, "%Y-%m-%d").date() if data_colheita_prevista_str else None
            field.produtividade_esperada_ton_ha = float(request.form.get("produtividade_esperada_ton_ha", 0) or 0)
            field.observacoes = request.form.get("observacoes", "").strip()
            field.status = request.form.get("status", "plantado").strip()

            db.session.commit()
            flash("Campo de produção atualizado com sucesso!")
        except ValueError as e:
            flash(f"Erro nos dados de entrada: {e}. Verifique se todos os campos numéricos e de data estão corretos.")
        except Exception as e:
            flash(f"Ocorreu um erro inesperado: {e}")
        return redirect(url_for("agricultura_modulo"))

    html_content = f"""
    <h2>Editar Campo de Produção</h2>
    <div class="card" style="max-width:700px;margin:0 auto">
      <form method="post">
        <label>Nome do Campo</label>
        <input name="nome_campo" value="{field.nome_campo}" required>
        <label>Cultura</label>
        <input name="cultura" value="{field.cultura}" required>
        <label>Área (hectares)</label>
        <input type="number" step="0.01" name="area_ha" value="{field.area_ha}" required>
        <label>Data de Plantio</label>
        <input type="date" name="data_plantio" value="{field.data_plantio.strftime('%Y-%m-%d')}" required>
        <label>Data de Colheita Prevista</label>
        <input type="date" name="data_colheita_prevista" value="{field.data_colheita_prevista.strftime('%Y-%m-%d') if field.data_colheita_prevista else ''}">
        <label>Produtividade Esperada (ton/ha)</label>
        <input type="number" step="0.01" name="produtividade_esperada_ton_ha" value="{field.produtividade_esperada_ton_ha}">
        <label>Status</label>
        <select name="status">
          <option value="plantado" {% if field.status == 'plantado' %}selected{% endif %}>Plantado</option>
          <option value="crescendo" {% if field.status == 'crescendo' %}selected{% endif %}>Crescendo</option>
          <option value="colhendo" {% if field.status == 'colhendo' %}selected{% endif %}>Colhendo</option>
          <option value="colhido" {% if field.status == 'colhido' %}selected{% endif %}>Colhido</option>
        </select>
        <label>Observações</label>
        <textarea name="observacoes">{field.observacoes or ''}</textarea>
        <button class="btn btn-ok" type="submit">Salvar Alterações</button>
        <a class="btn btn-ghost" href="{{ url_for('agricultura_modulo') }}">Cancelar</a>
      </form>
    </div>
    """
    return page(html_content, title="AP360 | Editar Campo", field=field)


@app.route("/agricultura/excluir_field/<int:field_id>", methods=["POST"])
@login_required
def excluir_agriculture_field(field_id):
    if current_user.segmento != "agricultura":
        flash("Seu perfil não tem acesso ao módulo de agricultura.")
        return redirect(url_for("dashboard"))
    field = AgricultureField.query.filter_by(id=field_id, user_id=current_user.id).first_or_404()
    AgricultureDailyRecord.query.filter_by(field_id=field.id).delete() # Exclui registros diários do campo
    db.session.delete(field)
    db.session.commit()
    flash("Campo de produção e seus registros excluídos com sucesso!")
    return redirect(url_for("agricultura_modulo"))


@app.route("/agricultura/field/<int:field_id>", methods=["GET", "POST"])
@login_required
def detalhes_agriculture_field(field_id):
    if current_user.segmento != "agricultura":
        flash("Seu perfil não tem acesso ao módulo de agricultura.")
        return redirect(url_for("dashboard"))

    field = AgricultureField.query.filter_by(id=field_id, user_id=current_user.id).first_or_404()
    daily_records = AgricultureDailyRecord.query.filter_by(field_id=field.id).order_by(AgricultureDailyRecord.data_registro.asc()).all()

    if request.method == "POST":
        form_type = request.form.get("form_type")
        if form_type == "novo_registro_diario_agricultura":
            try:
                data_registro = datetime.strptime(request.form.get("data_registro"), "%Y-%m-%d").date()
                if AgricultureDailyRecord.query.filter_by(field_id=field.id, data_registro=data_registro).first():
                    flash("Já existe um registro para esta data neste campo.")
                    return redirect(url_for("detalhes_agriculture_field", field_id=field.id))

                record = AgricultureDailyRecord(
                    field_id=field.id,
                    data_registro=data_registro,
                    chuva_mm=float(request.form.get("chuva_mm", 0) or 0),
                    temperatura_c=float(request.form.get("temperatura_c", 0) or 0),
                    insumo_aplicado=request.form.get("insumo_aplicado", "").strip(),
                    quantidade_insumo=float(request.form.get("quantidade_insumo", 0) or 0),
                    produtividade_parcial_ton_ha=float(request.form.get("produtividade_parcial_ton_ha", 0) or 0),
                    observacoes=request.form.get("observacoes", "").strip()
                )
                db.session.add(record)
                db.session.commit()
                flash("Registro diário adicionado com sucesso!")
            except ValueError:
                flash("Erro: Verifique se os valores numéricos e de data estão corretos.")
            except Exception as e:
                flash(f"Ocorreu um erro ao adicionar o registro: {e}")
            return redirect(url_for("detalhes_agriculture_field", field_id=field.id))

        elif form_type == "agrosim_predict":
            # Lógica de simulação Agrosim (simplificada)
            # Isso é um exemplo básico. Um Agrosim real seria muito mais complexo.
            try:
                # Parâmetros da simulação (poderiam vir do formulário ou ser fixos)
                dias_crescimento = (datetime.now().date() - field.data_plantio).days
                if dias_crescimento <= 0:
                    flash("O campo ainda não começou a crescer.")
                    return redirect(url_for("detalhes_agriculture_field", field_id=field.id))

                # Exemplo de cálculo de produtividade baseada em dias e produtividade esperada
                # Isso é uma simplificação extrema!
                produtividade_simulada = (field.produtividade_esperada_ton_ha / field.data_colheita_prevista.day) * dias_crescimento if field.data_colheita_prevista else (field.produtividade_esperada_ton_ha / 100) * dias_crescimento
                produtividade_simulada = min(produtividade_simulada, field.produtividade_esperada_ton_ha * 1.2) # Limita a um teto

                flash(f"Simulação Agrosim: Produtividade atual estimada em {produtividade_simulada:.2f} ton/ha.")
            except Exception as e:
                flash(f"Erro na simulação Agrosim: {e}")
            return redirect(url_for("detalhes_agriculture_field", field_id=field.id))


    # Preparar dados para gráficos
    chart_labels = [r.data_registro.strftime('%d/%m') for r in daily_records]
    chart_chuva = [r.chuva_mm for r in daily_records]
    chart_temperatura = [r.temperatura_c for r in daily_records]
    chart_produtividade_parcial = [r.produtividade_parcial_ton_ha for r in daily_records]

    html_content = f"""
    <h2>Detalhes do Campo: {field.nome_campo} ({field.cultura})</h2>
    <div class="card">
      <p><b>Área:</b> {"%.2f"|format(field.area_ha)} ha</p>
      <p><b>Data de Plantio:</b> {field.data_plantio.strftime('%d/%m/%Y')}</p>
      <p><b>Colheita Prevista:</b> {field.data_colheita_prevista.strftime('%d/%m/%Y') if field.data_colheita_prevista else '-'}</p>
      <p><b>Produtividade Esperada:</b> {"%.2f"|format(field.produtividade_esperada_ton_ha)} ton/ha</p>
      <p><b>Status:</b> {field.status|capitalize}</p>
      <p><b>Observações:</b> {field.observacoes or '-'}</p>
      <a class="btn btn-ghost" href="{{ url_for('agricultura_modulo') }}">Voltar para Campos</a>
    </div>

    <div class="grid">
      <div class="card">
        <h3>Adicionar Registro Diário</h3>
        <form method="post">
          <input type="hidden" name="form_type" value="novo_registro_diario_agricultura">
          <label>Data do Registro</label>
          <input type="date" name="data_registro" required>
          <input type="number" step="0.01" name="chuva_mm" placeholder="Chuva (mm)">
          <input type="number" step="0.01" name="temperatura_c" placeholder="Temperatura (°C)">
          <input name="insumo_aplicado" placeholder="Insumo Aplicado (Ex: Ureia)">
          <input type="number" step="0.01" name="quantidade_insumo" placeholder="Quantidade Insumo (kg/ha)">
          <input type="number" step="0.01" name="produtividade_parcial_ton_ha" placeholder="Produtividade Parcial (ton/ha)">
          <textarea name="observacoes" placeholder="Observações"></textarea>
          <button class="btn btn-ok" type="submit">Salvar Registro</button>
        </form>
      </div>
      <div class="card">
        <h3>Agrosim - Simulação de Crescimento</h3>
        <p class="muted">Obtenha uma estimativa da produtividade atual do seu campo com base nos dados registrados.</p>
        <form method="post">
          <input type="hidden" name="form_type" value="agrosim_predict">
          <button class="btn btn-pri" type="submit">Simular Produtividade</button>
        </form>
        <br>
        <h3>Gráficos de Acompanhamento</h3>
        <div class="grid">
          {% if chart_labels %}
          <div>
            <h4>Chuva (mm)</h4>
            <canvas id="chuvaChart" height="150"></canvas>
          </div>
          <div>
            <h4>Temperatura (°C)</h4>
            <canvas id="temperaturaChart" height="150"></canvas>
          </div>
          <div>
            <h4>Produtividade Parcial (ton/ha)</h4>
            <canvas id="produtividadeParcialChart" height="150"></canvas>
          </div>
          {% else %}
            <p class="muted">Adicione registros diários para ver os gráficos.</p>
          {% endif %}
        </div>
        <script>
          const chartLabels = {{ chart_labels | tojson }};
          const chartChuva = {{ chart_chuva | tojson }};
          const chartTemperatura = {{ chart_temperatura | tojson }};
          const chartProdutividadeParcial = {{ chart_produtividade_parcial | tojson }};

          if (chartLabels.length > 0) {
            new Chart(document.getElementById("chuvaChart"), {
              type: "bar",
              data: { labels: chartLabels, datasets: [{ label: "Chuva (mm)", data: chartChuva, backgroundColor: 'rgba(59, 185, 255, 0.5)' }] },
              options: { responsive: true, scales: { y: { beginAtZero: true } } }
            });
            new Chart(document.getElementById("temperaturaChart"), {
              type: "line",
              data: { labels: chartLabels, datasets: [{ label: "Temperatura (°C)", data: chartTemperatura, borderColor: 'rgba(255, 99, 132, 1)', tension: 0.2 }] },
              options: { responsive: true, scales: { y: { beginAtZero: true } } }
            });
            new Chart(document.getElementById("produtividadeParcialChart"), {
              type: "line",
              data: { labels: chartLabels, datasets: [{ label: "Produtividade Parcial (ton/ha)", data: chartProdutividadeParcial, borderColor: 'rgba(70, 221, 152, 1)', tension: 0.2 }] },
              options: { responsive: true, scales: { y: { beginAtZero: true } } }
            });
          }
        </script>
      </div>
    </div>

    <div class="card">
      <h3>Histórico de Registros Diários</h3>
      <table>
        <tr><th>Data</th><th>Chuva (mm)</th><th>Temp (°C)</th><th>Insumo</th><th>Qtd Insumo</th><th>Prod. Parcial (ton/ha)</th><th>Observações</th><th>Ações</th></tr>
        {% for record in daily_records %}
          <tr>
            <td>{{ record.data_registro.strftime('%d/%m/%Y') }}</td>
            <td>{{ record.chuva_mm }}</td>
            <td>{{ record.temperatura_c }}</td>
            <td>{{ record.insumo_aplicado or '-' }}</td>
            <td>{{ record.quantidade_insumo }}</td>
            <td>{{ record.produtividade_parcial_ton_ha }}</td>
            <td>{{ record.observacoes or '-' }}</td>
            <td>
              <form method="post" action="{{ url_for('excluir_registro_diario_agricultura', record_id=record.id) }}" style="display:inline;">
                <button type="submit" class="btn btn-danger" onclick="return confirm('Tem certeza que deseja excluir este registro?');">Excluir</button>
              </form>
            </td>
          </tr>
        {% else %}
          <tr><td colspan="8">Nenhum registro diário para este campo ainda.</td></tr>
        {% endfor %}
      </table>
    </div>
    """
    return page(html_content, title="AP360 | Detalhes do Campo", field=field,
                chart_labels=chart_labels, chart_chuva=chart_chuva,
                chart_temperatura=chart_temperatura, chart_produtividade_parcial=chart_produtividade_parcial)


@app.route("/agricultura/registro_diario/excluir/<int:record_id>", methods=["POST"])
@login_required
def excluir_registro_diario_agricultura(record_id):
    if current_user.segmento != "agricultura":
        flash("Seu perfil não tem acesso ao módulo de agricultura.")
        return redirect(url_for("dashboard"))
    record = AgricultureDailyRecord.query.get_or_404(record_id)
    field_id = record.field_id
    field = AgricultureField.query.filter_by(id=field_id, user_id=current_user.id).first_or_404() # Garante que o usuário é o dono do campo
    db.session.delete(record)
    db.session.commit()
    flash("Registro diário excluído com sucesso!")
    return redirect(url_for("detalhes_agriculture_field", field_id=field_id))


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