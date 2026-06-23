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
    produtividade_parcial_ton_ha = db.Column(db.Float, default=0.0)
    observacoes = db.Column(db.Text, default="")
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint('field_id', 'data_registro', name='_field_daily_record_uc'),)


# =========================================================
# HELPERS
# =========================================================
@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or current_user.perfil != "admin":
            abort(403)
        return f(*args, **kwargs)
    return decorated_function


def calc_gpd(peso_inicial, peso_final, dias):
    if dias <= 0:
        return 0.0
    return (peso_final - peso_inicial) / dias


def calc_ca(racao_total_kg, animais_final, peso_final, peso_inicial, animais_iniciais):
    if animais_final <= 0 or (peso_final * animais_final - peso_inicial * animais_iniciais) <= 0:
        return 0.0
    return racao_total_kg / (peso_final * animais_final - peso_inicial * animais_iniciais)


def calc_viabilidade(animais_iniciais, animais_final):
    if animais_iniciais <= 0:
        return 0.0
    return (animais_final / animais_iniciais) * 100


def calc_mortalidade(animais_iniciais, animais_final):
    if animais_iniciais <= 0:
        return 0.0
    return ((animais_iniciais - animais_final) / animais_iniciais) * 100


def calc_ca_ajustada_avicultura(ca_real, peso_real, idade_real, peso_meta, idade_meta, fator_peso, fator_idade):
    # CAA = CA real + (peso real - peso meta) * fator peso + (idade real - idade meta) * fator idade
    return ca_real + (peso_real - peso_meta) * fator_peso + (idade_real - idade_meta) * fator_idade


def calc_iep(gpd, viabilidade_pct, ca_ajustada):
    if ca_ajustada <= 0:
        return 0.0
    return (gpd * viabilidade_pct) / (ca_ajustada * 10) # Dividido por 10 para ajustar a escala


def calc_indice_lote(iep, peso_final):
    return iep * peso_final


def calc_rendimento_carcaca(peso_vivo_medio, peso_carcaca_medio):
    if peso_vivo_medio <= 0:
        return 0.0
    return (peso_carcaca_medio / peso_vivo_medio) * 100


def calc_bonus_tipificacao_suinos(carne_magra_pct):
    # Exemplo simplificado de bonificação por carne magra
    if carne_magra_pct >= 60:
        return 150.0
    elif carne_magra_pct >= 57:
        return 100.0
    elif carne_magra_pct >= 54:
        return 50.0
    else:
        return 0.0


def calc_bonificacao_total(cadeia, batch, user_benchmarks, coop_benchmarks):
    bonificacao = 0.0

    # Usar referências do lote se existirem, senão do usuário, senão da cooperativa
    ref_gpd = batch.gpd_coop_ref if batch.gpd_coop_ref > 0 else \
              (user_benchmarks.user_avicultura_gpd if cadeia == 'avicultura' else user_benchmarks.user_suinocultura_gpd)
    ref_ca = batch.ca_coop_ref if batch.ca_coop_ref > 0 else \
             (user_benchmarks.user_avicultura_ca if cadeia == 'avicultura' else user_benchmarks.user_suinocultura_ca)
    ref_bonus_base = (user_benchmarks.user_avicultura_bonus_base if cadeia == 'avicultura' else user_benchmarks.user_suinocultura_bonus_base)

    if ref_gpd == 0 and coop_benchmarks:
        ref_gpd = coop_benchmarks.media_gpd
    if ref_ca == 0 and coop_benchmarks:
        ref_ca = coop_benchmarks.media_ca
    if ref_bonus_base == 0 and coop_benchmarks:
        ref_bonus_base = coop_benchmarks.bonus_base

    # Se ainda não tiver referências, usa valores padrão ou 0
    if ref_gpd == 0: ref_gpd = 0.001 # Evitar divisão por zero
    if ref_ca == 0: ref_ca = 0.001
    if ref_bonus_base == 0: ref_bonus_base = 1000.0 # Valor base para cálculo

    # Armazenar os benchmarks efetivamente usados no lote
    batch.coop_media_gpd = ref_gpd
    batch.coop_media_ca = ref_ca

    if cadeia == 'avicultura':
        # Bonificação baseada em GPD e CA ajustada
        if batch.gpd > ref_gpd and batch.ca_ajustada < ref_ca:
            bonificacao = ref_bonus_base * (batch.gpd / ref_gpd) * (ref_ca / batch.ca_ajustada)
        elif batch.gpd > ref_gpd:
            bonificacao = ref_bonus_base * (batch.gpd / ref_gpd)
        elif batch.ca_ajustada < ref_ca:
            bonificacao = ref_bonus_base * (ref_ca / batch.ca_ajustada)
        else:
            bonificacao = 0.0 # Sem bonificação se não superar benchmarks
    elif cadeia == 'suinocultura':
        # Bonificação baseada em GPD, CA e bônus de tipificação
        bonificacao_gpd_ca = 0.0
        if batch.gpd > ref_gpd and batch.ca < ref_ca:
            bonificacao_gpd_ca = ref_bonus_base * (batch.gpd / ref_gpd) * (ref_ca / batch.ca)
        elif batch.gpd > ref_gpd:
            bonificacao_gpd_ca = ref_bonus_base * (batch.gpd / ref_gpd)
        elif batch.ca < ref_ca:
            bonificacao_gpd_ca = ref_bonus_base * (ref_ca / batch.ca)

        bonificacao = bonificacao_gpd_ca + batch.bonus_tipificacao

    return bonificacao


