import os
import io
import csv
import secrets
from datetime import datetime, timedelta
from functools import wraps
import pytz # Para manipulação de fusos horários

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

# Configuração do fuso horário de Brasília
BRASILIA_TZ = pytz.timezone("America/Sao_Paulo")

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
    status = db.Column(db.String(20), default="pendente")  # pendente/liberado/negado/oculto
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


# --- Novos modelos para Agrosim (Agricultura) ---
class AgriculturePlot(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    nome_talhao = db.Column(db.String(120), nullable=False)
    cultura = db.Column(db.String(80), nullable=False)
    area_ha = db.Column(db.Float, nullable=False)
    data_plantio = db.Column(db.Date, nullable=False)
    data_colheita_estimada = db.Column(db.Date, nullable=True)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint('user_id', 'nome_talhao', name='_user_talhao_uc'),)

class PlotYield(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    plot_id = db.Column(db.Integer, db.ForeignKey("agriculture_plot.id"), nullable=False)
    data_registro = db.Column(db.Date, nullable=False)
    produtividade_kg_ha = db.Column(db.Float, nullable=False)
    observacoes = db.Column(db.Text, nullable=True)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)

class PlotInput(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    plot_id = db.Column(db.Integer, db.ForeignKey("agriculture_plot.id"), nullable=False)
    tipo_insumo = db.Column(db.String(80), nullable=False) # Ex: Semente, Fertilizante, Defensivo
    nome_insumo = db.Column(db.String(120), nullable=False)
    quantidade = db.Column(db.Float, nullable=False)
    unidade = db.Column(db.String(20), nullable=False) # Ex: kg, L, saco
    custo_total_rs = db.Column(db.Float, nullable=False)
    data_aplicacao = db.Column(db.Date, nullable=False)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)
# --- Fim dos novos modelos para Agrosim (Agricultura) ---


class Batch(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

    cadeia = db.Column(db.String(20), nullable=False)  # avicultura/suinocultura
    estrutura = db.Column(db.String(40), nullable=False)
    lote = db.Column(db.String(40), nullable=False)

    # Base produtiva (agora peso_inicial e peso_final são MÉDIOS por animal)
    peso_inicial = db.Column(db.Float, nullable=False) # Peso médio por animal no início
    peso_final = db.Column(db.Float, nullable=False)   # Peso médio por animal no final
    # dias = db.Column(db.Integer, nullable=False) # Removido, será calculado
    alojamento_datetime = db.Column(db.DateTime, nullable=False) # Data e hora do alojamento
    carregamento_datetime = db.Column(db.DateTime, nullable=False) # Data e hora do carregamento
    dias_alojamento = db.Column(db.Float, nullable=False) # Dias de alojamento com vírgula

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


# --- Novos modelos para Live Lot Tracking ---
class LiveBatch(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    cadeia = db.Column(db.String(20), nullable=False) # avicultura/suinocultura
    estrutura = db.Column(db.String(40), nullable=False)
    lote = db.Column(db.String(40), nullable=False)
    alojamento_datetime = db.Column(db.DateTime, nullable=False)
    animais_iniciais = db.Column(db.Integer, nullable=False)
    peso_inicial_medio = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(20), default="ativo") # ativo/finalizado/cancelado
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint('user_id', 'cadeia', 'estrutura', 'lote', name='_user_live_batch_uc'),)

class LiveWeighing(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    live_batch_id = db.Column(db.Integer, db.ForeignKey("live_batch.id"), nullable=False)
    data_pesagem = db.Column(db.DateTime, nullable=False)
    peso_medio_animal = db.Column(db.Float, nullable=False)
    animais_pesados = db.Column(db.Integer, nullable=False)
    observacoes = db.Column(db.Text, nullable=True)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)

class LiveFeed(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    live_batch_id = db.Column(db.Integer, db.ForeignKey("live_batch.id"), nullable=False)
    data_registro = db.Column(db.DateTime, nullable=False)
    tipo_racao = db.Column(db.String(80), nullable=False)
    quantidade_kg = db.Column(db.Float, nullable=False)
    custo_total_rs = db.Column(db.Float, nullable=True)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)
# --- Fim dos novos modelos para Live Lot Tracking ---


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
    """Calcula o Ganho de Peso Diário (GPD) por animal, com dias em float."""
    if dias <= 0:
        return 0.0
    return round((peso_final_medio - peso_inicial_medio) / dias, 4)


def calc_ca(racao_total_lote: float, peso_inicial_medio: float, peso_final_medio: float, animais_final: int) -> float:
    """Calcula a Conversão Alimentar (CA) para o lote."""
    ganho_peso_total_lote = (peso_final_medio - peso_inicial_medio) * animais_final
    if ganho_peso_total_lote <= 0: # Evita divisão por zero ou CA negativa/infinita
        return 0.0
    return round(racao_total_lote / ganho_peso_total_lote, 4)


def calc_dias_alojamento(alojamento_dt: datetime, carregamento_dt: datetime) -> float:
    """Calcula a diferença em dias (com vírgula) entre duas datetimes."""
    if not alojamento_dt or not carregamento_dt:
        return 0.0
    # Garante que as datetimes estão no fuso horário de Brasília para cálculo consistente
    alojamento_dt_br = BRASILIA_TZ.localize(alojamento_dt) if alojamento_dt.tzinfo is None else alojamento_dt.astimezone(BRASILIA_TZ)
    carregamento_dt_br = BRASILIA_TZ.localize(carregamento_dt) if carregamento_dt.tzinfo is None else carregamento_dt.astimezone(BRASILIA_TZ)

    delta = carregamento_dt_br - alojamento_dt_br
    return round(delta.total_seconds() / (24 * 3600), 2) # Total de segundos dividido por segundos em um dia


def calc_viabilidade(animais_iniciais: int, animais_final: int) -> float:
    if animais_iniciais <= 0:
        return 0.0
    return round((animais_final / animais_iniciais) * 100.0, 2)


def calc_mortalidade(animais_iniciais: int, animais_final: int) -> float:
    if animais_iniciais <= 0:
        return 0.0
    mortos = max(0, animais_iniciais - animais_final)
    return round((mortos / animais_iniciais) * 100.0, 2)


def calc_ca_ajustada_avicultura(ca_observada: float, peso_real: float, idade_real_dias: float,
                                peso_meta: float, idade_meta_dias: int,
                                fator_peso: float = 0.30, fator_idade: float = 0.01) -> float:
    """
    Calcula a Conversão Alimentar Ajustada (CAA) para avicultura.
    Ajustado para idade_real_dias ser float.
    """
    # A fórmula original já é uma boa base. Vamos garantir que os parâmetros de entrada
    # sejam tratados corretamente e que o resultado seja razoável.
    # O peso_real e idade_real_dias são os valores observados do lote.
    # peso_meta e idade_meta_dias são os valores de referência da cooperativa.

    # Garante que os fatores são aplicados corretamente para ajustar a CA
    # Se o peso real for menor que o meta, a CA observada é "pior" do que seria no peso meta,
    # então o ajuste de peso deve REDUZIR a CA ajustada (melhorar).
    # Se a idade real for maior que a meta, a CA observada é "pior" do que seria na idade meta,
    # então o ajuste de idade deve REDUZIR a CA ajustada (melhorar).
    # A fórmula original: ca_observada + (fator_peso * (peso_meta - peso_real)) + (fator_idade * (idade_real - idade_meta))
    # parece estar no caminho certo para "melhorar" a CA se o lote não atingiu o peso meta ou passou da idade meta.

    caa = ca_observada + \
          (fator_peso * (peso_meta - peso_real)) + \
          (fator_idade * (idade_real_dias - idade_meta_dias))

    return round(max(caa, 0.01), 4) # Garante que a CAA não seja zero ou negativa


