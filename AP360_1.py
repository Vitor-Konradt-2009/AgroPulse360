import os
import io
import csv
import secrets
from datetime import datetime, timedelta
from functools import wraps
import pytz # Para lidar com fusos horários

from flask import (
    Flask, request, redirect, url_for, flash,
    render_template, jsonify, Response, abort
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


def calc_iep_avicultura(viabilidade_pct: float, peso_final_medio: float, dias: float, ca_ajustada: float) -> float:
    """Calcula o Índice de Eficiência Produtiva (IEP) para avicultura."""
    if ca_ajustada <= 0 or dias <= 0:
        return 0.0
    iep = (viabilidade_pct * peso_final_medio) / (dias * ca_ajustada)
    return round(iep, 2)


def calc_rendimento_carcaca(peso_vivo_medio: float, peso_carcaca_medio: float) -> float:
    """Calcula o rendimento de carcaça para suínos."""
    if peso_vivo_medio <= 0:
        return 0.0
    return round((peso_carcaca_medio / peso_vivo_medio) * 100.0, 2)


def calc_indice_lote_suino(gpd: float, viabilidade_pct: float, ca_ajustada: float) -> float:
    """Calcula o Índice de Lote para suínos."""
    if ca_ajustada <= 0:
        return 0.0
    indice = (gpd * viabilidade_pct) / ca_ajustada
    return round(indice, 2)


def calc_bonus_tipificacao(carne_magra_pct: float, rendimento_carcaca_pct: float) -> float:
    """Calcula um bônus de tipificação para suínos (exemplo simplificado)."""
    bonus = 0.0
    if carne_magra_pct >= 60 and rendimento_carcaca_pct >= 78:
        bonus = 50.0 # Exemplo de bônus fixo
    elif carne_magra_pct >= 55 and rendimento_carcaca_pct >= 75:
        bonus = 20.0
    return bonus


def calc_bonificacao(gpd_lote: float, ca_lote: float, gpd_referencia: float, ca_referencia: float, bonus_base: float) -> float:
    """Calcula a bonificação baseada em GPD e CA."""
    if gpd_referencia <= 0 or ca_referencia <= 0:
        return 0.0

    fator_gpd = (gpd_lote / gpd_referencia) if gpd_referencia > 0 else 0
    fator_ca = (ca_referencia / ca_lote) if ca_lote > 0 else 0

    # Ponderação simples: 50% GPD, 50% CA
    fator_total = (fator_gpd + fator_ca) / 2

    bonificacao = bonus_base * fator_total
    return round(bonificacao, 2)


def get_effective_benchmark(cadeia: str, cooperativa: str, user: User):
    """Obtém os benchmarks mais relevantes (cooperativa > usuário > padrão)."""
    coop_bm = CoopBenchmark.query.filter_by(cadeia=cadeia, cooperativa=cooperativa).first()

    if cadeia == "avicultura":
        gpd_ref = coop_bm.media_gpd if coop_bm else user.user_avicultura_gpd if user.user_avicultura_gpd > 0 else 0.045 # Padrão
        ca_ref = coop_bm.media_ca if coop_bm else user.user_avicultura_ca if user.user_avicultura_ca > 0 else 1.70 # Padrão
        bonus_base = coop_bm.bonus_base if coop_bm else user.user_avicultura_bonus_base if user.user_avicultura_bonus_base > 0 else 1000.0
    elif cadeia == "suinocultura":
        gpd_ref = coop_bm.media_gpd if coop_bm else user.user_suinocultura_gpd if user.user_suinocultura_gpd > 0 else 0.800 # Padrão
        ca_ref = coop_bm.media_ca if coop_bm else user.user_suinocultura_ca if user.user_suinocultura_ca > 0 else 2.50 # Padrão
        bonus_base = coop_bm.bonus_base if coop_bm else user.user_suinocultura_bonus_base if user.user_suinocultura_bonus_base > 0 else 1500.0
    else: # Default para outras cadeias ou caso não encontre
        gpd_ref = 0.0
        ca_ref = 0.0
        bonus_base = 0.0

    return gpd_ref, ca_ref, bonus_base


# =========================================================
# ROTAS GERAIS
# =========================================================
@app.route("/")
def index():
    return render_template("index.html", title="AP360 | Início") # Renderiza um template específico para a página inicial


@app.route("/dashboard")
@login_required
def dashboard():
    # Exemplo de dados para o dashboard
    total_lotes = Batch.query.filter_by(user_id=current_user.id).count()
    total_campos = AgricultureField.query.filter_by(user_id=current_user.id).count()

    # Exemplo de KPIs (você pode expandir muito isso!)
    latest_avicultura_batch = Batch.query.filter_by(user_id=current_user.id, cadeia="avicultura").order_by(Batch.criado_em.desc()).first()
    latest_suinocultura_batch = Batch.query.filter_by(user_id=current_user.id, cadeia="suinocultura").order_by(Batch.criado_em.desc()).first()
    latest_agriculture_field = AgricultureField.query.filter_by(user_id=current_user.id).order_by(AgricultureField.criado_em.desc()).first()

    return render_template("dashboard.html",
                           title="AP360 | Dashboard",
                           total_lotes=total_lotes,
                           total_campos=total_campos,
                           latest_avicultura_batch=latest_avicultura_batch,
                           latest_suinocultura_batch=latest_suinocultura_batch,
                           latest_agriculture_field=latest_agriculture_field)


# =========================================================
# AUTHENTICATION
# =========================================================
@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        email = request.form.get("email").strip().lower()
        password = request.form.get("password")
        user = User.query.filter_by(email=email).first()

        if user and user.check_password(password):
            if user.status == "ativo":
                login_user(user)
                flash(f"Bem-vindo, {user.nome}!")
                return redirect(url_for("dashboard"))
            elif user.status == "bloqueado":
                flash("Sua conta está bloqueada. Entre em contato com o administrador.")
            else:
                flash("Sua conta não está ativa. Entre em contato com o administrador.")
        else:
            flash("Email ou senha inválidos.")
    return render_template("auth/login.html", title="AP360 | Login")


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
        nome = request.form.get("nome").strip()
        email = request.form.get("email").strip().lower()
        cpf = request.form.get("cpf", "").strip()
        telefone = request.form.get("telefone", "").strip()
        segmento = request.form.get("segmento", "agricultura").strip()
        cooperativa = request.form.get("cooperativa", "").strip()

        if User.query.filter_by(email=email).first() or AccessRequest.query.filter_by(email=email, status="pendente").first():
            flash("Já existe uma conta ou solicitação de acesso com este email.")
            return redirect(url_for("signup_request"))

        new_request = AccessRequest(
            nome=nome,
            email=email,
            cpf=cpf,
            telefone=telefone,
            segmento=segmento,
            cooperativa=cooperativa
        )
        db.session.add(new_request)
        db.session.commit()
        flash("Sua solicitação de acesso foi enviada e será revisada pelo administrador. Entraremos em contato!")
        return redirect(url_for("index"))

    # Link para WhatsApp mais proeminente
    whatsapp_link = "https://wa.me/5545999999999?text=Ol%C3%A1%2C%20gostaria%20de%20saber%20mais%20sobre%20o%20AP360!"
    return render_template("auth/signup_request.html", title="AP360 | Solicitar Acesso", whatsapp_link=whatsapp_link)


@app.route("/invite/<token>", methods=["GET", "POST"])
def invite_signup(token):
    invite = AccessInvite.query.filter_by(token=token, status="convidado").first_or_404()

    if current_user.is_authenticated:
        flash("Você já está logado.")
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        password = request.form.get("password")
        confirm_password = request.form.get("confirm_password")

        if password != confirm_password:
            flash("As senhas não coincidem.")
            return redirect(url_for("invite_signup", token=token))

        # Recupera os dados da solicitação original, se houver
        access_request = AccessRequest.query.get(invite.request_id) if invite.request_id else None

        new_user = User(
            email=invite.email,
            nome=access_request.nome if access_request else "Usuário Convidado",
            cpf=access_request.cpf if access_request else None,
            telefone=access_request.telefone if access_request else None,
            segmento=access_request.segmento if access_request else "produtor", # Default se não houver request
            cooperativa=access_request.cooperativa if access_request else None,
            status="ativo" # Usuário convidado já é ativo
        )
        new_user.set_password(password)
        db.session.add(new_user)

        invite.status = "ativado"
        invite.ativado_em = datetime.utcnow()
        db.session.commit()

        login_user(new_user)
        flash(f"Bem-vindo, {new_user.nome}! Sua conta foi ativada com sucesso.")
        return redirect(url_for("dashboard"))

    return render_template("auth/invite_signup.html", title="AP360 | Ativar Conta", invite=invite)


# =========================================================
# ADMIN PANEL
# =========================================================
@app.route("/admin")
@admin_required
def admin_panel():
    access_requests = AccessRequest.query.filter_by(status="pendente").order_by(AccessRequest.criado_em.asc()).all()
    denied_requests = AccessRequest.query.filter_by(status="negado").order_by(AccessRequest.criado_em.desc()).all()
    active_users = User.query.filter_by(status="ativo").order_by(User.criado_em.desc()).all()
    blocked_users = User.query.filter_by(status="bloqueado").order_by(User.criado_em.desc()).all()
    pending_invites = AccessInvite.query.filter_by(status="convidado").order_by(AccessInvite.criado_em.desc()).all()
    coop_benchmarks = CoopBenchmark.query.order_by(CoopBenchmark.cadeia, CoopBenchmark.cooperativa).all()

    return render_template("admin/admin_panel.html",
                           title="AP360 | Painel Admin",
                           access_requests=access_requests,
                           denied_requests=denied_requests,
                           active_users=active_users,
                           blocked_users=blocked_users,
                           pending_invites=pending_invites,
                           coop_benchmarks=coop_benchmarks)


@app.route("/admin/approve_request/<int:request_id>", methods=["POST"])
@admin_required
def approve_request(request_id):
    req = AccessRequest.query.get_or_404(request_id)
    req.status = "liberado"

    # Cria um token de convite para o usuário definir a senha
    token = secrets.token_urlsafe(32)
    new_invite = AccessInvite(email=req.email, token=token, request_id=req.id)
    db.session.add(new_invite)
    db.session.commit()

    flash(f"Solicitação de {req.email} aprovada. Um convite foi gerado para ele definir a senha.")
    # Em um sistema real, você enviaria este token por email para o usuário.
    # Por enquanto, vamos exibir o link para facilitar o teste.
    flash(f"Link de convite (APENAS PARA TESTE): {url_for('invite_signup', token=token, _external=True)}")
    return redirect(url_for("admin_panel"))


@app.route("/admin/deny_request/<int:request_id>", methods=["POST"])
@admin_required
def deny_request(request_id):
    req = AccessRequest.query.get_or_404(request_id)
    req.status = "negado"
    db.session.commit()
    flash(f"Solicitação de {req.email} negada.")
    return redirect(url_for("admin_panel"))


@app.route("/admin/remove_denied_request/<int:request_id>", methods=["POST"])
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

    return render_template("agricultura/agricultura_modulo.html",
                           title="AP360 | Agricultura",
                           fields=fields)


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

    return render_template("agricultura/editar_agriculture_field.html",
                           title="AP360 | Editar Campo",
                           field=field)


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

    return render_template("agricultura/detalhes_agriculture_field.html",
                           title="AP360 | Detalhes do Campo",
                           field=field,
                           daily_records=daily_records,
                           chart_labels=chart_labels,
                           chart_chuva=chart_chuva,
                           chart_temperatura=chart_temperatura,
                           chart_produtividade_parcial=chart_produtividade_parcial)


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
@app.route("/ia")
@login_required
def ia_page():
    return render_template("ia.html", title="AP360 | IA")


@app.route("/ia/chat", methods=["POST"])
@login_required
def ia_chat():
    txt = (request.form.get("mensagem", "") or "").strip()
    if not txt:
        return jsonify({"resposta": "Digite sua pergunta."})
    dicas = [
        "Monitore GPD e CAA semanalmente para agir antes da perda de margem.",
        "Padronize coleta por estrutura/lote para comparação justa.",
        "Na agricultura, compare sempre margem líquida por tonelada."
    ]
    resposta_ia = f"AP360 IA: {txt[:170]}. Dica: {dicas[len(txt) % len(dicas)]}"
    return jsonify({"resposta": resposta_ia})


# =========================================================
# ERRORS
# =========================================================
@app.errorhandler(403)
def e403(_):
    return render_template("errors/403.html", title="403"), 403


@app.errorhandler(404)
def e404(_):
    return render_template("errors/404.html", title="404"), 404


@app.errorhandler(500)
def e500(_):
    return render_template("errors/500.html", title="500"), 500


# =========================================================
# RUN
# =========================================================
if __name__ == "__main__":
    app.run(debug=True)