# =========================================================
# ROUTES
# =========================================================
@app.route("/")
def index():
    return render_template("auth/login.html", title="AP360 | Login")


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        email = request.form["email"].strip().lower()
        password = request.form["password"].strip()
        user = User.query.filter_by(email=email).first()

        if user and user.check_password(password):
            if user.status == "ativo":
                login_user(user)
                flash(f"Bem-vindo, {user.nome}!", "success")
                return redirect(url_for("dashboard"))
            else:
                flash("Sua conta está bloqueada ou pendente de ativação. Entre em contato com o administrador.", "warning")
        else:
            flash("Email ou senha inválidos.", "danger")
    return render_template("auth/login.html", title="AP360 | Login")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("Você foi desconectado.", "info")
    return redirect(url_for("index"))


@app.route("/signup_request", methods=["GET", "POST"])
def signup_request():
    if request.method == "POST":
        nome = request.form["nome"].strip()
        email = request.form["email"].strip().lower()
        cpf = request.form.get("cpf", "").strip()
        telefone = request.form.get("telefone", "").strip()
        segmento = request.form["segmento"].strip()
        cooperativa = request.form.get("cooperativa", "").strip()

        if User.query.filter_by(email=email).first() or AccessRequest.query.filter_by(email=email, status="pendente").first():
            flash("Já existe uma solicitação ou usuário com este email.", "warning")
        else:
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
            flash("Sua solicitação de acesso foi enviada e será revisada pelo administrador.", "success")
            return redirect(url_for("login"))
    return render_template("auth/signup_request.html", title="AP360 | Solicitar Acesso")


@app.route("/invite/<token>", methods=["GET", "POST"])
def invite_signup(token):
    invite = AccessInvite.query.filter_by(token=token, status="convidado").first()
    if not invite:
        flash("Convite inválido ou já utilizado.", "danger")
        return redirect(url_for("index"))

    if request.method == "POST":
        nome = request.form["nome"].strip()
        password = request.form["password"].strip()
        if len(password) < 6:
            flash("A senha deve ter no mínimo 6 caracteres.", "danger")
            return render_template("auth/invite_signup.html", title="AP360 | Ativar Conta", token=token, invite=invite)

        user = User.query.filter_by(email=invite.email).first()
        if user:
            flash("Este email já está registrado. Por favor, faça login.", "warning")
            return redirect(url_for("login"))

        new_user = User(
            nome=nome,
            email=invite.email,
            perfil="produtor",
            status="ativo",
            segmento=invite.access_request.segmento if invite.access_request else None,
            cooperativa=invite.access_request.cooperativa if invite.access_request else None
        )
        new_user.set_password(password)
        db.session.add(new_user)

        invite.status = "ativado"
        invite.ativado_em = datetime.utcnow()
        db.session.commit()

        login_user(new_user)
        flash(f"Bem-vindo, {new_user.nome}! Sua conta foi ativada com sucesso.", "success")
        return redirect(url_for("dashboard"))

    return render_template("auth/invite_signup.html", title="AP360 | Ativar Conta", token=token, invite=invite)


@app.route("/dashboard")
@login_required
def dashboard():
    quotes = AgricultureQuote.query.filter_by(user_id=current_user.id).order_by(AgricultureQuote.criado_em.desc()).limit(5).all()
    return render_template("dashboard.html", title="AP360 | Dashboard", quotes=quotes)


@app.route("/add_quote", methods=["POST"])
@login_required
def add_quote():
    try:
        quantidade_ton = float(request.form["quantidade_ton"])
        cbot_usd_bushel = float(request.form["cbot_usd_bushel"])
        usd_brl = float(request.form["usd_brl"])
        export_rs_ton = float(request.form["export_rs_ton"])
        frete_rs_ton = float(request.form["frete_rs_ton"])

        # 1 bushel de soja = 27.2155 kg
        # 1 ton = 1000 kg
        # 1 ton = 1000 / 27.2155 = 36.7437 bushels
        bushels_per_ton = 36.7437

        cbot_rs_ton = (cbot_usd_bushel * bushels_per_ton) * usd_brl
        liquido_rs_ton = cbot_rs_ton - export_rs_ton - frete_rs_ton
        total_rs = liquido_rs_ton * quantidade_ton

        new_quote = AgricultureQuote(
            user_id=current_user.id,
            produto=request.form["produto"].strip(),
            quantidade_ton=quantidade_ton,
            origem=request.form["origem"].strip(),
            porto=request.form["porto"].strip(),
            cbot_usd_bushel=cbot_usd_bushel,
            usd_brl=usd_brl,
            export_rs_ton=export_rs_ton,
            frete_rs_ton=frete_rs_ton,
            liquido_rs_ton=liquido_rs_ton,
            total_rs=total_rs
        )
        db.session.add(new_quote)
        db.session.commit()
        flash("Cotação calculada e salva com sucesso!", "success")
    except ValueError:
        flash("Erro: Verifique se todos os campos numéricos foram preenchidos corretamente.", "danger")
    except Exception as e:
        flash(f"Ocorreu um erro inesperado: {e}", "danger")
    return redirect(url_for("dashboard"))