def calc_iep_avicultura(viabilidade_pct: float, peso_medio: float, idade_dias: float, ca_ajustada: float) -> float:
    """Calcula o Índice de Eficiência Produtiva (IEP) para avicultura, com idade_dias em float."""
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
    if ca <= 0 or meta_gpd <= 0 or meta_ca <= 0: # Evita divisão por zero
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
# --- FIM DAS FUNÇÕES DE CÁLCULO ATUALIZADAS ---


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
      --danger:#ff4d4d; /* Adicionado para botões de exclusão/negação */
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
    .btn-danger{background:linear-gradient(135deg,var(--danger),#cc3d3d);color:#fff} /* Estilo para botão de perigo */
    input,select,textarea{
      width:100%;padding:10px;border:1px solid var(--line);border-radius:10px;
      background:rgba(255,255,255,.08);color:#fff;margin:5px 0
    }
    input::placeholder,textarea::placeholder{color:#dbe7ff90}
    /* Adicionado para corrigir a cor do texto nos selects */
    select {
      color: #000; /* Cor do texto preto */
      background-color: #fff; /* Fundo branco para melhor contraste */
    }
    select option {
      color: #000; /* Garante que as opções também sejam pretas */
      background-color: #fff; /* Garante que as opções também tenham fundo branco */
    }
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
    .whatsapp-cta {
        background: linear-gradient(135deg, #25D366, #128C7E);
        color: white;
        padding: 12px 20px;
        border-radius: 10px;
        text-align: center;
        font-weight: bold;
        text-decoration: none;
        display: block;
        margin-top: 20px;
        box-shadow: 0 4px 8px rgba(0,0,0,0.2);
    }
    .whatsapp-cta:hover {
        opacity: 0.9;
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
          <a href="{{ url_for('live_lot_tracking') }}">Lotes Ao Vivo</a> {# Novo link #}
          <a href="{{ url_for('agrosim') }}">Agrosim</a> {# Novo link #}
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
      <a href="https://wa.me/5545999037929?text=Ol%C3%A1%2C%20gostaria%20de%20saber%20mais%20sobre%20o%20AP360!" target="_blank" class="whatsapp-cta">
        Fale Conosco no WhatsApp!
      </a>
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
        if not user.is_active(): # Usa o novo método is_active
            flash("Sua conta está bloqueada ou pendente. Entre em contato com o administrador.")
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
      <a href="https://wa.me/5545999037929?text=Ol%C3%A1%2C%20tenho%20d%C3%BAvidas%20sobre%20o%20login%20no%20AP360!" target="_blank" class="whatsapp-cta">
        Precisa de ajuda? Fale Conosco!
      </a>
    </div>
    """
    return page(html_content, title="AP360 | Login")


@app.route("/inscreva_se", methods=["GET", "POST"])
@app.route("/inscreva-se", methods=["GET", "POST"])
def signup_request():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()

        # Verifica se o e-mail já está em uso por um usuário ativo
        if User.query.filter_by(email=email).first():
            flash("Este e-mail já está cadastrado. Por favor, faça login.")
            return redirect(url_for("login"))

        # Verifica se já existe uma solicitação pendente ou negada para este e-mail
        existing_request = AccessRequest.query.filter_by(email=email).first()
        if existing_request and existing_request.status in ["pendente", "negado"]:
            flash("Já existe uma solicitação de acesso para este e-mail. Aguarde a análise ou entre em contato.")
            return redirect(url_for("signup_request"))

        # Verifica se já existe um convite pendente
        inv = AccessInvite.query.filter_by(email=email, status="convidado").first()
        if inv:
            flash("Este e-mail já possui um convite pendente. Por favor, ative sua conta.")
            return redirect(url_for("activate_account", token=inv.token))


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
      </p>
      <a href="https://wa.me/5545999037929?text=Ol%C3%A1%2C%20enviei%20minha%20solicita%C3%A7%C3%A3o%20de%20acesso%20ao%20AP360!" target="_blank" class="whatsapp-cta">
        Fale Conosco no WhatsApp!
      </a>
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
    live_batches = LiveBatch.query.filter_by(user_id=current_user.id, status="ativo").count()
    agrosim_plots = AgriculturePlot.query.filter_by(user_id=current_user.id).count()


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
      <div class="card">
        <div class="muted">Bovinos cadastrados</div>
        <div class="kpi">{{ bo }}</div>
        <a class="btn btn-ghost" href="{{ url_for('bovinocultura') }}">Acessar</a>
      </div>
      <div class="card">
        <div class="muted">Lotes Ao Vivo</div>
        <div class="kpi">{{ live_batches }}</div>
        <a class="btn btn-ghost" href="{{ url_for('live_lot_tracking') }}">Acompanhar</a>
      </div>
      <div class="card">
        <div class="muted">Talhões Agrosim</div>
        <div class="kpi">{{ agrosim_plots }}</div>
        <a class="btn btn-ghost" href="{{ url_for('agrosim') }}">Gerenciar</a>
      </div>
    </div>

    <div class="card">
      <h3>Meus Benchmarks Pessoais</h3>
      <p class="muted">Defina seus próprios benchmarks para avicultura e suinocultura. Se preenchidos, eles terão prioridade sobre os benchmarks da cooperativa.</p>
      <form method="post" action="{{ url_for('update_user_benchmarks') }}">
        <input type="hidden" name="form_type" value="user_benchmarks">
        <h4>Avicultura</h4>
        <label>GPD Médio (Avicultura)</label>
        <input type="number" step="0.0001" name="user_avicultura_gpd" value="{{ current_user.user_avicultura_gpd }}" placeholder="Ex: 0.065">
        <label>CA Médio (Avicultura)</label>
        <input type="number" step="0.0001" name="user_avicultura_ca" value="{{ current_user.user_avicultura_ca }}" placeholder="Ex: 1.70">
        <label>Base Bônus R$ (Avicultura)</label>
        <input type="number" step="0.01" name="user_avicultura_bonus_base" value="{{ current_user.user_avicultura_bonus_base }}" placeholder="Ex: 1000.00">

        <h4>Suinocultura</h4>
        <label>GPD Médio (Suinocultura)</label>
        <input type="number" step="0.0001" name="user_suinocultura_gpd" value="{{ current_user.user_suinocultura_gpd }}" placeholder="Ex: 0.72">
        <label>CA Médio (Suinocultura)</label>
        <input type="number" step="0.0001" name="user_suinocultura_ca" value="{{ current_user.user_suinocultura_ca }}" placeholder="Ex: 2.45">
        <label>Base Bônus R$ (Suinocultura)</label>
        <input type="number" step="0.01" name="user_suinocultura_bonus_base" value="{{ current_user.user_suinocultura_bonus_base }}" placeholder="Ex: 1200.00">

        <button class="btn btn-pri" type="submit">Salvar Meus Benchmarks</button>
      </form>
    </div>
    """
    return page(html_content, title="AP360 | Dashboard", ag=ag, av=av, su=su, bo=bo, live_batches=live_batches, agrosim_plots=agrosim_plots)


@app.route("/update_user_benchmarks", methods=["POST"])
@login_required
def update_user_benchmarks():
    if request.form.get("form_type") == "user_benchmarks":
        current_user.user_avicultura_gpd = float(request.form.get("user_avicultura_gpd", 0) or 0)
        current_user.user_avicultura_ca = float(request.form.get("user_avicultura_ca", 0) or 0)
        current_user.user_avicultura_bonus_base = float(request.form.get("user_avicultura_bonus_base", 0) or 0)

        current_user.user_suinocultura_gpd = float(request.form.get("user_suinocultura_gpd", 0) or 0)
        current_user.user_suinocultura_ca = float(request.form.get("user_suinocultura_ca", 0) or 0)
        current_user.user_suinocultura_bonus_base = float(request.form.get("user_suinocultura_bonus_base", 0) or 0)

        db.session.commit()
        flash("Seus benchmarks pessoais foram atualizados com sucesso!")
    return redirect(url_for("dashboard"))


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

            if AccessInvite.query.filter_by(email=email, status="convidado").first():
                flash("Já existe um convite pendente para este e-mail.")
                return redirect(url_for("admin_panel"))
            if User.query.filter_by(email=email).first():
                flash("Já existe um usuário ativo com este e-mail.")
                return redirect(url_for("admin_panel"))

            token = secrets.token_urlsafe(32)
            inv = AccessInvite(email=email, token=token, status="convidado")
            db.session.add(inv)
            db.session.commit()
            flash(f"Convite gerado para {email}. Link: {url_for('activate_account', token=token, _external=True)}")
            return redirect(url_for("admin_panel"))

        elif form_type == "approve_request":
            req_id = request.form.get("request_id", type=int)
            req = AccessRequest.query.get(req_id)
            if req and req.status == "pendente":
                # Verifica se já existe um convite ou usuário para este email
                if AccessInvite.query.filter_by(email=req.email, status="convidado").first():
                    flash(f"Já existe um convite pendente para {req.email}.")
                    return redirect(url_for("admin_panel"))
                if User.query.filter_by(email=req.email).first():
                    flash(f"Já existe um usuário ativo com {req.email}.")
                    return redirect(url_for("admin_panel"))

                token = secrets.token_urlsafe(32)
                inv = AccessInvite(email=req.email, token=token, status="convidado", request_id=req.id)
                req.status = "liberado" # Marca a solicitação como liberada
                db.session.add(inv)
                db.session.commit()
                flash(f"Solicitação de {req.nome} aprovada. Convite gerado para {req.email}. Link: {url_for('activate_account', token=token, _external=True)}")
            else:
                flash("Solicitação não encontrada ou já processada.")
            return redirect(url_for("admin_panel"))

        elif form_type == "deny_request":
            req_id = request.form.get("request_id", type=int)
            req = AccessRequest.query.get(req_id)
            if req:
                # Se a solicitação já gerou um convite, precisamos revogar o convite também
                inv = AccessInvite.query.filter_by(request_id=req.id, status="convidado").first()
                if inv:
                    inv.status = "revogado"
                    flash(f"Convite para {inv.email} revogado.")
                req.status = "negado"
                db.session.commit()
                flash(f"Solicitação de {req.nome} negada.")
            else:
                flash("Solicitação não encontrada.")
            return redirect(url_for("admin_panel"))

        elif form_type == "hide_denied_request": # Novo tipo de formulário
            req_id = request.form.get("request_id", type=int)
            req = AccessRequest.query.get(req_id)
            if req and req.status == "negado":
                req.status = "oculto" # Altera o status para oculto
                db.session.commit()
                flash(f"Solicitação de {req.nome} (negada) foi ocultada.")
            else:
                flash("Solicitação não encontrada ou não está no status 'negado'.")
            return redirect(url_for("admin_panel"))

        elif form_type == "revoke_invite":
            invite_id = request.form.get("invite_id", type=int)
            inv = AccessInvite.query.get(invite_id)
            if inv and inv.status == "convidado":
                inv.status = "revogado"
                # Se o convite foi gerado a partir de uma solicitação, marca a solicitação como negada
                if inv.request_id:
                    req = AccessRequest.query.get(inv.request_id)
                    if req:
                        req.status = "negado"
                db.session.commit()
                flash(f"Convite para {inv.email} revogado.")
            else:
                flash("Convite não encontrado ou já ativado/revogado.")
            return redirect(url_for("admin_panel"))

        elif form_type == "block_user":
            user_id = request.form.get("user_id", type=int)
            user = User.query.get(user_id)
            if user and user.perfil != "admin": # Admins não podem bloquear outros admins
                user.status = "bloqueado"
                db.session.commit()
                flash(f"Usuário {user.nome} ({user.email}) bloqueado.")
            else:
                flash("Usuário não encontrado ou é um administrador.")
            return redirect(url_for("admin_panel"))

        elif form_type == "unblock_user":
            user_id = request.form.get("user_id", type=int)
            user = User.query.get(user_id)
            if user:
                user.status = "ativo"
                db.session.commit()
                flash(f"Usuário {user.nome} ({user.email}) desbloqueado.")
            else:
                flash("Usuário não encontrado.")
            return redirect(url_for("admin_panel"))

        elif form_type == "add_benchmark":
            cadeia = request.form.get("cadeia")
            cooperativa = request.form.get("cooperativa")
            media_gpd = float(request.form.get("media_gpd", 0) or 0)
            media_ca = float(request.form.get("media_ca", 0) or 0)
            bonus_base = float(request.form.get("bonus_base", 0) or 0)

            if not cadeia or not cooperativa:
                flash("Cadeia e Cooperativa são obrigatórios para o benchmark.")
                return redirect(url_for("admin_panel"))

            # Verifica se já existe um benchmark para essa cadeia/cooperativa
            existing_benchmark = CoopBenchmark.query.filter_by(cadeia=cadeia, cooperativa=cooperativa).first()
            if existing_benchmark:
                existing_benchmark.media_gpd = media_gpd
                existing_benchmark.media_ca = media_ca
                existing_benchmark.bonus_base = bonus_base
                existing_benchmark.atualizado_em = datetime.utcnow()
                flash(f"Benchmark para {cooperativa} ({cadeia}) atualizado.")
            else:
                new_benchmark = CoopBenchmark(
                    cadeia=cadeia,
                    cooperativa=cooperativa,
                    media_gpd=media_gpd,
                    media_ca=media_ca,
                    bonus_base=bonus_base
                )
                db.session.add(new_benchmark)
                flash(f"Benchmark para {cooperativa} ({cadeia}) adicionado.")
            db.session.commit()
            return redirect(url_for("admin_panel"))

        elif form_type == "delete_benchmark":
            benchmark_id = request.form.get("benchmark_id", type=int)
            benchmark = CoopBenchmark.query.get(benchmark_id)
            if benchmark:
                db.session.delete(benchmark)
                db.session.commit()
                flash(f"Benchmark para {benchmark.cooperativa} ({benchmark.cadeia}) excluído.")
            else:
                flash("Benchmark não encontrado.")
            return redirect(url_for("admin_panel"))

    # Filtra solicitações com status 'oculto' para não aparecerem na tabela principal
    requests = AccessRequest.query.filter(AccessRequest.status != "oculto").order_by(AccessRequest.criado_em.desc()).all()
    invites = AccessInvite.query.order_by(AccessInvite.criado_em.desc()).all()
    users = User.query.order_by(User.criado_em.desc()).all()
    benchmarks = CoopBenchmark.query.order_by(CoopBenchmark.cadeia, CoopBenchmark.cooperativa).all()

    html_content = """
    <h2>Painel Administrativo</h2>

    <div class="card">
      <h3>Gerar Convite Manual</h3>
      <form method="post">
        <input type="hidden" name="form_type" value="manual_invite">
        <input type="email" name="email" placeholder="E-mail do novo usuário" required>
        <button class="btn btn-pri" type="submit">Gerar Convite</button>
      </form>
    </div>

    <div class="card">
      <h3>Solicitações de Acesso</h3>
      <table>
        <tr><th>Nome</th><th>E-mail</th><th>Segmento</th><th>Cooperativa</th><th>Status</th><th>Data</th><th>Ações</th></tr>
        {% for r in requests %}
        <tr>
          <td>{{ r.nome }}</td>
          <td>{{ r.email }}</td>
          <td>{{ r.segmento|capitalize }}</td>
          <td>{{ r.cooperativa or "-" }}</td>
          <td>{{ r.status|capitalize }}</td>
          <td>{{ r.criado_em.strftime("%d/%m/%Y %H:%M") }}</td>
          <td>
            {% if r.status == 'pendente' %}
              <form method="post" action="{{ url_for('admin_panel') }}" style="display:inline;">
                <input type="hidden" name="form_type" value="approve_request">
                <input type="hidden" name="request_id" value="{{ r.id }}">
                <button type="submit" class="btn btn-ok">Aprovar</button>
              </form>
              <form method="post" action="{{ url_for('admin_panel') }}" style="display:inline;">
                <input type="hidden" name="form_type" value="deny_request">
                <input type="hidden" name="request_id" value="{{ r.id }}">
                <button type="submit" class="btn btn-ghost" onclick="return confirm('Tem certeza que deseja NEGAR esta solicitação?');">Negar</button>
              </form>
            {% elif r.status == 'liberado' %}
              <span class="muted">Liberado</span>
              <form method="post" action="{{ url_for('admin_panel') }}" style="display:inline;">
                <input type="hidden" name="form_type" value="deny_request">
                <input type="hidden" name="request_id" value="{{ r.id }}">
                <button type="submit" class="btn btn-ghost" onclick="return confirm('Tem certeza que deseja NEGAR esta solicitação e revogar o convite?');">Negar/Revogar</button>
              </form>
            {% elif r.status == 'negado' %} {# Adicionado botão para ocultar solicitações negadas #}
              <span class="muted">Negado</span>
              <form method="post" action="{{ url_for('admin_panel') }}" style="display:inline;">
                <input type="hidden" name="form_type" value="hide_denied_request">
                <input type="hidden" name="request_id" value="{{ r.id }}">
                <button type="submit" class="btn btn-ghost" onclick="return confirm('Ocultar esta solicitação negada? Ela não aparecerá mais nesta lista.');">Ocultar</button>
              </form>
            {% else %}
              <span class="muted">{{ r.status|capitalize }}</span>
            {% endif %}
          </td>
        </tr>
        {% endfor %}
      </table>
    </div>

    <div class="card">
      <h3>Convites Pendentes</h3>
      <table>
        <tr><th>E-mail</th><th>Token</th><th>Status</th><th>Data Criação</th><th>Ações</th></tr>
        {% for inv in invites %}
        <tr>
          <td>{{ inv.email }}</td>
          <td>{{ inv.token[:8] }}...</td>
          <td>{{ inv.status|capitalize }}</td>
          <td>{{ inv.criado_em.strftime("%d/%m/%Y %H:%M") }}</td>
          <td>
            {% if inv.status == 'convidado' %}
              <form method="post" action="{{ url_for('admin_panel') }}" style="display:inline;">
                <input type="hidden" name="form_type" value="revoke_invite">
                <input type="hidden" name="invite_id" value="{{ inv.id }}">
                <button type="submit" class="btn btn-ghost" onclick="return confirm('Tem certeza que deseja REVOGAR este convite?');">Revogar</button>
              </form>
            {% else %}
              <span class="muted">{{ inv.status|capitalize }}</span>
            {% endif %}
          </td>
        </tr>
        {% endfor %}
      </table>
    </div>

    <div class="card">
      <h3>Usuários Cadastrados</h3>
      <table>
        <tr><th>Nome</th><th>E-mail</th><th>Perfil</th><th>Status</th><th>Data Criação</th><th>Ações</th></tr>
        {% for u in users %}
        <tr>
          <td>{{ u.nome }}</td>
          <td>{{ u.email }}</td>
          <td>{{ u.perfil|capitalize }}</td>
          <td>{{ u.status|capitalize }}</td>
          <td>{{ u.criado_em.strftime("%d/%m/%Y %H:%M") }}</td>
          <td>
            {% if u.perfil != 'admin' %} {# Admins não podem bloquear outros admins #}
              {% if u.status == 'ativo' %}
                <form method="post" action="{{ url_for('admin_panel') }}" style="margin:0; display:inline-block;">
                  <input type="hidden" name="form_type" value="block_user">
                  <input type="hidden" name="user_id" value="{{ u.id }}">
                  <button type="submit" class="btn btn-danger" onclick="return confirm('Tem certeza que deseja BLOQUEAR o acesso deste usuário?');">Bloquear</button>
                </form>
              {% else %}
                <form method="post" action="{{ url_for('admin_panel') }}" style="margin:0; display:inline-block;">
                  <input type="hidden" name="form_type" value="unblock_user">
                  <input type="hidden" name="user_id" value="{{ u.id }}">
                  <button type="submit" class="btn btn-ok" onclick="return confirm('Tem certeza que deseja DESBLOQUEAR o acesso deste usuário?');">Desbloquear</button>
                </form>
              {% endif %}
            {% else %}
              <span class="muted">Admin</span>
            {% endif %}
          </td>
        </tr>
        {% endfor %}
      </table>
    </div>

    <div class="card">
      <h3>Benchmarks Gerais (Cooperativas)</h3>
      <form method="post">
        <input type="hidden" name="form_type" value="add_benchmark">
        <select name="cadeia" required>
          <option value="">Selecione a Cadeia</option>
          <option value="avicultura">Avicultura</option>
          <option value="suinocultura">Suinocultura</option>
        </select>
        <input name="cooperativa" placeholder="Nome da Cooperativa" required>
        <input type="number" step="0.0001" name="media_gpd" placeholder="GPD Médio" required>
        <input type="number" step="0.0001" name="media_ca" placeholder="CA Média" required>
        <input type="number" step="0.01" name="bonus_base" placeholder="Bônus Base R$" required>
        <button class="btn btn-pri" type="submit">Adicionar/Atualizar Benchmark</button>
      </form>

      <table>
        <tr><th>Cadeia</th><th>Cooperativa</th><th>GPD Médio</th><th>CA Média</th><th>Bônus Base</th><th>Atualizado Em</th><th>Ações</th></tr>
        {% for b in benchmarks %}
        <tr>
          <td>{{ b.cadeia|capitalize }}</td>
          <td>{{ b.cooperativa }}</td>
          <td>{{ b.media_gpd }}</td>
          <td>{{ b.media_ca }}</td>
          <td>R$ {{ b.bonus_base }}</td>
          <td>{{ b.atualizado_em.strftime("%d/%m/%Y %H:%M") }}</td>
          <td>
            {% if current_user.perfil == 'admin' %} {# Apenas admins podem excluir benchmarks #}
              <form method="post" action="{{ url_for('admin_panel') }}" style="display:inline;">
                <input type="hidden" name="form_type" value="delete_benchmark">
                <input type="hidden" name="benchmark_id" value="{{ b.id }}">
                <button type="submit" class="btn btn-ghost" onclick="return confirm('Tem certeza que deseja excluir este benchmark?');">Excluir</button>
              </form>
            {% else %}
              <span class="muted">Sem permissão</span>
            {% endif %}
          </td>
        </tr>
        {% endfor %}
      </table>
    </div>
    """
    return page(html_content, title="AP360 | Admin", requests=requests, invites=invites, users=users, benchmarks=benchmarks, admin_name=ADMIN_NAME)


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

        rs_ton, usd_bushel = cbot_para_rs_ton(produto)
        usd_brl = fx_usd_brl()
        frete = frete_medio(origem, porto)
        liquido_rs_ton = rs_ton - frete
        total_rs = liquido_rs_ton * quantidade_ton

        q = AgricultureQuote(
            user_id=current_user.id,
            produto=produto,
            quantidade_ton=quantidade_ton,
            origem=origem,
            porto=porto,
            cbot_usd_bushel=usd_bushel,
            usd_brl=usd_brl,
            export_rs_ton=rs_ton,
            frete_rs_ton=frete,
            liquido_rs_ton=liquido_rs_ton,
            total_rs=total_rs
        )
        db.session.add(q)
        db.session.commit()
        flash("Cotação registrada com sucesso!")
        return redirect(url_for("agricultura"))

    cotacoes = AgricultureQuote.query.filter_by(user_id=current_user.id).order_by(AgricultureQuote.criado_em.desc()).all()

    html_content = """
    <h2>Agricultura</h2>

    <div class="grid">
      <div class="card">
        <h3>Nova Cotação</h3>
        <form method="post">
          <select name="produto" required>
            <option value="">Selecione o Produto</option>
            <option value="soja">Soja</option>
            <option value="milho">Milho</option>
            <option value="trigo">Trigo</option>
            <option value="aveia">Aveia</option>
            <option value="arroz">Arroz</option>
          </select>
          <input type="number" step="0.01" name="quantidade_ton" placeholder="Quantidade (ton)" required>
          <input name="origem" placeholder="Origem (Cidade - UF)" required>
          <select name="porto" required>
            <option value="">Selecione o Porto</option>
            {% for p in portos %}
              <option value="{{ p }}">{{ p }}</option>
            {% endfor %}
          </select>
          <button class="btn btn-ok" type="submit">Calcular e Salvar</button>
        </form>
      </div>

      <div class="card">
        <h3>Última Cotação</h3>
        {% if cotacoes %}
          <p class="muted">Produto: <b>{{ cotacoes[0].produto|capitalize }}</b></p>
          <p class="muted">Quantidade: <b>{{ cotacoes[0].quantidade_ton }} ton</b></p>
          <p class="muted">Origem: <b>{{ cotacoes[0].origem }}</b></p>
          <p class="muted">Porto: <b>{{ cotacoes[0].porto }}</b></p>
          <p class="muted">CBOT (USD/Bushel): <b>{{ cotacoes[0].cbot_usd_bushel }}</b></p>
          <p class="muted">USD/BRL: <b>{{ cotacoes[0].usd_brl }}</b></p>
          <p class="muted">Exportação (R$/ton): <b>R$ {{ cotacoes[0].export_rs_ton }}</b></p>
          <p class="muted">Frete (R$/ton): <b>R$ {{ cotacoes[0].frete_rs_ton }}</b></p>
          <p class="muted">Líquido (R$/ton): <b>R$ {{ cotacoes[0].liquido_rs_ton }}</b></p>
          <p class="muted">Total (R$): <b class="kpi">R$ {{ cotacoes[0].total_rs }}</b></p>
        {% else %}
          <p class="muted">Nenhuma cotação registrada ainda.</p>
        {% endif %}
      </div>
    </div>

    <div class="card">
      <h3>Histórico de Cotações</h3>
      <table>
        <tr><th>Data</th><th>Produto</th><th>Origem</th><th>Porto</th><th>Qtd (ton)</th><th>Líquido (R$/ton)</th><th>Total (R$)</th><th>Ações</th></tr>
        {% for c in cotacoes %}
        <tr>
          <td>{{ c.criado_em.strftime("%d/%m %H:%M") }}</td>
          <td>{{ c.produto|capitalize }}</td>
          <td>{{ c.origem }}</td>
          <td>{{ c.porto }}</td>
          <td>{{ c.quantidade_ton }}</td>
          <td>R$ {{ c.liquido_rs_ton }}</td>
          <td>R$ {{ c.total_rs }}</td>
          <td>
            <form method="post" action="{{ url_for('excluir_cotacao', cotacao_id=c.id) }}" style="display:inline;">
              <button type="submit" class="btn btn-ghost" onclick="return confirm('Tem certeza que deseja excluir esta cotação?');">Excluir</button>
            </form>
          </td>
        </tr>
        {% endfor %}
      </table>
    </div>
    """
    return page(html_content, title="AP360 | Agricultura", portos=PORTOS, cotacoes=cotacoes)


@app.route("/agricultura/excluir/<int:cotacao_id>", methods=["POST"])
@login_required
def excluir_cotacao(cotacao_id):
    cotacao = AgricultureQuote.query.filter_by(id=cotacao_id, user_id=current_user.id).first_or_404()
    db.session.delete(cotacao)
    db.session.commit()
    flash("Cotação excluída com sucesso!")
    return redirect(url_for("agricultura"))


# =========================================================
# AGROSIN (Agricultura - Talhões)
# =========================================================
@app.route("/agrosim", methods=["GET", "POST"])
@login_required
def agrosim():
    if request.method == "POST":
        form_type = request.form.get("form_type")

        if form_type == "novo_talhao":
            nome_talhao = request.form.get("nome_talhao", "").strip()
            cultura = request.form.get("cultura", "").strip()
            area_ha = float(request.form.get("area_ha", 0) or 0)
            data_plantio_str = request.form.get("data_plantio", "")
            data_colheita_estimada_str = request.form.get("data_colheita_estimada", "")

            if not nome_talhao or not cultura or area_ha <= 0 or not data_plantio_str:
                flash("Preencha todos os campos obrigatórios para o talhão.")
                return redirect(url_for("agrosim"))

            data_plantio = datetime.strptime(data_plantio_str, "%Y-%m-%d").date()
            data_colheita_estimada = datetime.strptime(data_colheita_estimada_str, "%Y-%m-%d").date() if data_colheita_estimada_str else None

            # Verifica unicidade do nome do talhão por usuário
            if AgriculturePlot.query.filter_by(user_id=current_user.id, nome_talhao=nome_talhao).first():
                flash(f"Já existe um talhão com o nome '{nome_talhao}' para você.")
                return redirect(url_for("agrosim"))

            plot = AgriculturePlot(
                user_id=current_user.id,
                nome_talhao=nome_talhao,
                cultura=cultura,
                area_ha=area_ha,
                data_plantio=data_plantio,
                data_colheita_estimada=data_colheita_estimada
            )
            db.session.add(plot)
            db.session.commit()
            flash(f"Talhão '{nome_talhao}' registrado com sucesso!")
            return redirect(url_for("agrosim", plot_id=plot.id))

        elif form_type == "novo_rendimento":
            plot_id = int(request.form.get("plot_id"))
            plot = AgriculturePlot.query.filter_by(id=plot_id, user_id=current_user.id).first_or_404()
            data_registro_str = request.form.get("data_registro", "")
            produtividade_kg_ha = float(request.form.get("produtividade_kg_ha", 0) or 0)
            observacoes = request.form.get("observacoes", "").strip()

            if not data_registro_str or produtividade_kg_ha <= 0:
                flash("Preencha a data e a produtividade.")
                return redirect(url_for("agrosim", plot_id=plot.id))

            data_registro = datetime.strptime(data_registro_str, "%Y-%m-%d").date()

            yield_entry = PlotYield(
                plot_id=plot.id,
                data_registro=data_registro,
                produtividade_kg_ha=produtividade_kg_ha,
                observacoes=observacoes
            )
            db.session.add(yield_entry)
            db.session.commit()
            flash("Registro de produtividade adicionado.")
            return redirect(url_for("agrosim", plot_id=plot.id))

        elif form_type == "novo_insumo":
            plot_id = int(request.form.get("plot_id"))
            plot = AgriculturePlot.query.filter_by(id=plot_id, user_id=current_user.id).first_or_404()
            tipo_insumo = request.form.get("tipo_insumo", "").strip()
            nome_insumo = request.form.get("nome_insumo", "").strip()
            quantidade = float(request.form.get("quantidade", 0) or 0)
            unidade = request.form.get("unidade", "").strip()
            custo_total_rs = float(request.form.get("custo_total_rs", 0) or 0)
            data_aplicacao_str = request.form.get("data_aplicacao", "")

            if not tipo_insumo or not nome_insumo or quantidade <= 0 or not unidade or custo_total_rs < 0 or not data_aplicacao_str:
                flash("Preencha todos os campos obrigatórios para o insumo.")
                return redirect(url_for("agrosim", plot_id=plot.id))

            data_aplicacao = datetime.strptime(data_aplicacao_str, "%Y-%m-%d").date()

            input_entry = PlotInput(
                plot_id=plot.id,
                tipo_insumo=tipo_insumo,
                nome_insumo=nome_insumo,
                quantidade=quantidade,
                unidade=unidade,
                custo_total_rs=custo_total_rs,
                data_aplicacao=data_aplicacao
            )
            db.session.add(input_entry)
            db.session.commit()
            flash("Insumo registrado.")
            return redirect(url_for("agrosim", plot_id=plot.id))

    plots = AgriculturePlot.query.filter_by(user_id=current_user.id).order_by(AgriculturePlot.data_plantio.desc()).all()
    selected_plot_id = request.args.get("plot_id", type=int)

    plot_detail = None
    yield_data = []
    input_data = []
    yield_chart_data = None
    cost_chart_data = None

    if selected_plot_id:
        plot_detail = AgriculturePlot.query.filter_by(id=selected_plot_id, user_id=current_user.id).first()
        if plot_detail:
            yield_data = PlotYield.query.filter_by(plot_id=plot_detail.id).order_by(PlotYield.data_registro.asc()).all()
            input_data = PlotInput.query.filter_by(plot_id=plot_detail.id).order_by(PlotInput.data_aplicacao.asc()).all()

            if yield_data:
                yield_chart_data = {
                    "labels": [y.data_registro.strftime("%d/%m/%Y") for y in yield_data],
                    "data": [y.produtividade_kg_ha for y in yield_data]
                }

            if input_data:
                # Agrupar custos por tipo de insumo para o gráfico de pizza
                cost_by_type = {}
                for i in input_data:
                    cost_by_type[i.tipo_insumo] = cost_by_type.get(i.tipo_insumo, 0) + i.custo_total_rs

                cost_chart_data = {
                    "labels": list(cost_by_type.keys()),
                    "data": list(cost_by_type.values())
                }


    html_content = """
    <h2>Agrosim - Gestão de Talhões</h2>

    <div class="grid">
      <div class="card">
        <h3>Novo Talhão</h3>
        <form method="post">
          <input type="hidden" name="form_type" value="novo_talhao">
          <input name="nome_talhao" placeholder="Nome do Talhão (ex: Talhão A)" required>
          <input name="cultura" placeholder="Cultura (ex: Soja, Milho)" required>
          <input type="number" step="0.01" name="area_ha" placeholder="Área (hectares)" required>
          <label>Data de Plantio</label>
          <input type="date" name="data_plantio" required>
          <label>Data de Colheita Estimada</label>
          <input type="date" name="data_colheita_estimada">
          <button class="btn btn-ok" type="submit">Registrar Talhão</button>
        </form>
      </div>

      <div class="card">
        <h3>Meus Talhões</h3>
        {% if plots %}
          <table>
            <tr><th>Talhão</th><th>Cultura</th><th>Área (ha)</th><th>Plantio</th><th>Ações</th></tr>
            {% for p in plots %}
              <tr>
                <td>{{ p.nome_talhao }}</td>
                <td>{{ p.cultura }}</td>
                <td>{{ p.area_ha }}</td>
                <td>{{ p.data_plantio.strftime("%d/%m/%Y") }}</td>
                <td>
                  <a class="btn btn-ghost" href="{{ url_for('agrosim', plot_id=p.id) }}">Detalhes</a>
                  <a class="btn btn-ghost" href="{{ url_for('editar_talhao', plot_id=p.id) }}">Editar</a>
                  <form method="post" action="{{ url_for('excluir_talhao', plot_id=p.id) }}" style="display:inline;">
                    <button type="submit" class="btn btn-ghost" onclick="return confirm('Tem certeza que deseja excluir este talhão e todos os seus registros?');">Excluir</button>
                  </form>
                </td>
              </tr>
            {% endfor %}
          </table>
        {% else %}
          <p class="muted">Nenhum talhão registrado ainda.</p>
        {% endif %}
      </div>
    </div>

    {% if plot_detail %}
    <div class="card">
      <h3>Detalhes do Talhão: {{ plot_detail.nome_talhao }} ({{ plot_detail.cultura }})</h3>
      <p class="muted">Área: <b>{{ plot_detail.area_ha }} ha</b> | Plantio: <b>{{ plot_detail.data_plantio.strftime("%d/%m/%Y") }}</b> | Colheita Estimada: <b>{{ plot_detail.data_colheita_estimada.strftime("%d/%m/%Y") if plot_detail.data_colheita_estimada else '-' }}</b></p>
    </div>

    <div class="grid">
      <div class="card">
        <h3>Registrar Produtividade</h3>
        <form method="post">
          <input type="hidden" name="form_type" value="novo_rendimento">
          <input type="hidden" name="plot_id" value="{{ plot_detail.id }}">
          <label>Data do Registro</label>
          <input type="date" name="data_registro" required>
          <input type="number" step="0.01" name="produtividade_kg_ha" placeholder="Produtividade (kg/ha)" required>
          <textarea name="observacoes" placeholder="Observações (opcional)"></textarea>
          <button class="btn btn-pri" type="submit">Adicionar Produtividade</button>
        </form>
        {% if yield_chart_data %}
        <h4 style="margin-top: 20px;">Histórico de Produtividade</h4>
        <canvas id="yieldChart" height="90"></canvas>
        <script>
          const yieldData = {{ yield_chart_data | tojson }};
          new Chart(document.getElementById("yieldChart"), {
            type: "line",
            data: {
              labels: yieldData.labels,
              datasets: [{
                label: "Produtividade (kg/ha)",
                data: yieldData.data,
                borderColor: 'rgb(75, 192, 192)',
                tension: 0.1,
                fill: false
              }]
            },
            options: { responsive: true }
          });
        </script>
        {% endif %}
      </div>

      <div class="card">
        <h3>Registrar Insumo/Custo</h3>
        <form method="post">
          <input type="hidden" name="form_type" value="novo_insumo">
          <input type="hidden" name="plot_id" value="{{ plot_detail.id }}">
          <select name="tipo_insumo" required>
            <option value="">Tipo de Insumo</option>
            <option value="semente">Semente</option>
            <option value="fertilizante">Fertilizante</option>
            <option value="defensivo">Defensivo</option>
            <option value="combustivel">Combustível</option>
            <option value="mao_de_obra">Mão de Obra</option>
            <option value="outros">Outros</option>
          </select>
          <input name="nome_insumo" placeholder="Nome do Insumo (ex: Ureia, Glifosato)" required>
          <input type="number" step="0.01" name="quantidade" placeholder="Quantidade" required>
          <input name="unidade" placeholder="Unidade (ex: kg, L, saco)" required>
          <input type="number" step="0.01" name="custo_total_rs" placeholder="Custo Total (R$)" required>
          <label>Data de Aplicação</label>
          <input type="date" name="data_aplicacao" required>
          <button class="btn btn-pri" type="submit">Adicionar Insumo</button>
        </form>
        {% if cost_chart_data %}
        <h4 style="margin-top: 20px;">Custos por Tipo de Insumo</h4>
        <canvas id="costChart" height="90"></canvas>
        <script>
          const costData = {{ cost_chart_data | tojson }};
          new Chart(document.getElementById("costChart"), {
            type: "pie",
            data: {
              labels: costData.labels,
              datasets: [{
                data: costData.data,
                backgroundColor: [
                  '#FF6384', '#36A2EB', '#FFCE56', '#4BC0C0', '#9966FF', '#FF9F40'
                ]
              }]
            },
            options: { responsive: true }
          });
        </script>
        {% endif %}
      </div>
    </div>

    <div class="card">
      <h3>Histórico de Produtividade</h3>
      {% if yield_data %}
      <table>
        <tr><th>Data</th><th>Produtividade (kg/ha)</th><th>Observações</th><th>Ações</th></tr>
        {% for y in yield_data %}
        <tr>
          <td>{{ y.data_registro.strftime("%d/%m/%Y") }}</td>
          <td>{{ y.produtividade_kg_ha }}</td>
          <td>{{ y.observacoes or '-' }}</td>
          <td>
            <form method="post" action="{{ url_for('excluir_rendimento', yield_id=y.id) }}" style="display:inline;">
              <button type="submit" class="btn btn-ghost" onclick="return confirm('Tem certeza que deseja excluir este registro de produtividade?');">Excluir</button>
            </form>
          </td>
        </tr>
        {% endfor %}
      </table>
      {% else %}
      <p class="muted">Nenhum registro de produtividade para este talhão.</p>
      {% endif %}
    </div>

    <div class="card">
      <h3>Histórico de Insumos/Custos</h3>
      {% if input_data %}
      <table>
        <tr><th>Data</th><th>Tipo</th><th>Insumo</th><th>Qtd</th><th>Unidade</th><th>Custo (R$)</th><th>Ações</th></tr>
        {% for i in input_data %}
        <tr>
          <td>{{ i.data_aplicacao.strftime("%d/%m/%Y") }}</td>
          <td>{{ i.tipo_insumo|capitalize }}</td>
          <td>{{ i.nome_insumo }}</td>
          <td>{{ i.quantidade }}</td>
          <td>{{ i.unidade }}</td>
          <td>R$ {{ i.custo_total_rs }}</td>
          <td>
            <form method="post" action="{{ url_for('excluir_insumo', input_id=i.id) }}" style="display:inline;">
              <button type="submit" class="btn btn-ghost" onclick="return confirm('Tem certeza que deseja excluir este registro de insumo?');">Excluir</button>
            </form>
          </td>
        </tr>
        {% endfor %}
      </table>
      {% else %}
      <p class="muted">Nenhum registro de insumo para este talhão.</p>
      {% endif %}
    </div>
    {% endif %}
    """
    return page(html_content, title="AP360 | Agrosim", plots=plots, plot_detail=plot_detail,
                yield_data=yield_data, input_data=input_data,
                yield_chart_data=yield_chart_data, cost_chart_data=cost_chart_data)


@app.route("/agrosim/editar_talhao/<int:plot_id>", methods=["GET", "POST"])
@login_required
def editar_talhao(plot_id):
    plot = AgriculturePlot.query.filter_by(id=plot_id, user_id=current_user.id).first_or_404()

    if request.method == "POST":
        new_nome_talhao = request.form.get("nome_talhao", "").strip()
        cultura = request.form.get("cultura", "").strip()
        area_ha = float(request.form.get("area_ha", 0) or 0)
        data_plantio_str = request.form.get("data_plantio", "")
        data_colheita_estimada_str = request.form.get("data_colheita_estimada", "")

        if not new_nome_talhao or not cultura or area_ha <= 0 or not data_plantio_str:
            flash("Preencha todos os campos obrigatórios para o talhão.")
            return redirect(url_for("editar_talhao", plot_id=plot.id))

        # Verifica unicidade do nome do talhão por usuário, excluindo o próprio talhão
        if AgriculturePlot.query.filter(
            AgriculturePlot.user_id == current_user.id,
            AgriculturePlot.nome_talhao == new_nome_talhao,
            AgriculturePlot.id != plot_id
        ).first():
            flash(f"Já existe outro talhão com o nome '{new_nome_talhao}' para você.")
            return redirect(url_for("editar_talhao", plot_id=plot.id))

        plot.nome_talhao = new_nome_talhao
        plot.cultura = cultura
        plot.area_ha = area_ha
        plot.data_plantio = datetime.strptime(data_plantio_str, "%Y-%m-%d").date()
        plot.data_colheita_estimada = datetime.strptime(data_colheita_estimada_str, "%Y-%m-%d").date() if data_colheita_estimada_str else None
        db.session.commit()
        flash("Talhão atualizado com sucesso!")
        return redirect(url_for("agrosim", plot_id=plot.id))

    html_content = """
    <h2>Editar Talhão: {{ plot.nome_talhao }}</h2>
    <div class="card" style="max-width:700px;margin:0 auto">
      <form method="post">
        <label>Nome do Talhão</label>
        <input name="nome_talhao" value="{{ plot.nome_talhao }}" required>
        <label>Cultura</label>
        <input name="cultura" value="{{ plot.cultura }}" required>
        <label>Área (hectares)</label>
        <input type="number" step="0.01" name="area_ha" value="{{ plot.area_ha }}" required>
        <label>Data de Plantio</label>
        <input type="date" name="data_plantio" value="{{ plot.data_plantio.strftime('%Y-%m-%d') }}" required>
        <label>Data de Colheita Estimada</label>
        <input type="date" name="data_colheita_estimada" value="{{ plot.data_colheita_estimada.strftime('%Y-%m-%d') if plot.data_colheita_estimada else '' }}">
        <button class="btn btn-ok" type="submit">Salvar Alterações</button>
        <a class="btn btn-ghost" href="{{ url_for('agrosim', plot_id=plot.id) }}">Cancelar</a>
      </form>
    </div>
    """
    return page(html_content, title="AP360 | Editar Talhão", plot=plot)


@app.route("/agrosim/excluir_talhao/<int:plot_id>", methods=["POST"])
@login_required
def excluir_talhao(plot_id):
    plot = AgriculturePlot.query.filter_by(id=plot_id, user_id=current_user.id).first_or_404()
    # Excluir registros de produtividade e insumos relacionados
    PlotYield.query.filter_by(plot_id=plot.id).delete()
    PlotInput.query.filter_by(plot_id=plot.id).delete()
    db.session.delete(plot)
    db.session.commit()
    flash("Talhão e todos os seus registros excluídos com sucesso!")
    return redirect(url_for("agrosim"))


@app.route("/agrosim/excluir_rendimento/<int:yield_id>", methods=["POST"])
@login_required
def excluir_rendimento(yield_id):
    yield_entry = PlotYield.query.get_or_404(yield_id)
    plot_id = yield_entry.plot_id
    plot = AgriculturePlot.query.filter_by(id=plot_id, user_id=current_user.id).first_or_404() # Garante que o usuário é o dono

    db.session.delete(yield_entry)
    db.session.commit()
    flash("Registro de produtividade excluído com sucesso!")
    return redirect(url_for("agrosim", plot_id=plot_id))


@app.route("/agrosim/excluir_insumo/<int:input_id>", methods=["POST"])
@login_required
def excluir_insumo(input_id):
    input_entry = PlotInput.query.get_or_404(input_id)
    plot_id = input_entry.plot_id
    plot = AgriculturePlot.query.filter_by(id=plot_id, user_id=current_user.id).first_or_404() # Garante que o usuário é o dono

    db.session.delete(input_entry)
    db.session.commit()
    flash("Registro de insumo excluído com sucesso!")
    return redirect(url_for("agrosim", plot_id=plot_id))


# =========================================================
# AVICULTURA / SUINOCULTURA (Lotes)
# =========================================================
def modulo_lotes(cadeia: str):
    resultado = None
    compare_data = None
    c1 = None
    c2 = None

    if request.method == "POST":
        # Pega os valores do formulário
        estrutura = request.form.get("estrutura", "").strip()
        lote = request.form.get("lote", "").strip()
        peso_inicial_medio = float(request.form.get("peso_inicial", 0) or 0) # Peso MÉDIO por animal
        peso_final_medio = float(request.form.get("peso_final", 0) or 0)     # Peso MÉDIO por animal
        racao_total_lote = float(request.form.get("racao_total_kg", 0) or 0) # Ração TOTAL do lote
        animais_iniciais = int(request.form.get("animais_iniciais", 0) or 0)
        animais_final = int(request.form.get("animais_final", 0) or 0)

        # Novos campos de data e hora
        alojamento_dt_str = request.form.get("alojamento_datetime", "")
        carregamento_dt_str = request.form.get("carregamento_datetime", "")

        if not alojamento_dt_str or not carregamento_dt_str:
            flash("Data e hora de alojamento e carregamento são obrigatórias.")
            return redirect(url_for(cadeia))

        try:
            alojamento_datetime = datetime.strptime(alojamento_dt_str, "%Y-%m-%dT%H:%M")
            carregamento_datetime = datetime.strptime(carregamento_dt_str, "%Y-%m-%dT%H:%M")
        except ValueError:
            flash("Formato de data e hora inválido. Use YYYY-MM-DDTHH:MM.")
            return redirect(url_for(cadeia))

        dias_alojamento = calc_dias_alojamento(alojamento_datetime, carregamento_datetime)

        # --- CÁLCULOS ATUALIZADOS ---
        gpd = calc_gpd(peso_inicial_medio, peso_final_medio, dias_alojamento)
        ca = calc_ca(racao_total_lote, peso_inicial_medio, peso_final_medio, animais_final)
        # --- FIM DOS CÁLCULOS ATUALIZADOS ---

        viabilidade = calc_viabilidade(animais_iniciais, animais_final)
        mortalidade = calc_mortalidade(animais_iniciais, animais_final)

        # Referência manual (cooperativa informada pelo produtor)
        ca_coop_ref = float(request.form.get("ca_coop_ref", 0) or 0)
        gpd_coop_ref = float(request.form.get("gpd_coop_ref", 0) or 0)

        # benchmark base: prioriza pessoal, depois cooperativa, depois global
        meta_gpd_efetivo, meta_ca_efetivo, bonus_base_efetivo = get_effective_benchmark(cadeia, current_user.cooperativa, current_user)

        meta_ca = ca_coop_ref if ca_coop_ref > 0 else meta_ca_efetivo
        meta_gpd = gpd_coop_ref if gpd_coop_ref > 0 else meta_gpd_efetivo
        bonus_base = bonus_base_efetivo

        ca_ajustada = ca # Default
        iep = 0.0
        indice_lote = 0.0
        peso_vivo_medio = 0.0
        peso_carcaca_medio = 0.0
        rendimento_carcaca_pct = 0.0
        carne_magra_pct = 0.0
        bonus_tipificacao = 0.0

        if cadeia == "avicultura":
            peso_meta = float(request.form.get("peso_meta_coop", 0) or 0)
            idade_meta = int(request.form.get("idade_meta_coop", 0) or 0)
            fator_peso = float(request.form.get("fator_peso_caa", 0.30) or 0.30)
            fator_idade = float(request.form.get("fator_idade_caa", 0.01) or 0.01)

            if peso_meta > 0 and idade_meta > 0:
                ca_ajustada = calc_ca_ajustada_avicultura(
                    ca_observada=ca,
                    peso_real=peso_final_medio,
                    idade_real_dias=dias_alojamento, # Usando dias_alojamento float
                    peso_meta=peso_meta,
                    idade_meta_dias=idade_meta,
                    fator_peso=fator_peso,
                    fator_idade=fator_idade
                )
            iep = calc_iep_avicultura(viabilidade, peso_final_medio, dias_alojamento, ca_ajustada)

        if cadeia == "suinocultura":
            peso_vivo_medio = float(request.form.get("peso_vivo_medio", 0) or 0)
            peso_carcaca_medio = float(request.form.get("peso_carcaca_medio", 0) or 0)
            carne_magra_pct = float(request.form.get("carne_magra_pct", 0) or 0)

            rendimento_carcaca_pct = calc_rendimento_carcaca(peso_vivo_medio, peso_carcaca_medio)

            if peso_vivo_medio > 0:
                ajuste_peso = 0.003 * (peso_vivo_medio - 120.0)
                ca_ajustada = round(max(ca + ajuste_peso, 0.01), 4)

            indice_lote = calc_indice_lote_suino(gpd, viabilidade, ca_ajustada)
            bonus_tipificacao = calc_bonus_tipificacao(carne_magra_pct, rendimento_carcaca_pct)

        bon_total = calc_bonificacao(gpd, ca_ajustada, meta_gpd, meta_ca, bonus_base)
        bonificacao = round(bon_total + bonus_tipificacao, 2)

        b = Batch(
            user_id=current_user.id,
            cadeia=cadeia,
            estrutura=estrutura,
            lote=lote,
            peso_inicial=peso_inicial_medio,
            peso_final=peso_final_medio,
            alojamento_datetime=alojamento_datetime,
            carregamento_datetime=carregamento_datetime,
            dias_alojamento=dias_alojamento,
            racao_total_kg=racao_total_lote,
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
            bonificacao=bonificacao,
            coop_media_gpd=meta_gpd,
            coop_media_ca=meta_ca
        )
        db.session.add(b)
        db.session.commit()
        flash("Lote registrado com sucesso!")
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
          <input type="number" step="0.0001" name="peso_inicial" placeholder="Peso inicial médio por animal (kg)" required>
          <input type="number" step="0.0001" name="peso_final" placeholder="Peso final médio por animal (kg)" required>
          <label>Data e Hora de Alojamento</label>
          <input type="datetime-local" name="alojamento_datetime" required>
          <label>Data e Hora de Carregamento</label>
          <input type="datetime-local" name="carregamento_datetime" required>
          <input type="number" step="0.0001" name="racao_total_kg" placeholder="Ração TOTAL consumida pelo lote (kg)" required>
          <input type="number" name="animais_iniciais" placeholder="Animais iniciais" required>
          <input type="number" name="animais_final" placeholder="Animais finais (abatidos/vendidos)" required>

          <h4>Minhas referências para este lote (opcional)</h4>
          <p class="muted">Se preenchido, estes valores sobrescrevem seus benchmarks pessoais e os da cooperativa para este lote.</p>
          <input type="number" step="0.0001" name="gpd_coop_ref" placeholder="GPD médio (opcional)" value="{{ resultado.gpd_coop_ref if resultado else '' }}">
          <input type="number" step="0.0001" name="ca_coop_ref" placeholder="CA média (opcional)" value="{{ resultado.ca_coop_ref if resultado else '' }}">

          {% if cadeia == 'avicultura' %}
            <h4>CA Ajustada (Avicultura)</h4>
            <input type="number" step="0.0001" name="peso_meta_coop" placeholder="Peso meta coop (kg), ex: 2.90" required value="{{ resultado.peso_meta_coop if resultado else '' }}">
            <input type="number" name="idade_meta_coop" placeholder="Idade meta coop (dias), ex: 42" required value="{{ resultado.idade_meta_coop if resultado else '' }}">
            <input type="number" step="0.0001" name="fator_peso_caa" value="{{ resultado.fator_peso_caa if resultado else '0.30' }}" placeholder="Fator peso CAA">
            <input type="number" step="0.0001" name="fator_idade_caa" value="{{ resultado.fator_idade_caa if resultado else '0.01' }}" placeholder="Fator idade CAA">
          {% endif %}

          {% if cadeia == 'suinocultura' %}
            <h4>Carcaça e tipificação (Suínos)</h4>
            <input type="number" step="0.01" name="peso_vivo_medio" placeholder="Peso vivo médio (kg/cab)" required value="{{ resultado.peso_vivo_medio if resultado else '' }}">
            <input type="number" step="0.01" name="peso_carcaca_medio" placeholder="Peso carcaça médio (kg/cab)" required value="{{ resultado.peso_carcaca_medio if resultado else '' }}">
            <input type="number" step="0.01" name="carne_magra_pct" placeholder="% carne magra (ex: 57.5)" required value="{{ resultado.carne_magra_pct if resultado else '' }}">
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
        <h3>Resultado do lote {{ resultado.estrutura }}/{{ resultado.lote }}</h3>
        <p class="muted">Dias de alojamento: <b>{{ resultado.dias_alojamento }}</b></p>
        <div class="grid3">
          <div><div class="muted">GPD</div><div class="kpi">{{ "%.4f"|format(resultado.gpd) }}</div></div>
          <div><div class="muted">CA</div><div class="kpi">{{ "%.4f"|format(resultado.ca) }}</div></div>
          <div><div class="muted">CA Ajustada</div><div class="kpi">{{ "%.4f"|format(resultado.ca_ajustada) }}</div></div>
        </div>
        <div class="grid3">
          <div><div class="muted">Viabilidade</div><div class="kpi">{{ "%.2f"|format(resultado.viabilidade_pct) }}%</div></div>
          <div><div class="muted">Mortalidade</div><div class="kpi">{{ "%.2f"|format(resultado.mortalidade_pct) }}%</div></div>
          <div><div class="muted">Bônus total</div><div class="kpi">R$ {{ "%.2f"|format(resultado.bonificacao) }}</div></div>
        </div>

        {% if cadeia == 'avicultura' %}
        <div class="grid3">
          <div><div class="muted">IEP/EPEF</div><div class="kpi">{{ "%.2f"|format(resultado.iep) }}</div></div>
          <div><div class="muted">Peso meta</div><div class="kpi">{{ "%.2f"|format(resultado.peso_meta_coop) }}</div></div>
          <div><div class="muted">Idade meta</div><div class="kpi">{{ resultado.idade_meta_coop }}</div></div>
        </div>
        {% endif %}

        {% if cadeia == 'suinocultura' %}
        <div class="grid3">
          <div><div class="muted">Rendimento carcaça</div><div class="kpi">{{ "%.2f"|format(resultado.rendimento_carcaca_pct) }}%</div></div>
          <div><div class="muted">% carne magra</div><div class="kpi">{{ "%.2f"|format(resultado.carne_magra_pct) }}%</div></div>
          <div><div class="muted">Bônus tipificação</div><div class="kpi">R$ {{ "%.2f"|format(resultado.bonus_tipificacao) }}</div></div>
        </div>
        <div class="grid3">
          <div><div class="muted">Índice lote</div><div class="kpi">{{ "%.2f"|format(resultado.indice_lote) }}</div></div>
          <div><div class="muted">Peso vivo médio</div><div class="kpi">{{ "%.2f"|format(resultado.peso_vivo_medio) }}</div></div>
          <div><div class="muted">Peso carcaça médio</div><div class="kpi">{{ "%.2f"|format(resultado.peso_carcaca_medio) }}</div></div>
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
              { label: cmp.a_name, data: cmp.a_vals, backgroundColor: 'rgba(59, 185, 255, 0.6)' },
              { label: cmp.b_name, data: cmp.b_vals, backgroundColor: 'rgba(70, 221, 152, 0.6)' }
            ]
          },
          options: { responsive: true, scales: { y: { beginAtZero: true } } }
        });
      </script>
      {% endif %}
    </div>

    <div class="card">
      <h3>Histórico</h3>
      <table>
        <tr>
          <th>Data</th><th>Estrutura</th><th>Lote</th><th>Dias</th><th>GPD</th><th>CA</th><th>CAA</th>
          <th>Viab%</th><th>Mort%</th><th>IEP/Índice</th><th>Rend. Carcaça%</th><th>Bônus</th>
          <th>Ações</th>
        </tr>
        {% for h in hist %}
        <tr>
          <td>{{ h.criado_em.strftime("%d/%m %H:%M") }}</td>
          <td>{{ h.estrutura }}</td>
          <td>{{ h.lote }}</td>
          <td>{{ "%.2f"|format(h.dias_alojamento) }}</td>
          <td>{{ "%.4f"|format(h.gpd) }}</td>
          <td>{{ "%.4f"|format(h.ca) }}</td>
          <td>{{ "%.4f"|format(h.ca_ajustada) }}</td>
          <td>{{ "%.2f"|format(h.viabilidade_pct) }}</td>
          <td>{{ "%.2f"|format(h.mortalidade_pct) }}</td>
          <td>{% if cadeia == 'avicultura' %}{{ "%.2f"|format(h.iep) }}{% else %}{{ "%.2f"|format(h.indice_lote) }}{% endif %}</td>
          <td>{{ "%.2f"|format(h.rendimento_carcaca_pct) }}</td>
          <td>R$ {{ "%.2f"|format(h.bonificacao) }}</td>
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
    batch = Batch.query.filter_by(id=batch_id, user_id=current_user.id).first_or_404()

    if request.method == "POST":
        batch.estrutura = request.form.get("estrutura", "").strip()
        batch.lote = request.form.get("lote", "").strip()
        batch.peso_inicial = float(request.form.get("peso_inicial", 0) or 0) # Peso MÉDIO por animal
        batch.peso_final = float(request.form.get("peso_final", 0) or 0)     # Peso MÉDIO por animal
        batch.racao_total_kg = float(request.form.get("racao_total_kg", 0) or 0) # Ração TOTAL do lote
        batch.animais_iniciais = int(request.form.get("animais_iniciais", 0) or 0)
        batch.animais_final = int(request.form.get("animais_final", 0) or 0)

        # Novos campos de data e hora
        alojamento_dt_str = request.form.get("alojamento_datetime", "")
        carregamento_dt_str = request.form.get("carregamento_datetime", "")

        if not alojamento_dt_str or not carregamento_dt_str:
            flash("Data e hora de alojamento e carregamento são obrigatórias.")
            return redirect(url_for("editar_lote", cadeia=cadeia, batch_id=batch.id))

        try:
            batch.alojamento_datetime = datetime.strptime(alojamento_dt_str, "%Y-%m-%dT%H:%M")
            batch.carregamento_datetime = datetime.strptime(carregamento_dt_str, "%Y-%m-%dT%H:%M")
        except ValueError:
            flash("Formato de data e hora inválido. Use YYYY-MM-DDTHH:MM.")
            return redirect(url_for("editar_lote", cadeia=cadeia, batch_id=batch.id))

        batch.dias_alojamento = calc_dias_alojamento(batch.alojamento_datetime, batch.carregamento_datetime)

        # --- CÁLCULOS ATUALIZADOS ---
        batch.gpd = calc_gpd(batch.peso_inicial, batch.peso_final, batch.dias_alojamento)
        batch.ca = calc_ca(batch.racao_total_kg, batch.peso_inicial, batch.peso_final, batch.animais_final)
        # --- FIM DOS CÁLCULOS ATUALIZADOS ---

        batch.viabilidade_pct = calc_viabilidade(batch.animais_iniciais, batch.animais_final)
        batch.mortalidade_pct = calc_mortalidade(batch.animais_iniciais, batch.animais_final)

        # Referência manual (cooperativa informada pelo produtor)
        batch.ca_coop_ref = float(request.form.get("ca_coop_ref", 0) or 0)
        batch.gpd_coop_ref = float(request.form.get("gpd_coop_ref", 0) or 0)

        # benchmark base: prioriza pessoal, depois cooperativa, depois global
        meta_gpd_efetivo, meta_ca_efetivo, bonus_base_efetivo = get_effective_benchmark(cadeia, current_user.cooperativa, current_user)

        meta_ca = batch.ca_coop_ref if batch.ca_coop_ref > 0 else meta_ca_efetivo
        meta_gpd = batch.gpd_coop_ref if batch.gpd_coop_ref > 0 else meta_gpd_efetivo
        bonus_base = bonus_base_efetivo

        batch.ca_ajustada = batch.ca # Default
        batch.iep = 0.0
        batch.indice_lote = 0.0
        batch.peso_vivo_medio = 0.0
        batch.peso_carcaca_medio = 0.0
        batch.rendimento_carcaca_pct = 0.0
        batch.carne_magra_pct = 0.0
        bonus_tipificacao = 0.0

        if cadeia == "avicultura":
            batch.peso_meta_coop = float(request.form.get("peso_meta_coop", 0) or 0)
            batch.idade_meta_coop = int(request.form.get("idade_meta_coop", 0) or 0)
            batch.fator_peso_caa = float(request.form.get("fator_peso_caa", 0.30) or 0.30)
            batch.fator_idade_caa = float(request.form.get("fator_idade_caa", 0.01) or 0.01)

            if batch.peso_meta_coop > 0 and batch.idade_meta_coop > 0:
                batch.ca_ajustada = calc_ca_ajustada_avicultura(
                    ca_observada=batch.ca,
                    peso_real=batch.peso_final,
                    idade_real_dias=batch.dias_alojamento, # Usando dias_alojamento float
                    peso_meta=batch.peso_meta_coop,
                    idade_meta_dias=batch.idade_meta_coop,
                    fator_peso=batch.fator_peso_caa,
                    fator_idade=batch.fator_idade_caa
                )
            batch.iep = calc_iep_avicultura(batch.viabilidade_pct, batch.peso_final, batch.dias_alojamento, batch.ca_ajustada)

        if cadeia == "suinocultura":
            batch.peso_vivo_medio = float(request.form.get("peso_vivo_medio", 0) or 0)
            batch.peso_carcaca_medio = float(request.form.get("peso_carcaca_medio", 0) or 0)
            batch.carne_magra_pct = float(request.form.get("carne_magra_pct", 0) or 0)

            batch.rendimento_carcaca_pct = calc_rendimento_carcaca(batch.peso_vivo_medio, batch.peso_carcaca_medio)

            if batch.peso_vivo_medio > 0:
                ajuste_peso = 0.003 * (batch.peso_vivo_medio - 120.0)
                batch.ca_ajustada = round(max(batch.ca + ajuste_peso, 0.01), 4)

            batch.indice_lote = calc_indice_lote_suino(batch.gpd, batch.viabilidade_pct, batch.ca_ajustada)
            bonus_tipificacao = calc_bonus_tipificacao(batch.carne_magra_pct, batch.rendimento_carcaca_pct)

        bon = calc_bonificacao(batch.gpd, batch.ca_ajustada, meta_gpd, meta_ca, bonus_base)
        batch.bonificacao = round(bon + bonus_tipificacao, 2)
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
        <label>Peso inicial médio por animal (kg)</label>
        <input type="number" step="0.0001" name="peso_inicial" value="{{ batch.peso_inicial }}" required>
        <label>Peso final médio por animal (kg)</label>
        <input type="number" step="0.0001" name="peso_final" value="{{ batch.peso_final }}" required>
        <label>Data e Hora de Alojamento</label>
        <input type="datetime-local" name="alojamento_datetime" value="{{ batch.alojamento_datetime.strftime('%Y-%m-%dT%H:%M') }}" required>
        <label>Data e Hora de Carregamento</label>
        <input type="datetime-local" name="carregamento_datetime" value="{{ batch.carregamento_datetime.strftime('%Y-%m-%dT%H:%M') }}" required>
        <label>Ração TOTAL consumida pelo lote (kg)</label>
        <input type="number" step="0.0001" name="racao_total_kg" value="{{ batch.racao_total_kg }}" required>
        <label>Animais iniciais</label>
        <input type="number" name="animais_iniciais" value="{{ batch.animais_iniciais }}" required>
        <label>Animais finais (abatidos/vendidos)</label>
        <input type="number" name="animais_final" value="{{ batch.animais_final }}" required>

        <h4>Minhas referências para este lote (opcional)</h4>
        <p class="muted">Se preenchido, estes valores sobrescrevem seus benchmarks pessoais e os da cooperativa para este lote.</p>
        <label>GPD médio (opcional)</label>
        <input type="number" step="0.0001" name="gpd_coop_ref" value="{{ batch.gpd_coop_ref }}" placeholder="GPD médio (opcional)">
        <label>CA média (opcional)</label>
        <input type="number" step="0.0001" name="ca_coop_ref" value="{{ batch.ca_coop_ref }}" placeholder="CA média (opcional)">

        {% if cadeia == 'avicultura' %}
          <h4>CA Ajustada (Avicultura)</h4>
          <label>Peso meta coop (kg)</label>
          <input type="number" step="0.0001" name="peso_meta_coop" value="{{ batch.peso_meta_coop }}" required>
          <label>Idade meta coop (dias)</label>
          <input type="number" name="idade_meta_coop" value="{{ batch.idade_meta_coop }}" required>
          <label>Fator peso CAA</label>
          <input type="number" step="0.0001" name="fator_peso_caa" value="{{ batch.fator_peso_caa }}" placeholder="Fator peso CAA">
          <label>Fator idade CAA</label>
          <input type="number" step="0.0001" name="fator_idade_caa" value="{{ batch.fator_idade_caa }}" placeholder="Fator idade CAA">
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
# LIVE LOT TRACKING (Acompanhamento de Lotes Ao Vivo)
# =========================================================
@app.route("/live_lot", methods=["GET", "POST"])
@login_required
def live_lot_tracking():
    if request.method == "POST":
        form_type = request.form.get("form_type")

        if form_type == "novo_live_batch":
            cadeia = request.form.get("cadeia", "").strip()
            estrutura = request.form.get("estrutura", "").strip()
            lote = request.form.get("lote", "").strip()
            alojamento_dt_str = request.form.get("alojamento_datetime", "")
            animais_iniciais = int(request.form.get("animais_iniciais", 0) or 0)
            peso_inicial_medio = float(request.form.get("peso_inicial_medio", 0) or 0)

            if not cadeia or not estrutura or not lote or not alojamento_dt_str or animais_iniciais <= 0 or peso_inicial_medio <= 0:
                flash("Preencha todos os campos obrigatórios para o lote ao vivo.")
                return redirect(url_for("live_lot_tracking"))

            try:
                alojamento_datetime = datetime.strptime(alojamento_dt_str, "%Y-%m-%dT%H:%M")
            except ValueError:
                flash("Formato de data e hora de alojamento inválido. Use YYYY-MM-DDTHH:MM.")
                return redirect(url_for("live_lot_tracking"))

            # Verifica unicidade do lote por usuário
            if LiveBatch.query.filter_by(user_id=current_user.id, cadeia=cadeia, estrutura=estrutura, lote=lote).first():
                flash(f"Já existe um lote ao vivo '{estrutura}/{lote}' para esta cadeia.")
                return redirect(url_for("live_lot_tracking"))

            live_batch = LiveBatch(
                user_id=current_user.id,
                cadeia=cadeia,
                estrutura=estrutura,
                lote=lote,
                alojamento_datetime=alojamento_datetime,
                animais_iniciais=animais_iniciais,
                peso_inicial_medio=peso_inicial_medio,
                status="ativo"
            )
            db.session.add(live_batch)
            db.session.commit()
            flash(f"Lote ao vivo '{estrutura}/{lote}' iniciado com sucesso!")
            return redirect(url_for("live_lot_tracking", live_batch_id=live_batch.id))

        elif form_type == "nova_pesagem":
            live_batch_id = int(request.form.get("live_batch_id"))
            live_batch = LiveBatch.query.filter_by(id=live_batch_id, user_id=current_user.id).first_or_404()
            data_pesagem_str = request.form.get("data_pesagem", "")
            peso_medio_animal = float(request.form.get("peso_medio_animal", 0) or 0)
            animais_pesados = int(request.form.get("animais_pesados", 0) or 0)
            observacoes = request.form.get("observacoes", "").strip()

            if not data_pesagem_str or peso_medio_animal <= 0 or animais_pesados <= 0:
                flash("Preencha todos os campos obrigatórios para a pesagem.")
                return redirect(url_for("live_lot_tracking", live_batch_id=live_batch.id))

            try:
                data_pesagem = datetime.strptime(data_pesagem_str, "%Y-%m-%dT%H:%M")
            except ValueError:
                flash("Formato de data e hora de pesagem inválido. Use YYYY-MM-DDTHH:MM.")
                return redirect(url_for("live_lot_tracking", live_batch_id=live_batch.id))

            weighing = LiveWeighing(
                live_batch_id=live_batch.id,
                data_pesagem=data_pesagem,
                peso_medio_animal=peso_medio_animal,
                animais_pesados=animais_pesados,
                observacoes=observacoes
            )
            db.session.add(weighing)
            db.session.commit()
            flash("Pesagem registrada.")
            return redirect(url_for("live_lot_tracking", live_batch_id=live_batch.id))

        elif form_type == "novo_recebimento_racao":
            live_batch_id = int(request.form.get("live_batch_id"))
            live_batch = LiveBatch.query.filter_by(id=live_batch_id, user_id=current_user.id).first_or_404()
            data_registro_str = request.form.get("data_registro", "")
            tipo_racao = request.form.get("tipo_racao", "").strip()
            quantidade_kg = float(request.form.get("quantidade_kg", 0) or 0)
            custo_total_rs = float(request.form.get("custo_total_rs", 0) or 0)

            if not data_registro_str or not tipo_racao or quantidade_kg <= 0:
                flash("Preencha todos os campos obrigatórios para o recebimento de ração.")
                return redirect(url_for("live_lot_tracking", live_batch_id=live_batch.id))

            try:
                data_registro = datetime.strptime(data_registro_str, "%Y-%m-%dT%H:%M")
            except ValueError:
                flash("Formato de data e hora de registro inválido. Use YYYY-MM-DDTHH:MM.")
                return redirect(url_for("live_lot_tracking", live_batch_id=live_batch.id))

            feed_entry = LiveFeed(
                live_batch_id=live_batch.id,
                data_registro=data_registro,
                tipo_racao=tipo_racao,
                quantidade_kg=quantidade_kg,
                custo_total_rs=custo_total_rs
            )
            db.session.add(feed_entry)
            db.session.commit()
            flash("Recebimento de ração registrado.")
            return redirect(url_for("live_lot_tracking", live_batch_id=live_batch.id))

        elif form_type == "finalizar_live_batch":
            live_batch_id = int(request.form.get("live_batch_id"))
            live_batch = LiveBatch.query.filter_by(id=live_batch_id, user_id=current_user.id).first_or_404()
            live_batch.status = "finalizado"
            db.session.commit()
            flash(f"Lote ao vivo '{live_batch.estrutura}/{live_batch.lote}' finalizado.")
            return redirect(url_for("live_lot_tracking"))

    live_batches = LiveBatch.query.filter_by(user_id=current_user.id).order_by(LiveBatch.criado_em.desc()).all()
    selected_live_batch_id = request.args.get("live_batch_id", type=int)

    live_batch_detail = None
    weighings = []
    feeds = []
    gpd_chart_data = None
    ca_chart_data = None
    feed_intake_chart_data = None

    if selected_live_batch_id:
        live_batch_detail = LiveBatch.query.filter_by(id=selected_live_batch_id, user_id=current_user.id).first()
        if live_batch_detail:
            weighings = LiveWeighing.query.filter_by(live_batch_id=live_batch_detail.id).order_by(LiveWeighing.data_pesagem.asc()).all()
            feeds = LiveFeed.query.filter_by(live_batch_id=live_batch_detail.id).order_by(LiveFeed.data_registro.asc()).all()

            # Calcular GPD e CA para o gráfico
            gpd_labels = []
            gpd_data = []
            ca_labels = []
            ca_data = []

            if weighings:
                # Adiciona o peso inicial como primeiro ponto
                gpd_labels.append(live_batch_detail.alojamento_datetime.strftime("%d/%m %H:%M"))
                gpd_data.append(live_batch_detail.peso_inicial_medio)

                for i in range(len(weighings)):
                    current_weighing = weighings[i]

                    # Para GPD, comparamos com a pesagem anterior ou peso inicial
                    prev_weight = live_batch_detail.peso_inicial_medio
                    prev_datetime = live_batch_detail.alojamento_datetime
                    if i > 0:
                        prev_weighing = weighings[i-1]
                        prev_weight = prev_weighing.peso_medio_animal
                        prev_datetime = prev_weighing.data_pesagem

                    dias_desde_anterior = calc_dias_alojamento(prev_datetime, current_weighing.data_pesagem)
                    current_gpd = calc_gpd(prev_weight, current_weighing.peso_medio_animal, dias_desde_anterior)

                    gpd_labels.append(current_weighing.data_pesagem.strftime("%d/%m %H:%M"))
                    gpd_data.append(current_gpd) # Aqui estamos plotando o GPD calculado, não o peso.
                                                 # Se quisermos o peso, seria current_weighing.peso_medio_animal

                # Para CA, precisamos acumular ração e ganho de peso
                total_racao_acumulada = 0.0
                total_ganho_peso_acumulado = 0.0
                last_peso_medio = live_batch_detail.peso_inicial_medio

                for w in weighings:
                    # Ração consumida desde a última pesagem ou alojamento
                    racao_periodo = sum(f.quantidade_kg for f in feeds if live_batch_detail.alojamento_datetime <= f.data_registro <= w.data_pesagem)

                    ganho_peso_periodo = (w.peso_medio_animal - last_peso_medio) * live_batch_detail.animais_iniciais # Simplificado, idealmente seria animais_vivos_no_periodo

                    if ganho_peso_periodo > 0:
                        current_ca = racao_periodo / ganho_peso_periodo
                        ca_labels.append(w.data_pesagem.strftime("%d/%m %H:%M"))
                        ca_data.append(current_ca)

                    last_peso_medio = w.peso_medio_animal
                    total_racao_acumulada += racao_periodo # Isso não está correto para acumular, precisa ser mais granular
                    total_ganho_peso_acumulado += ganho_peso_periodo # Isso também não está correto para acumular

            if gpd_labels and gpd_data:
                gpd_chart_data = {"labels": gpd_labels, "data": gpd_data}
            if ca_labels and ca_data:
                ca_chart_data = {"labels": ca_labels, "data": ca_data}

            # Gráfico de consumo de ração
            if feeds:
                feed_labels = [f.data_registro.strftime("%d/%m %H:%M") for f in feeds]
                feed_quantities = [f.quantidade_kg for f in feeds]
                feed_intake_chart_data = {"labels": feed_labels, "data": feed_quantities}


    html_content = """
    <h2>Acompanhamento de Lotes Ao Vivo</h2>

    <div class="grid">
      <div class="card">
        <h3>Iniciar Novo Lote Ao Vivo</h3>
        <form method="post">
          <input type="hidden" name="form_type" value="novo_live_batch">
          <select name="cadeia" required>
            <option value="">Selecione a Cadeia</option>
            <option value="avicultura">Avicultura</option>
            <option value="suinocultura">Suinocultura</option>
          </select>
          <input name="estrutura" placeholder="Número da estrutura" required>
          <input name="lote" placeholder="Número do lote" required>
          <label>Data e Hora de Alojamento</label>
          <input type="datetime-local" name="alojamento_datetime" required>
          <input type="number" name="animais_iniciais" placeholder="Animais iniciais" required>
          <input type="number" step="0.0001" name="peso_inicial_medio" placeholder="Peso inicial médio por animal (kg)" required>
          <button class="btn btn-ok" type="submit">Iniciar Lote</button>
        </form>
      </div>

      <div class="card">
        <h3>Meus Lotes Ao Vivo</h3>
        {% if live_batches %}
          <table>
            <tr><th>Cadeia</th><th>Estrutura</th><th>Lote</th><th>Alojamento</th><th>Status</th><th>Ações</th></tr>
            {% for lb in live_batches %}
              <tr>
                <td>{{ lb.cadeia|capitalize }}</td>
                <td>{{ lb.estrutura }}</td>
                <td>{{ lb.lote }}</td>
                <td>{{ lb.alojamento_datetime.strftime("%d/%m %H:%M") }}</td>
                <td>{{ lb.status|capitalize }}</td>
                <td>
                  <a class="btn btn-ghost" href="{{ url_for('live_lot_tracking', live_batch_id=lb.id) }}">Detalhes</a>
                  {% if lb.status == 'ativo' %}
                    <form method="post" action="{{ url_for('live_lot_tracking') }}" style="display:inline;">
                      <input type="hidden" name="form_type" value="finalizar_live_batch">
                      <input type="hidden" name="live_batch_id" value="{{ lb.id }}">
                      <button type="submit" class="btn btn-ghost" onclick="return confirm('Tem certeza que deseja FINALIZAR este lote?');">Finalizar</button>
                    </form>
                  {% endif %}
                  <form method="post" action="{{ url_for('excluir_live_batch', live_batch_id=lb.id) }}" style="display:inline;">
                    <button type="submit" class="btn btn-ghost" onclick="return confirm('Tem certeza que deseja EXCLUIR este lote e todos os seus registros?');">Excluir</button>
                  </form>
                </td>
              </tr>
            {% endfor %}
          </table>
        {% else %}
          <p class="muted">Nenhum lote ao vivo registrado ainda.</p>
        {% endif %}
      </div>
    </div>

    {% if live_batch_detail %}
    <div class="card">
      <h3>Detalhes do Lote: {{ live_batch_detail.estrutura }}/{{ live_batch_detail.lote }} ({{ live_batch_detail.cadeia|capitalize }})</h3>
      <p class="muted">Alojamento: <b>{{ live_batch_detail.alojamento_datetime.strftime("%d/%m/%Y %H:%M") }}</b> | Animais Iniciais: <b>{{ live_batch_detail.animais_iniciais }}</b> | Peso Inicial Médio: <b>{{ live_batch_detail.peso_inicial_medio }} kg</b> | Status: <b>{{ live_batch_detail.status|capitalize }}</b></p>
    </div>

    <div class="grid">
      <div class="card">
        <h3>Registrar Pesagem Semanal</h3>
        <form method="post">
          <input type="hidden" name="form_type" value="nova_pesagem">
          <input type="hidden" name="live_batch_id" value="{{ live_batch_detail.id }}">
          <label>Data e Hora da Pesagem</label>
          <input type="datetime-local" name="data_pesagem" required>
          <input type="number" step="0.01" name="peso_medio_animal" placeholder="Peso médio por animal (kg)" required>
          <input type="number" name="animais_pesados" placeholder="Número de animais pesados" required>
          <textarea name="observacoes" placeholder="Observações (opcional)"></textarea>
          <button class="btn btn-pri" type="submit">Adicionar Pesagem</button>
        </form>
        {% if gpd_chart_data %}
        <h4 style="margin-top: 20px;">GPD (Ganho de Peso Diário)</h4>
        <canvas id="gpdChart" height="90"></canvas>
        <script>
          const gpdChart = {{ gpd_chart_data | tojson }};
          new Chart(document.getElementById("gpdChart"), {
            type: "line",
            data: {
              labels: gpdChart.labels,
              datasets: [{
                label: "GPD (kg/dia)",
                data: gpdChart.data,
                borderColor: 'rgb(75, 192, 192)',
                tension: 0.1,
                fill: false
              }]
            },
            options: { responsive: true, scales: { y: { beginAtZero: true } } }
          });
        </script>
        {% endif %}
      </div>

      <div class="card">
        <h3>Registrar Recebimento de Ração</h3>
        <form method="post">
          <input type="hidden" name="form_type" value="novo_recebimento_racao">
          <input type="hidden" name="live_batch_id" value="{{ live_batch_detail.id }}">
          <label>Data e Hora do Recebimento</label>
          <input type="datetime-local" name="data_registro" required>
          <input name="tipo_racao" placeholder="Tipo de Ração (ex: Inicial, Crescimento)" required>
          <input type="number" step="0.01" name="quantidade_kg" placeholder="Quantidade (kg)" required>
          <input type="number" step="0.01" name="custo_total_rs" placeholder="Custo Total (R$)" value="0">
          <button class="btn btn-pri" type="submit">Adicionar Ração</button>
        </form>
        {% if feed_intake_chart_data %}
        <h4 style="margin-top: 20px;">Consumo de Ração (kg)</h4>
        <canvas id="feedIntakeChart" height="90"></canvas>
        <script>
          const feedIntakeChart = {{ feed_intake_chart_data | tojson }};
          new Chart(document.getElementById("feedIntakeChart"), {
            type: "bar",
            data: {
              labels: feedIntakeChart.labels,
              datasets: [{
                label: "Ração (kg)",
                data: feedIntakeChart.data,
                backgroundColor: 'rgba(153, 102, 255, 0.6)'
              }]
            },
            options: { responsive: true, scales: { y: { beginAtZero: true } } }
          });
        </script>
        {% endif %}
      </div>
    </div>

    <div class="card">
      <h3>Histórico de Pesagens</h3>
      {% if weighings %}
      <table>
        <tr><th>Data Pesagem</th><th>Peso Médio (kg)</th><th>Animais Pesados</th><th>Observações</th><th>Ações</th></tr>
        {% for w in weighings %}
        <tr>
          <td>{{ w.data_pesagem.strftime("%d/%m/%Y %H:%M") }}</td>
          <td>{{ w.peso_medio_animal }}</td>
          <td>{{ w.animais_pesados }}</td>
          <td>{{ w.observacoes or '-' }}</td>
          <td>
            <form method="post" action="{{ url_for('excluir_live_weighing', weighing_id=w.id) }}" style="display:inline;">
              <button type="submit" class="btn btn-ghost" onclick="return confirm('Tem certeza que deseja excluir esta pesagem?');">Excluir</button>
            </form>
          </td>
        </tr>
        {% endfor %}
      </table>
      {% else %}
      <p class="muted">Nenhuma pesagem registrada para este lote.</p>
      {% endif %}
    </div>

    <div class="card">
      <h3>Histórico de Recebimento de Ração</h3>
      {% if feeds %}
      <table>
        <tr><th>Data Registro</th><th>Tipo Ração</th><th>Quantidade (kg)</th><th>Custo (R$)</th><th>Ações</th></tr>
        {% for f in feeds %}
        <tr>
          <td>{{ f.data_registro.strftime("%d/%m/%Y %H:%M") }}</td>
          <td>{{ f.tipo_racao }}</td>
          <td>{{ f.quantidade_kg }}</td>
          <td>R$ {{ f.custo_total_rs }}</td>
          <td>
            <form method="post" action="{{ url_for('excluir_live_feed', feed_id=f.id) }}" style="display:inline;">
              <button type="submit" class="btn btn-ghost" onclick="return confirm('Tem certeza que deseja excluir este registro de ração?');">Excluir</button>
            </form>
          </td>
        </tr>
        {% endfor %}
      </table>
      {% else %}
      <p class="muted">Nenhum recebimento de ração registrado para este lote.</p>
      {% endif %}
    </div>
    {% endif %}
    """
    return page(html_content, title="AP360 | Lotes Ao Vivo", live_batches=live_batches,
                live_batch_detail=live_batch_detail, weighings=weighings, feeds=feeds,
                gpd_chart_data=gpd_chart_data, ca_chart_data=ca_chart_data, feed_intake_chart_data=feed_intake_chart_data)


@app.route("/live_lot/excluir/<int:live_batch_id>", methods=["POST"])
@login_required
def excluir_live_batch(live_batch_id):
    live_batch = LiveBatch.query.filter_by(id=live_batch_id, user_id=current_user.id).first_or_404()
    LiveWeighing.query.filter_by(live_batch_id=live_batch.id).delete()
    LiveFeed.query.filter_by(live_batch_id=live_batch.id).delete()
    db.session.delete(live_batch)
    db.session.commit()
    flash("Lote ao vivo e todos os seus registros excluídos com sucesso!")
    return redirect(url_for("live_lot_tracking"))


@app.route("/live_lot/weighing/excluir/<int:weighing_id>", methods=["POST"])
@login_required
def excluir_live_weighing(weighing_id):
    weighing = LiveWeighing.query.get_or_404(weighing_id)
    live_batch_id = weighing.live_batch_id
    live_batch = LiveBatch.query.filter_by(id=live_batch_id, user_id=current_user.id).first_or_404()

    db.session.delete(weighing)
    db.session.commit()
    flash("Pesagem excluída com sucesso!")
    return redirect(url_for("live_lot_tracking", live_batch_id=live_batch_id))


@app.route("/live_lot/feed/excluir/<int:feed_id>", methods=["POST"])
@login_required
def excluir_live_feed(feed_id):
    feed = LiveFeed.query.get_or_404(feed_id)
    live_batch_id = feed.live_batch_id
    live_batch = LiveBatch.query.filter_by(id=live_batch_id, user_id=current_user.id).first_or_404()

    db.session.delete(feed)
    db.session.commit()
    flash("Recebimento de ração excluído com sucesso!")
    return redirect(url_for("live_lot_tracking", live_batch_id=live_batch_id))


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

    html_content = """
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
      <p class="muted">Origem: {{ animal.origem or "-" }} | Lote: {{ animal.lote or "-" }} | Status: {{ animal.status|capitalize }}</p>
      <p class="muted">Peso atual: <b>{{ animal.peso_atual }} kg</b> | Última pesagem: <b>{{ animal.ultima_pesagem or "-" }}</b></p>
      <p class="muted">Observações: {{ animal.observacoes or "-" }}</p>
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
            options: { responsive: true, scales: { y: { beginAtZero: true } } }
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
            <option value="medicacao">Medicação</option>
            <option value="diagnostico">Diagnóstico</option>
            <option value="outros">Outros</option>
          </select>
          <label>Data</label><input type="date" name="data" required>
          <textarea name="descricao" placeholder="Descrição do evento" required></textarea>
          <button class="btn btn-ok" type="submit">Salvar evento</button>
        </form>
      </div>
    </div>

    <div class="card">
      <h3>Histórico de pesagens</h3>
      {% if pesos %}
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
      {% else %}
      <p class="muted">Nenhum registro de pesagem para este animal.</p>
      {% endif %}
    </div>

    <div class="card">
      <h3>Histórico de eventos</h3>
      {% if eventos %}
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
      {% else %}
      <p class="muted">Nenhum registro de evento para este animal.</p>
      {% endif %}
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
# IA LOCAL
# =========================================================
def ia_local(msg: str):
    txt = (msg or "").strip()
    if not txt:
        return "Digite sua pergunta."
    dicas = [
        "Monitore GPD e CAA semanalmente para agir antes da perda de margem.",
        "Padronize coleta por estrutura/lote para comparação justa.",
        "Na agricultura, compare sempre margem líquida por tonelada.",
        "Acompanhe o custo por kg de ração para otimizar a alimentação.",
        "Analise o histórico de produtividade dos talhões para planejar a próxima safra.",
        "O registro de eventos em bovinos ajuda a identificar padrões de saúde e manejo."
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