@app.route("/delete_quote/<int:quote_id>", methods=["POST"])
@login_required
def delete_quote(quote_id):
    quote = AgricultureQuote.query.filter_by(id=quote_id, user_id=current_user.id).first_or_404()
    db.session.delete(quote)
    db.session.commit()
    flash("Cotação excluída com sucesso!", "success")
    return redirect(url_for("dashboard"))


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
        flash("Seus benchmarks pessoais foram atualizados com sucesso!", "success")
    except ValueError:
        flash("Erro: Verifique se os valores numéricos estão corretos.", "danger")
    except Exception as e:
        flash(f"Ocorreu um erro inesperado: {e}", "danger")
    return redirect(url_for("dashboard"))


@app.route("/admin_panel")
@admin_required
def admin_panel():
    pending_requests = AccessRequest.query.filter_by(status="pendente").all()
    invites = AccessInvite.query.all()
    users = User.query.all()
    benchmarks = CoopBenchmark.query.all()
    return render_template("admin/admin_panel.html", title="AP360 | Admin",
                           pending_requests=pending_requests, invites=invites,
                           users=users, benchmarks=benchmarks)


@app.route("/admin_approve_request/<int:request_id>", methods=["POST"])
@admin_required
def admin_approve_request(request_id):
    req = AccessRequest.query.get_or_404(request_id)
    if req.status == "pendente":
        # Criar um token de convite
        token = secrets.token_urlsafe(32)
        new_invite = AccessInvite(email=req.email, token=token, request_id=req.id)
        db.session.add(new_invite)

        req.status = "liberado"
        db.session.commit()
        flash(f"Solicitação de {req.email} aprovada. Convite enviado.", "success")
        # Em um sistema real, você enviaria um email com o link do convite aqui
        # Ex: mail.send_message("Assunto", recipients=[req.email], body=f"Use este link para ativar sua conta: {url_for('invite_signup', token=token, _external=True)}")
    else:
        flash("Esta solicitação já foi processada.", "warning")
    return redirect(url_for("admin_panel"))


@app.route("/admin_deny_request/<int:request_id>", methods=["POST"])
@admin_required
def admin_deny_request(request_id):
    req = AccessRequest.query.get_or_404(request_id)
    if req.status == "pendente":
        req.status = "negado"
        db.session.commit()
        flash(f"Solicitação de {req.email} negada.", "info")
    else:
        flash("Esta solicitação já foi processada.", "warning")
    return redirect(url_for("admin_panel"))


@app.route("/admin_revoke_invite/<int:invite_id>", methods=["POST"])
@admin_required
def admin_revoke_invite(invite_id):
    invite = AccessInvite.query.get_or_404(invite_id)
    if invite.status == "convidado":
        invite.status = "revogado"
        db.session.commit()
        flash(f"Convite para {invite.email} revogado.", "info")
    else:
        flash("Este convite já foi ativado ou revogado.", "warning")
    return redirect(url_for("admin_panel"))


@app.route("/admin_block_user/<int:user_id>", methods=["POST"])
@admin_required
def admin_block_user(user_id):
    user = User.query.get_or_404(user_id)
    if user.perfil != "admin": # Não permitir bloquear o próprio admin
        user.status = "bloqueado"
        db.session.commit()
        flash(f"Usuário {user.email} bloqueado.", "info")
    else:
        flash("Não é possível bloquear um administrador.", "danger")
    return redirect(url_for("admin_panel"))


@app.route("/admin_activate_user/<int:user_id>", methods=["POST"])
@admin_required
def admin_activate_user(user_id):
    user = User.query.get_or_404(user_id)
    user.status = "ativo"
    db.session.commit()
    flash(f"Usuário {user.email} ativado.", "success")
    return redirect(url_for("admin_panel"))


@app.route("/admin_update_benchmark", methods=["POST"])
@admin_required
def admin_update_benchmark():
    try:
        cadeia = request.form["cadeia"].strip()
        cooperativa = request.form["cooperativa"].strip()
        media_gpd = float(request.form["media_gpd"])
        media_ca = float(request.form["media_ca"])
        bonus_base = float(request.form["bonus_base"])

        benchmark = CoopBenchmark.query.filter_by(cadeia=cadeia, cooperativa=cooperativa).first()
        if benchmark:
            benchmark.media_gpd = media_gpd
            benchmark.media_ca = media_ca
            benchmark.bonus_base = bonus_base
            benchmark.atualizado_em = datetime.utcnow()
            flash(f"Benchmark para {cooperativa} ({cadeia}) atualizado.", "success")
        else:
            new_benchmark = CoopBenchmark(
                cadeia=cadeia,
                cooperativa=cooperativa,
                media_gpd=media_gpd,
                media_ca=media_ca,
                bonus_base=bonus_base
            )
            db.session.add(new_benchmark)
            flash(f"Novo benchmark para {cooperativa} ({cadeia}) adicionado.", "success")
        db.session.commit()
    except ValueError:
        flash("Erro: Verifique se os valores numéricos estão corretos.", "danger")
    except Exception as e:
        flash(f"Ocorreu um erro inesperado: {e}", "danger")
    return redirect(url_for("admin_panel"))


@app.route("/admin_delete_benchmark/<int:benchmark_id>", methods=["POST"])
@admin_required
def admin_delete_benchmark(benchmark_id):
    benchmark = CoopBenchmark.query.get_or_404(benchmark_id)
    db.session.delete(benchmark)
    db.session.commit()
    flash(f"Benchmark para {benchmark.cooperativa} ({benchmark.cadeia}) excluído.", "success")
    return redirect(url_for("admin_panel"))


@app.route("/<string:cadeia>")
@login_required
def modulo_lotes(cadeia):
    if cadeia not in ["avicultura", "suinocultura"]:
        abort(404) # Retorna 404 se a cadeia não for reconhecida

    batches = Batch.query.filter_by(user_id=current_user.id, cadeia=cadeia).order_by(Batch.criado_em.desc()).all()
    return render_template("lotes/lotes_list.html", title=f"AP360 | {cadeia.capitalize()}",
                           batches=batches, cadeia=cadeia)


@app.route("/<string:cadeia>/add_lote", methods=["POST"])
@login_required
def add_lote(cadeia):
    if cadeia not in ["avicultura", "suinocultura"]:
        abort(404)

    try:
        data_alojamento_str = request.form["data_alojamento"]
        hora_alojamento_str = request.form["hora_alojamento"]
        data_carregamento_str = request.form["data_carregamento"]
        hora_carregamento_str = request.form["hora_carregamento"]

        dt_alojamento_naive = datetime.strptime(f"{data_alojamento_str} {hora_alojamento_str}", "%Y-%m-%d %H:%M")
        dt_carregamento_naive = datetime.strptime(f"{data_carregamento_str} {hora_carregamento_str}", "%Y-%m-%d %H:%M")

        # Localizar para o fuso horário de Brasília
        dt_alojamento = BRASILIA_TZ.localize(dt_alojamento_naive)
        dt_carregamento = BRASILIA_TZ.localize(dt_carregamento_naive)

        dias_timedelta = dt_carregamento - dt_alojamento
        dias = dias_timedelta.total_seconds() / (24 * 3600) # Dias com casas decimais

        peso_inicial = float(request.form["peso_inicial"])
        peso_final = float(request.form["peso_final"])
        racao_total_kg = float(request.form["racao_total_kg"])
        animais_iniciais = int(request.form["animais_iniciais"])
        animais_final = int(request.form["animais_final"])

        gpd = calc_gpd(peso_inicial, peso_final, dias)
        ca = calc_ca(racao_total_kg, animais_final, peso_final, peso_inicial, animais_iniciais)
        viabilidade_pct = calc_viabilidade(animais_iniciais, animais_final)
        mortalidade_pct = calc_mortalidade(animais_iniciais, animais_final)

        ca_ajustada = 0.0
        iep = 0.0
        indice_lote = 0.0
        peso_vivo_medio = 0.0
        peso_carcaca_medio = 0.0
        rendimento_carcaca_pct = 0.0
        carne_magra_pct = 0.0
        bonus_tipificacao = 0.0

        if cadeia == 'avicultura':
            peso_meta_coop = float(request.form.get("peso_meta_coop", 0) or 0)
            idade_meta_coop = float(request.form.get("idade_meta_coop", 0) or 0)
            fator_peso_caa = float(request.form.get("fator_peso_caa", 0.30) or 0.30)
            fator_idade_caa = float(request.form.get("fator_idade_caa", 0.01) or 0.01)

            # Usar peso_final do lote como peso_real e dias como idade_real
            ca_ajustada = calc_ca_ajustada_avicultura(ca, peso_final, dias, peso_meta_coop, idade_meta_coop, fator_peso_caa, fator_idade_caa)
            iep = calc_iep(gpd, viabilidade_pct, ca_ajustada)
            indice_lote = calc_indice_lote(iep, peso_final)
        elif cadeia == 'suinocultura':
            peso_vivo_medio = float(request.form.get("peso_vivo_medio", 0) or 0)
            peso_carcaca_medio = float(request.form.get("peso_carcaca_medio", 0) or 0)
            carne_magra_pct = float(request.form.get("carne_magra_pct", 0) or 0)
            rendimento_carcaca_pct = calc_rendimento_carcaca(peso_vivo_medio, peso_carcaca_medio)
            bonus_tipificacao = calc_bonus_tipificacao_suinos(carne_magra_pct)

        # Buscar benchmarks da cooperativa do usuário
        coop_benchmarks = None
        if current_user.cooperativa:
            coop_benchmarks = CoopBenchmark.query.filter_by(
                cadeia=cadeia, cooperativa=current_user.cooperativa
            ).first()

        new_batch = Batch(
            user_id=current_user.id,
            cadeia=cadeia,
            estrutura=request.form["estrutura"].strip(),
            lote=request.form["lote"].strip(),
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
            peso_meta_coop=float(request.form.get("peso_meta_coop", 0) or 0),
            idade_meta_coop=float(request.form.get("idade_meta_coop", 0) or 0),
            fator_peso_caa=float(request.form.get("fator_peso_caa", 0.30) or 0.30),
            fator_idade_caa=float(request.form.get("fator_idade_caa", 0.01) or 0.01),
            peso_vivo_medio=peso_vivo_medio,
            peso_carcaca_medio=peso_carcaca_medio,
            rendimento_carcaca_pct=rendimento_carcaca_pct,
            carne_magra_pct=carne_magra_pct,
            bonus_tipificacao=bonus_tipificacao,
            gpd_coop_ref=float(request.form.get("gpd_coop_ref", 0) or 0),
            ca_coop_ref=float(request.form.get("ca_coop_ref", 0) or 0)
        )

        # Calcular bonificação
        new_batch.bonificacao = calc_bonificacao_total(cadeia, new_batch, current_user, coop_benchmarks)

        db.session.add(new_batch)
        db.session.commit()
        flash(f"Lote de {cadeia} adicionado com sucesso!", "success")
    except ValueError as e:
        flash(f"Erro nos dados de entrada: {e}. Verifique se todos os campos numéricos e de data/hora estão corretos.", "danger")
    except Exception as e:
        flash(f"Ocorreu um erro inesperado: {e}", "danger")
    return redirect(url_for("modulo_lotes", cadeia=cadeia))


@app.route("/<string:cadeia>/editar/<int:batch_id>", methods=["GET", "POST"])
@login_required
def editar_lote(cadeia, batch_id):
    if cadeia not in ["avicultura", "suinocultura"]:
        abort(404)

    batch = Batch.query.filter_by(id=batch_id, user_id=current_user.id, cadeia=cadeia).first_or_404()

    if request.method == "POST":
        try:
            data_alojamento_str = request.form["data_alojamento"]
            hora_alojamento_str = request.form["hora_alojamento"]
            data_carregamento_str = request.form["data_carregamento"]
            hora_carregamento_str = request.form["hora_carregamento"]

            dt_alojamento_naive = datetime.strptime(f"{data_alojamento_str} {hora_alojamento_str}", "%Y-%m-%d %H:%M")
            dt_carregamento_naive = datetime.strptime(f"{data_carregamento_str} {hora_carregamento_str}", "%Y-%m-%d %H:%M")

            dt_alojamento = BRASILIA_TZ.localize(dt_alojamento_naive)
            dt_carregamento = BRASILIA_TZ.localize(dt_carregamento_naive)

            dias_timedelta = dt_carregamento - dt_alojamento
            dias = dias_timedelta.total_seconds() / (24 * 3600)

            peso_inicial = float(request.form["peso_inicial"])
            peso_final = float(request.form["peso_final"])
            racao_total_kg = float(request.form["racao_total_kg"])
            animais_iniciais = int(request.form["animais_iniciais"])
            animais_final = int(request.form["animais_final"])

            batch.estrutura = request.form["estrutura"].strip()
            batch.lote = request.form["lote"].strip()
            batch.data_alojamento = dt_alojamento
            batch.data_carregamento = dt_carregamento
            batch.dias = dias
            batch.peso_inicial = peso_inicial
            batch.peso_final = peso_final
            batch.racao_total_kg = racao_total_kg
            batch.animais_iniciais = animais_iniciais
            batch.animais_final = animais_final

            batch.gpd = calc_gpd(peso_inicial, peso_final, dias)
            batch.ca = calc_ca(racao_total_kg, animais_final, peso_final, peso_inicial, animais_iniciais)
            batch.viabilidade_pct = calc_viabilidade(animais_iniciais, animais_final)
            batch.mortalidade_pct = calc_mortalidade(animais_iniciais, animais_final)

            batch.gpd_coop_ref = float(request.form.get("gpd_coop_ref", 0) or 0)
            batch.ca_coop_ref = float(request.form.get("ca_coop_ref", 0) or 0)

            if cadeia == 'avicultura':
                batch.peso_meta_coop = float(request.form.get("peso_meta_coop", 0) or 0)
                batch.idade_meta_coop = float(request.form.get("idade_meta_coop", 0) or 0)
                batch.fator_peso_caa = float(request.form.get("fator_peso_caa", 0.30) or 0.30)
                batch.fator_idade_caa = float(request.form.get("fator_idade_caa", 0.01) or 0.01)
                batch.ca_ajustada = calc_ca_ajustada_avicultura(batch.ca, batch.peso_final, batch.dias, batch.peso_meta_coop, batch.idade_meta_coop, batch.fator_peso_caa, batch.fator_idade_caa)
                batch.iep = calc_iep(batch.gpd, batch.viabilidade_pct, batch.ca_ajustada)
                batch.indice_lote = calc_indice_lote(batch.iep, batch.peso_final)
            elif cadeia == 'suinocultura':
                batch.peso_vivo_medio = float(request.form.get("peso_vivo_medio", 0) or 0)
                batch.peso_carcaca_medio = float(request.form.get("peso_carcaca_medio", 0) or 0)
                batch.carne_magra_pct = float(request.form.get("carne_magra_pct", 0) or 0)
                batch.rendimento_carcaca_pct = calc_rendimento_carcaca(batch.peso_vivo_medio, batch.peso_carcaca_medio)
                batch.bonus_tipificacao = calc_bonus_tipificacao_suinos(batch.carne_magra_pct)

            coop_benchmarks = None
            if current_user.cooperativa:
                coop_benchmarks = CoopBenchmark.query.filter_by(
                    cadeia=cadeia, cooperativa=current_user.cooperativa
                ).first()
            batch.bonificacao = calc_bonificacao_total(cadeia, batch, current_user, coop_benchmarks)

            db.session.commit()
            flash(f"Lote de {cadeia} atualizado com sucesso!", "success")
        except ValueError as e:
            flash(f"Erro nos dados de entrada: {e}. Verifique se todos os campos numéricos e de data/hora estão corretos.", "danger")
        except Exception as e:
            flash(f"Ocorreu um erro inesperado: {e}", "danger")
        return redirect(url_for("modulo_lotes", cadeia=cadeia))

    return render_template("lotes/editar_lote.html", title=f"AP360 | Editar Lote {cadeia.capitalize()}",
                           batch=batch, cadeia=cadeia, BRASILIA_TZ=BRASILIA_TZ)


@app.route("/<string:cadeia>/excluir/<int:batch_id>", methods=["POST"])
@login_required
def excluir_lote(cadeia, batch_id):
    batch = Batch.query.filter_by(id=batch_id, user_id=current_user.id, cadeia=cadeia).first_or_404()
    BatchDailyRecord.query.filter_by(batch_id=batch.id).delete() # Exclui registros diários do lote
    db.session.delete(batch)
    db.session.commit()
    flash(f"Lote de {cadeia} excluído com sucesso!", "success")
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
                    flash("Já existe um registro para esta data neste lote.", "warning")
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
                flash("Registro diário adicionado com sucesso!", "success")
            except ValueError:
                flash("Erro: Verifique se os valores numéricos e de data estão corretos.", "danger")
            except Exception as e:
                flash(f"Ocorreu um erro ao adicionar o registro: {e}", "danger")
            return redirect(url_for("detalhes_lote_ao_vivo", cadeia=cadeia, batch_id=batch.id))

    # Preparar dados para gráficos
    chart_labels = [r.data_registro.strftime('%d/%m') for r in daily_records]
    chart_peso_medio = [r.peso_medio for r in daily_records]
    chart_consumo_racao = [r.consumo_racao_dia for r in daily_records]
    chart_mortalidade = [r.mortalidade_dia for r in daily_records]

    return render_template("lotes/detalhes_lote_ao_vivo.html", title=f"AP360 | Detalhes do Lote {cadeia.capitalize()}",
                           batch=batch, cadeia=cadeia, daily_records=daily_records,
                           chart_labels=chart_labels, chart_peso_medio=chart_peso_medio,
                           chart_consumo_racao=chart_consumo_racao, chart_mortalidade=chart_mortalidade,
                           BRASILIA_TZ=BRASILIA_TZ)


@app.route("/bovinocultura")
@login_required
def bovinocultura():
    animais = Bovino.query.filter_by(user_id=current_user.id).order_by(Bovino.brinco).all()

    animal_id = request.args.get("animal", type=int)
    animal = None
    pesos = []
    eventos = []
    chart = None

    if animal_id:
        animal = Bovino.query.filter_by(id=animal_id, user_id=current_user.id).first_or_404()
        pesos = BovinoPeso.query.filter_by(bovino_id=animal.id).order_by(BovinoPeso.data.asc()).all()
        eventos = BovinoEvento.query.filter_by(bovino_id=animal.id).order_by(BovinoEvento.data.desc()).all()

        if pesos:
            chart_labels = [p.data for p in pesos]
            chart_vals = [p.peso for p in pesos]
            chart = {"labels": chart_labels, "vals": chart_vals}

    return render_template("bovinocultura/bovinocultura.html", title="AP360 | Bovinocultura",
                           animais=animais, animal=animal, pesos=pesos, eventos=eventos, chart=chart)


@app.route("/bovinocultura/add", methods=["POST"])
@login_required
def add_bovino():
    try:
        brinco = request.form["brinco"].strip()
        existing_bovino = Bovino.query.filter_by(user_id=current_user.id, brinco=brinco).first()
        if existing_bovino:
            flash("Já existe um bovino com este brinco cadastrado para você.", "danger")
            return redirect(url_for("bovinocultura"))

        new_bovino = Bovino(
            user_id=current_user.id,
            brinco=brinco,
            nome=request.form.get("nome", "").strip(),
            sexo=request.form.get("sexo", "").strip(),
            raca=request.form.get("raca", "").strip(),
            nascimento=request.form.get("nascimento", "").strip(),
            origem=request.form.get("origem", "").strip(),
            lote=request.form.get("lote", "").strip(),
            observacoes=request.form.get("observacoes", "").strip()
        )
        db.session.add(new_bovino)
        db.session.commit()
        flash("Bovino cadastrado com sucesso!", "success")
    except Exception as e:
        flash(f"Ocorreu um erro ao cadastrar o bovino: {e}", "danger")
    return redirect(url_for("bovinocultura"))


@app.route("/bovinocultura/add_pesagem_evento", methods=["POST"])
@login_required
def add_pesagem_evento():
    form_type = request.form.get("form_type")
    bovino_id = request.form.get("bovino_id", type=int)
    bovino = Bovino.query.filter_by(id=bovino_id, user_id=current_user.id).first_or_404()

    if form_type == "nova_pesagem":
        try:
            data = request.form["data"].strip()
            peso = float(request.form["peso"])
            new_peso = BovinoPeso(bovino_id=bovino.id, data=data, peso=peso)
            db.session.add(new_peso)

            # Atualiza o peso_atual e ultima_pesagem do bovino
            bovino.peso_atual = peso
            bovino.ultima_pesagem = data
            db.session.commit()
            flash("Pesagem registrada com sucesso!", "success")
        except ValueError:
            flash("Erro: Verifique se o peso é um número válido e a data está correta.", "danger")
        except Exception as e:
            flash(f"Ocorreu um erro ao registrar a pesagem: {e}", "danger")
    elif form_type == "novo_evento":
        try:
            tipo = request.form["tipo"].strip()
            data = request.form["data"].strip()
            descricao = request.form["descricao"].strip()
            new_evento = BovinoEvento(bovino_id=bovino.id, tipo=tipo, data=data, descricao=descricao)
            db.session.add(new_evento)
            db.session.commit()
            flash("Evento registrado com sucesso!", "success")
        except Exception as e:
            flash(f"Ocorreu um erro ao registrar o evento: {e}", "danger")

    return redirect(url_for("bovinocultura", animal=bovino.id))


@app.route("/bovinocultura/editar/<int:bovino_id>", methods=["GET", "POST"])
@login_required
def editar_bovino(bovino_id):
    bovino = Bovino.query.filter_by(id=bovino_id, user_id=current_user.id).first_or_404()

    if request.method == "POST":
        new_brinco = request.form.get("brinco", "").strip()
        existing_bovino_with_brinco = Bovino.query.filter(
            Bovino.user_id == current_user.id,
            Bovino.brinco == new_brinco,
            Bovino.id != bovino_id
        ).first()
        if existing_bovino_with_brinco:
            flash("Brinco já cadastrado para este usuário em outro animal.", "danger")
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
        flash("Dados do bovino atualizados com sucesso!", "success")
        return redirect(url_for("bovinocultura", animal=bovino.id))

    return render_template("bovinocultura/editar_bovino.html", title="AP360 | Editar Bovino", bovino=bovino)


@app.route("/bovinocultura/excluir/<int:bovino_id>", methods=["POST"])
@login_required
def excluir_bovino(bovino_id):
    bovino = Bovino.query.filter_by(id=bovino_id, user_id=current_user.id).first_or_404()
    BovinoPeso.query.filter_by(bovino_id=bovino.id).delete()
    BovinoEvento.query.filter_by(bovino_id=bovino.id).delete()
    db.session.delete(bovino)
    db.session.commit()
    flash("Bovino e todos os seus registros excluídos com sucesso!", "success")
    return redirect(url_for("bovinocultura"))


@app.route("/bovinocultura/pesagem/excluir/<int:peso_id>", methods=["POST"])
@login_required
def excluir_pesagem(peso_id):
    peso_registro = BovinoPeso.query.get_or_404(peso_id)
    bovino_id = peso_registro.bovino_id
    bovino = Bovino.query.filter_by(id=bovino_id, user_id=current_user.id).first_or_404()

    db.session.delete(peso_registro)
    db.session.commit()

    ultima_pesagem = BovinoPeso.query.filter_by(bovino_id=bovino.id).order_by(BovinoPeso.data.desc()).first()
    if ultima_pesagem:
        bovino.peso_atual = ultima_pesagem.peso
        bovino.ultima_pesagem = ultima_pesagem.data
    else:
        bovino.peso_atual = 0.0
        bovino.ultima_pesagem = None
    db.session.commit()

    flash("Registro de pesagem excluído com sucesso!", "success")
    return redirect(url_for("bovinocultura", animal=bovino_id))


@app.route("/bovinocultura/evento/excluir/<int:evento_id>", methods=["POST"])
@login_required
def excluir_evento(evento_id):
    evento_registro = BovinoEvento.query.get_or_404(evento_id)
    bovino_id = evento_registro.bovino_id
    bovino = Bovino.query.filter_by(id=bovino_id, user_id=current_user.id).first_or_404()

    db.session.delete(evento_registro)
    db.session.commit()
    flash("Registro de evento excluído com sucesso!", "success")
    return redirect(url_for("bovinocultura", animal=bovino_id))


@app.route("/agricultura")
@login_required
def agricultura_modulo():
    if current_user.segmento != "agricultura":
        flash("Seu perfil não tem acesso ao módulo de agricultura.", "warning")
        return redirect(url_for("dashboard"))
    fields = AgricultureField.query.filter_by(user_id=current_user.id).order_by(AgricultureField.data_plantio.desc()).all()
    return render_template("agricultura/agricultura_modulo.html", title="AP360 | Agricultura", fields=fields)


@app.route("/agricultura/add_field", methods=["POST"])
@login_required
def add_agriculture_field():
    if current_user.segmento != "agricultura":
        flash("Seu perfil não tem acesso ao módulo de agricultura.", "warning")
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
        flash("Campo de produção adicionado com sucesso!", "success")
    except ValueError as e:
        flash(f"Erro nos dados de entrada: {e}. Verifique se todos os campos numéricos e de data estão corretos.", "danger")
    except Exception as e:
        flash(f"Ocorreu um erro inesperado: {e}", "danger")
    return redirect(url_for("agricultura_modulo"))


@app.route("/agricultura/editar_field/<int:field_id>", methods=["GET", "POST"])
@login_required
def editar_agriculture_field(field_id):
    if current_user.segmento != "agricultura":
        flash("Seu perfil não tem acesso ao módulo de agricultura.", "warning")
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
            flash("Campo de produção atualizado com sucesso!", "success")
        except ValueError as e:
            flash(f"Erro nos dados de entrada: {e}. Verifique se todos os campos numéricos e de data estão corretos.", "danger")
        except Exception as e:
            flash(f"Ocorreu um erro inesperado: {e}", "danger")
        return redirect(url_for("agricultura_modulo"))

    return render_template("agricultura/editar_agriculture_field.html", title="AP360 | Editar Campo", field=field)


@app.route("/agricultura/excluir_field/<int:field_id>", methods=["POST"])
@login_required
def excluir_agriculture_field(field_id):
    if current_user.segmento != "agricultura":
        flash("Seu perfil não tem acesso ao módulo de agricultura.", "warning")
        return redirect(url_for("dashboard"))
    field = AgricultureField.query.filter_by(id=field_id, user_id=current_user.id).first_or_404()
    AgricultureDailyRecord.query.filter_by(field_id=field.id).delete() # Exclui registros diários do campo
    db.session.delete(field)
    db.session.commit()
    flash("Campo de produção e seus registros excluídos com sucesso!", "success")
    return redirect(url_for("agricultura_modulo"))


@app.route("/agricultura/field/<int:field_id>", methods=["GET", "POST"])
@login_required
def detalhes_agriculture_field(field_id):
    if current_user.segmento != "agricultura":
        flash("Seu perfil não tem acesso ao módulo de agricultura.", "warning")
        return redirect(url_for("dashboard"))

    field = AgricultureField.query.filter_by(id=field_id, user_id=current_user.id).first_or_404()
    daily_records = AgricultureDailyRecord.query.filter_by(field_id=field.id).order_by(AgricultureDailyRecord.data_registro.asc()).all()

    if request.method == "POST":
        form_type = request.form.get("form_type")
        if form_type == "novo_registro_diario_agricultura":
            try:
                data_registro = datetime.strptime(request.form.get("data_registro"), "%Y-%m-%d").date()
                if AgricultureDailyRecord.query.filter_by(field_id=field.id, data_registro=data_registro).first():
                    flash("Já existe um registro para esta data neste campo.", "warning")
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
                flash("Registro diário adicionado com sucesso!", "success")
            except ValueError:
                flash("Erro: Verifique se os valores numéricos e de data estão corretos.", "danger")
            except Exception as e:
                flash(f"Ocorreu um erro ao adicionar o registro: {e}", "danger")
            return redirect(url_for("detalhes_agriculture_field", field_id=field.id))

        elif form_type == "agrosim_predict":
            try:
                dias_crescimento = (datetime.now().date() - field.data_plantio).days
                if dias_crescimento <= 0:
                    flash("O campo ainda não começou a crescer.", "info")
                    return redirect(url_for("detalhes_agriculture_field", field_id=field.id))

                produtividade_simulada = (field.produtividade_esperada_ton_ha / field.data_colheita_prevista.day) * dias_crescimento if field.data_colheita_prevista else (field.produtividade_esperada_ton_ha / 100) * dias_crescimento
                produtividade_simulada = min(produtividade_simulada, field.produtividade_esperada_ton_ha * 1.2)

                flash(f"Simulação Agrosim: Produtividade atual estimada em {produtividade_simulada:.2f} ton/ha.", "info")
            except Exception as e:
                flash(f"Erro na simulação Agrosim: {e}", "danger")
            return redirect(url_for("detalhes_agriculture_field", field_id=field.id))


    # Preparar dados para gráficos
    chart_labels = [r.data_registro.strftime('%d/%m') for r in daily_records]
    chart_chuva = [r.chuva_mm for r in daily_records]
    chart_temperatura = [r.temperatura_c for r in daily_records]
    chart_produtividade_parcial = [r.produtividade_parcial_ton_ha for r in daily_records]

    return render_template("agricultura/detalhes_agriculture_field.html", title="AP360 | Detalhes do Campo",
                           field=field, daily_records=daily_records,
                           chart_labels=chart_labels, chart_chuva=chart_chuva,
                           chart_temperatura=chart_temperatura, chart_produtividade_parcial=chart_produtividade_parcial)


@app.route("/agricultura/registro_diario/excluir/<int:record_id>", methods=["POST"])
@login_required
def excluir_registro_diario_agricultura(record_id):
    if current_user.segmento != "agricultura":
        flash("Seu perfil não tem acesso ao módulo de agricultura.", "warning")
        return redirect(url_for("dashboard"))
    record = AgricultureDailyRecord.query.get_or_404(record_id)
    field_id = record.field_id
    field = AgricultureField.query.filter_by(id=field_id, user_id=current_user.id).first_or_404()
    db.session.delete(record)
    db.session.commit()
    flash("Registro diário excluído com sucesso!", "success")
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
    return render_template("ia.html", title="AP360 | IA")


@app.route("/ia/chat", methods=["POST"])
@login_required
def ia_chat():
    return jsonify({"resposta": ia_local(request.form.get("mensagem", ""))})


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