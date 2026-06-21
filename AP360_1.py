import os
import io
import csv
import secrets
from datetime import datetime, timedelta
from functools import wraps
import pytz # Para lidar com fusos horários

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
    .whatsapp-link {
        display: inline-block;
        background-color: #25D366; /* Cor do WhatsApp */
        color: white;
        padding: 8px 12px;
        border-radius: 8px;
        text-decoration: none;
        font-weight: bold;
        margin-top: 10px;
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
      <h1 style="margin:0;font-size:2.15rem">AP360: Sua Plataforma Completa de Gestão Agropecuária</h1>
      <p class="muted">
        O AP360 oferece ferramentas intuitivas para monitorar, analisar e otimizar suas produções agrícolas e pecuárias.
        Tome decisões mais inteligentes com dados precisos, estatísticas simplificadas e acompanhamento em tempo real.
        Com gráficos interativos e projeções, você terá o controle total para facilitar suas tomadas de decisão e
        impulsionar a produtividade.
      </p>
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
        flash("Para agilizar, entre em contato via WhatsApp:")
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
      <p class="muted" style="margin-top:15px;">
        Após o envio, aguarde a liberação do acesso. Para agilizar o processo,
        entre em contato conosco via WhatsApp:
        <a href="https://wa.me/5545999999999?text=Ol%C3%A1%2C%20enviei%20uma%20solicita%C3%A7%C3%A3o%20de%20acesso%20ao%20AP360%20e%20gostaria%20de%20agilizar%20a%20libera%C3%A7%C3%A3o."
           target="_blank" class="whatsapp-link">
           <img src="https://upload.wikimedia.org/wikipedia/commons/6/6b/WhatsApp.svg" alt="WhatsApp" style="height:16px;vertical-align:middle;margin-right:5px;">
           Fale Conosco
        </a>
      </p>
    </div>
    """
    return page(html_content, title="AP360 | Inscrição")


@app.route("/ativar/<token>", methods=["GET", "POST"])
def activate_account(token):
    invite = AccessInvite.query.filter_by(token=token, status="convidado").first()
    if not invite:
        flash("Token de ativação inválido ou expirado.")
        return redirect(url_for("login"))

    if request.method == "POST":
        senha = request.form.get("senha", "")
        confirma_senha = request.form.get("confirma_senha", "")

        if not senha or senha != confirma_senha:
            flash("As senhas não conferem ou estão vazias.")
            return redirect(url_for("activate_account", token=token))

        # Busca a solicitação de acesso original, se houver
        access_request = AccessRequest.query.get(invite.request_id) if invite.request_id else None

        # Cria o novo usuário
        new_user = User(
            email=invite.email,
            nome=access_request.nome if access_request else "Usuário Ativado",
            cpf=access_request.cpf if access_request else None,
            telefone=access_request.telefone if access_request else None,
            segmento=access_request.segmento if access_request else "agricultura",
            cooperativa=access_request.cooperativa if access_request else None,
            status="ativo"
        )
        new_user.set_password(senha)
        db.session.add(new_user)

        # Atualiza o convite
        invite.status = "ativado"
        invite.ativado_em = datetime.utcnow()

        # Atualiza a solicitação de acesso (se existir)
        if access_request:
            access_request.status = "liberado"

        db.session.commit()
        flash("Sua conta foi ativada com sucesso! Faça login para começar.")
        return redirect(url_for("login"))

    html_content = """
    <div class="card" style="max-width:520px;margin:20px auto">
      <h2 style="margin-top:0">Ativar Conta</h2>
      <p class="muted">Defina sua senha para o e-mail: <b>{{ invite.email }}</b></p>
      <form method="post">
        <input type="password" name="senha" placeholder="Nova senha" required>
        <input type="password" name="confirma_senha" placeholder="Confirme a senha" required>
        <button class="btn btn-ok" type="submit">Ativar</button>
      </form>
    </div>
    """
    return page(html_content, title="AP360 | Ativar Conta", invite=invite)


@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("Você foi desconectado.")
    return redirect(url_for("index"))


# =========================================================
# DASHBOARD
# =========================================================
@app.route("/dashboard")
@login_required
def dashboard():
    html_content = """
    <div class="hero">
      <h1 style="margin:0">Bem-vindo(a), <span class="welcome-name">{{ current_user.nome.split(' ')[0] }}</span>!</h1>
      <p class="muted">Seu painel de controle completo para gestão agropecuária.</p>
    </div>

    <div class="grid">
      <div class="card">
        <h3>Últimos Lotes (Avicultura/Suinocultura)</h3>
        {% if current_user.segmento in ['avicultura', 'suinocultura'] %}
          <table>
            <tr><th>Estrutura</th><th>Lote</th><th>GPD</th><th>CA</th><th>Bonificação</th></tr>
            {% for batch in Batch.query.filter_by(user_id=current_user.id).order_by(Batch.criado_em.desc()).limit(5).all() %}
              <tr>
                <td>{{ batch.estrutura }}</td>
                <td>{{ batch.lote }}</td>
                <td>{{ batch.gpd }}</td>
                <td>{{ batch.ca }}</td>
                <td>R$ {{ batch.bonificacao }}</td>
              </tr>
            {% else %}
              <tr><td colspan="5">Nenhum lote registrado ainda.</td></tr>
            {% endfor %}
          </table>
        {% else %}
          <p class="muted">Seu segmento não inclui avicultura ou suinocultura.</p>
        {% endif %}
      </div>

      <div class="card">
        <h3>Últimas Cotações (Agricultura)</h3>
        {% if current_user.segmento == 'agricultura' %}
          <table>
            <tr><th>Produto</th><th>Quantidade</th><th>Líquido R$/Ton</th><th>Total R$</th></tr>
            {% for quote in AgricultureQuote.query.filter_by(user_id=current_user.id).order_by(AgricultureQuote.criado_em.desc()).limit(5).all() %}
              <tr>
                <td>{{ quote.produto|capitalize }}</td>
                <td>{{ quote.quantidade_ton }} ton</td>
                <td>R$ {{ quote.liquido_rs_ton }}</td>
                <td>R$ {{ quote.total_rs }}</td>
              </tr>
            {% else %}
              <tr><td colspan="4">Nenhuma cotação registrada ainda.</td></tr>
            {% endfor %}
          </table>
        {% else %}
          <p class="muted">Seu segmento não inclui agricultura.</p>
        {% endif %}
      </div>
    </div>

    <div class="card">
      <h3>Seus Benchmarks Pessoais</h3>
      <form method="post" action="{{ url_for('update_user_benchmarks') }}">
        <h4>Avicultura</h4>
        <label>GPD Avicultura</label>
        <input type="number" step="0.0001" name="user_avicultura_gpd" value="{{ current_user.user_avicultura_gpd }}" placeholder="Ex: 0.065">
        <label>CA Avicultura</label>
        <input type="number" step="0.0001" name="user_avicultura_ca" value="{{ current_user.user_avicultura_ca }}" placeholder="Ex: 1.70">
        <label>Bônus Base Avicultura</label>
        <input type="number" step="0.01" name="user_avicultura_bonus_base" value="{{ current_user.user_avicultura_bonus_base }}" placeholder="Ex: 1000.00">

        <h4>Suinocultura</h4>
        <label>GPD Suinocultura</label>
        <input type="number" step="0.0001" name="user_suinocultura_gpd" value="{{ current_user.user_suinocultura_gpd }}" placeholder="Ex: 0.72">
        <label>CA Suinocultura</label>
        <input type="number" step="0.0001" name="user_suinocultura_ca" value="{{ current_user.user_suinocultura_ca }}" placeholder="Ex: 2.45">
        <label>Bônus Base Suinocultura</label>
        <input type="number" step="0.01" name="user_suinocultura_bonus_base" value="{{ current_user.user_suinocultura_bonus_base }}" placeholder="Ex: 1200.00">

        <button class="btn btn-ok" type="submit">Salvar Benchmarks</button>
      </form>
    </div>
    """
    return page(html_content, title="AP360 | Dashboard")


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
        flash("Erro: Verifique se os valores inseridos para os benchmarks são números válidos.")
    return redirect(url_for("dashboard"))


# =========================================================
# ADMIN PANEL
# =========================================================
@app.route("/admin")
@admin_required
def admin_panel():
    access_requests = AccessRequest.query.order_by(AccessRequest.criado_em.desc()).all()
    users = User.query.order_by(User.criado_em.desc()).all()
    invites = AccessInvite.query.order_by(AccessInvite.criado_em.desc()).all()
    coop_benchmarks = CoopBenchmark.query.order_by(CoopBenchmark.cadeia, CoopBenchmark.cooperativa).all()

    html_content = """
    <h2>Painel Administrativo</h2>

    <div class="card">
      <h3>Solicitações de Acesso</h3>
      <table>
        <tr><th>Data</th><th>Nome</th><th>Email</th><th>Segmento</th><th>Status</th><th>Ações</th></tr>
        {% for req in access_requests %}
          <tr>
            <td>{{ req.criado_em.strftime("%d/%m %H:%M") }}</td>
            <td>{{ req.nome }}</td>
            <td>{{ req.email }}</td>
            <td>{{ req.segmento|capitalize }}</td>
            <td>{{ req.status|capitalize }}</td>
            <td>
              {% if req.status == "pendente" %}
                <form method="post" action="{{ url_for('approve_access_request', request_id=req.id) }}" style="display:inline;">
                  <button type="submit" class="btn btn-ok">Aprovar</button>
                </form>
                <form method="post" action="{{ url_for('deny_access_request', request_id=req.id) }}" style="display:inline;">
                  <button type="submit" class="btn btn-danger" onclick="return confirm('Tem certeza que deseja negar esta solicitação?');">Negar</button>
                </form>
              {% elif req.status == "negado" %}
                <span class="muted">Negado</span>
                <form method="post" action="{{ url_for('delete_access_request', request_id=req.id) }}" style="display:inline;">
                  <button type="submit" class="btn btn-ghost" onclick="return confirm('Tem certeza que deseja remover esta solicitação negada?');">Remover</button>
                </form>
              {% else %}
                <span class="muted">{{ req.status|capitalize }}</span>
              {% endif %}
            </td>
          </tr>
        {% endfor %}
      </table>
    </div>

    <div class="card">
      <h3>Convites Pendentes</h3>
      <table>
        <tr><th>Data</th><th>Email</th><th>Status</th><th>Token</th><th>Ações</th></tr>
        {% for inv in invites %}
          <tr>
            <td>{{ inv.criado_em.strftime("%d/%m %H:%M") }}</td>
            <td>{{ inv.email }}</td>
            <td>{{ inv.status|capitalize }}</td>
            <td>{{ inv.token }}</td>
            <td>
              {% if inv.status == "convidado" %}
                <form method="post" action="{{ url_for('revoke_invite', invite_id=inv.id) }}" style="display:inline;">
                  <button type="submit" class="btn btn-danger" onclick="return confirm('Tem certeza que deseja revogar este convite?');">Revogar</button>
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
        <tr><th>Data</th><th>Nome</th><th>Email</th><th>Perfil</th><th>Status</th><th>Ações</th></tr>
        {% for u in users %}
          <tr>
            <td>{{ u.criado_em.strftime("%d/%m %H:%M") }}</td>
            <td>{{ u.nome }}</td>
            <td>{{ u.email }}</td>
            <td>{{ u.perfil|capitalize }}</td>
            <td>{{ u.status|capitalize }}</td>
            <td>
              {% if u.id != current_user.id %} {# Não permite bloquear a si mesmo #}
                {% if u.status == "ativo" %}
                  <form method="post" action="{{ url_for('block_user', user_id=u.id) }}" style="display:inline;">
                    <button type="submit" class="btn btn-danger" onclick="return confirm('Tem certeza que deseja bloquear este usuário?');">Bloquear</button>
                  </form>
                {% else %}
                  <form method="post" action="{{ url_for('unblock_user', user_id=u.id) }}" style="display:inline;">
                    <button type="submit" class="btn btn-ok">Desbloquear</button>
                  </form>
                {% endif %}
              {% else %}
                <span class="muted">Você</span>
              {% endif %}
            </td>
          </tr>
        {% endfor %}
      </table>
    </div>

    <div class="card">
      <h3>Benchmarks Gerais (Cooperativas)</h3>
      <form method="post" action="{{ url_for('add_coop_benchmark') }}">
        <h4>Adicionar/Atualizar Benchmark</h4>
        <select name="cadeia" required>
          <option value="avicultura">Avicultura</option>
          <option value="suinocultura">Suinocultura</option>
        </select>
        <input name="cooperativa" placeholder="Nome da Cooperativa" required>
        <input type="number" step="0.0001" name="media_gpd" placeholder="Média GPD" required>
        <input type="number" step="0.0001" name="media_ca" placeholder="Média CA" required>
        <input type="number" step="0.01" name="bonus_base" placeholder="Bônus Base" required>
        <button class="btn btn-ok" type="submit">Salvar Benchmark</button>
      </form>

      <table style="margin-top:20px;">
        <tr><th>Cadeia</th><th>Cooperativa</th><th>Média GPD</th><th>Média CA</th><th>Bônus Base</th><th>Ações</th></tr>
        {% for cb in coop_benchmarks %}
          <tr>
            <td>{{ cb.cadeia|capitalize }}</td>
            <td>{{ cb.cooperativa }}</td>
            <td>{{ cb.media_gpd }}</td>
            <td>{{ cb.media_ca }}</td>
            <td>R$ {{ cb.bonus_base }}</td>
            <td>
              <form method="post" action="{{ url_for('delete_coop_benchmark', benchmark_id=cb.id) }}" style="display:inline;">
                <button type="submit" class="btn btn-danger" onclick="return confirm('Tem certeza que deseja excluir este benchmark?');">Excluir</button>
              </form>
            </td>
          </tr>
        {% endfor %}
      </table>
    </div>
    """
    return page(html_content, title="AP360 | Admin", access_requests=access_requests, users=users, invites=invites, coop_benchmarks=coop_benchmarks)


@app.route("/admin/approve_request/<int:request_id>", methods=["POST"])
@admin_required
def approve_access_request(request_id):
    req = AccessRequest.query.get_or_404(request_id)
    if req.status == "pendente":
        # Verifica se já existe um usuário com este e-mail
        if User.query.filter_by(email=req.email).first():
            flash(f"Já existe um usuário cadastrado com o e-mail {req.email}. A solicitação não pode ser aprovada.")
            req.status = "negado" # Marca como negado para evitar reprocessamento
            db.session.commit()
            return redirect(url_for("admin_panel"))

        # Cria um token de ativação
        token = secrets.token_urlsafe(32)
        invite = AccessInvite(email=req.email, token=token, status="convidado", request_id=req.id)
        db.session.add(invite)

        req.status = "liberado" # Marca a solicitação como liberada
        db.session.commit()
        flash(f"Solicitação de {req.nome} aprovada. Convite enviado para {req.email}. Token: {token}")
    else:
        flash("Esta solicitação já foi processada.")
    return redirect(url_for("admin_panel"))


@app.route("/admin/deny_request/<int:request_id>", methods=["POST"])
@admin_required
def deny_access_request(request_id):
    req = AccessRequest.query.get_or_404(request_id)
    if req.status == "pendente" or req.status == "liberado": # Permite negar mesmo se já liberado
        req.status = "negado"
        # Se houver um convite associado, revoga-o
        invite = AccessInvite.query.filter_by(request_id=req.id, status="convidado").first()
        if invite:
            invite.status = "revogado"
        db.session.commit()
        flash(f"Solicitação de {req.nome} negada.")
    else:
        flash("Esta solicitação já foi negada ou removida.")
    return redirect(url_for("admin_panel"))


@app.route("/admin/delete_request/<int:request_id>", methods=["POST"])
@admin_required
def delete_access_request(request_id):
    req = AccessRequest.query.get_or_404(request_id)
    if req.status == "negado": # Só permite remover se estiver negado
        db.session.delete(req)
        db.session.commit()
        flash(f"Solicitação negada de {req.nome} removida.")
    else:
        flash("Apenas solicitações negadas podem ser removidas.")
    return redirect(url_for("admin_panel"))


@app.route("/admin/revoke_invite/<int:invite_id>", methods=["POST"])
@admin_required
def revoke_invite(invite_id):
    invite = AccessInvite.query.get_or_404(invite_id)
    if invite.status == "convidado":
        invite.status = "revogado"
        # Se houver uma solicitação de acesso associada, muda o status para pendente novamente
        if invite.request_id:
            req = AccessRequest.query.get(invite.request_id)
            if req and req.status == "liberado":
                req.status = "pendente"
        db.session.commit()
        flash(f"Convite para {invite.email} revogado.")
    else:
        flash("Este convite já foi ativado ou revogado.")
    return redirect(url_for("admin_panel"))


@app.route("/admin/block_user/<int:user_id>", methods=["POST"])
@admin_required
def block_user(user_id):
    user = User.query.get_or_404(user_id)
    if user.id == current_user.id:
        flash("Você não pode bloquear sua própria conta.")
    elif user.perfil == "admin":
        flash("Você não pode bloquear outro administrador.")
    else:
        user.status = "bloqueado"
        db.session.commit()
        flash(f"Usuário {user.nome} bloqueado.")
    return redirect(url_for("admin_panel"))


@app.route("/admin/unblock_user/<int:user_id>", methods=["POST"])
@admin_required
def unblock_user(user_id):
    user = User.query.get_or_404(user_id)
    user.status = "ativo"
    db.session.commit()
    flash(f"Usuário {user.nome} desbloqueado.")
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

        benchmark = CoopBenchmark.query.filter_by(cadeia=cadeia, cooperativa=cooperativa).first()
        if benchmark:
            benchmark.media_gpd = media_gpd
            benchmark.media_ca = media_ca
            benchmark.bonus_base = bonus_base
            benchmark.atualizado_em = datetime.utcnow()
            flash(f"Benchmark para {cooperativa} ({cadeia}) atualizado com sucesso!")
        else:
            new_benchmark = CoopBenchmark(
                cadeia=cadeia,
                cooperativa=cooperativa,
                media_gpd=media_gpd,
                media_ca=media_ca,
                bonus_base=bonus_base
            )
            db.session.add(new_benchmark)
            flash(f"Benchmark para {cooperativa} ({cadeia}) adicionado com sucesso!")
        db.session.commit()
    except ValueError:
        flash("Erro: Verifique se os valores de GPD, CA e Bônus Base são números válidos.")
    except Exception as e:
        flash(f"Ocorreu um erro: {e}")
    return redirect(url_for("admin_panel"))


@app.route("/admin/delete_coop_benchmark/<int:benchmark_id>", methods=["POST"])
@admin_required
def delete_coop_benchmark(benchmark_id):
    benchmark = CoopBenchmark.query.get_or_404(benchmark_id)
    db.session.delete(benchmark)
    db.session.commit()
    flash(f"Benchmark de {benchmark.cooperativa} ({benchmark.cadeia}) excluído com sucesso!")
    return redirect(url_for("admin_panel"))


# =========================================================
# AGRICULTURA
# =========================================================
@app.route("/agricultura", methods=["GET", "POST"])
@login_required
def agricultura():
    if current_user.segmento != "agricultura":
        flash("Seu perfil não tem acesso ao módulo de agricultura.")
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        form_type = request.form.get("form_type")

        if form_type == "nova_cotacao":
            try:
                produto = request.form.get("produto", "").strip()
                quantidade_ton = float(request.form.get("quantidade_ton", 0) or 0)
                origem = request.form.get("origem", "").strip()
                porto = request.form.get("porto", "").strip()

                export_rs_ton, cbot_usd_bushel = cbot_para_rs_ton(produto)
                usd_brl = fx_usd_brl()
                frete_rs_ton = frete_medio(origem, porto)
                liquido_rs_ton = round(export_rs_ton - frete_rs_ton, 2)
                total_rs = round(liquido_rs_ton * quantidade_ton, 2)

                quote = AgricultureQuote(
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
                db.session.add(quote)
                db.session.commit()
                flash("Cotação registrada com sucesso!")
            except ValueError:
                flash("Erro: Verifique se a quantidade é um número válido.")
            except Exception as e:
                flash(f"Ocorreu um erro ao registrar a cotação: {e}")
            return redirect(url_for("agricultura"))

        elif form_type == "novo_campo":
            try:
                nome_campo = request.form.get("nome_campo", "").strip()
                if AgricultureField.query.filter_by(user_id=current_user.id, nome_campo=nome_campo).first():
                    flash("Já existe um campo com este nome. Escolha outro.")
                    return redirect(url_for("agricultura"))

                field = AgricultureField(
                    user_id=current_user.id,
                    nome_campo=nome_campo,
                    cultura=request.form.get("cultura", "").strip(),
                    area_ha=float(request.form.get("area_ha", 0) or 0),
                    data_plantio=datetime.strptime(request.form.get("data_plantio"), "%Y-%m-%d").date(),
                    data_colheita_prevista=datetime.strptime(request.form.get("data_colheita_prevista"), "%Y-%m-%d").date() if request.form.get("data_colheita_prevista") else None,
                    produtividade_esperada_ton_ha=float(request.form.get("produtividade_esperada_ton_ha", 0) or 0),
                    observacoes=request.form.get("observacoes", "").strip()
                )
                db.session.add(field)
                db.session.commit()
                flash("Campo agrícola registrado com sucesso!")
            except ValueError:
                flash("Erro: Verifique se os valores numéricos e de data estão corretos.")
            except Exception as e:
                flash(f"Ocorreu um erro ao registrar o campo: {e}")
            return redirect(url_for("agricultura"))

    cotacoes = AgricultureQuote.query.filter_by(user_id=current_user.id).order_by(AgricultureQuote.criado_em.desc()).all()
    campos = AgricultureField.query.filter_by(user_id=current_user.id).order_by(AgricultureField.data_plantio.desc()).all()

    html_content = """
    <h2>Agricultura</h2>

    <div class="grid">
      <div class="card">
        <h3>Nova Cotação</h3>
        <form method="post">
          <input type="hidden" name="form_type" value="nova_cotacao">
          <select name="produto" required>
            {% for p in CBOT.keys() %}<option value="{{ p }}">{{ p|capitalize }}</option>{% endfor %}
          </select>
          <input type="number" step="0.01" name="quantidade_ton" placeholder="Quantidade (ton)" required>
          <input name="origem" placeholder="Origem (Cidade-UF)" required>
          <select name="porto" required>
            {% for p in PORTOS %}<option value="{{ p }}">{{ p }}</option>{% endfor %}
          </select>
          <button class="btn btn-ok" type="submit">Calcular Cotação</button>
        </form>
      </div>

      <div class="card">
        <h3>Cotações Recentes</h3>
        <table>
          <tr><th>Produto</th><th>Quantidade</th><th>Origem</th><th>Porto</th><th>Líquido R$/Ton</th><th>Total R$</th><th>Ações</th></tr>
          {% for quote in cotacoes %}
            <tr>
              <td>{{ quote.produto|capitalize }}</td>
              <td>{{ quote.quantidade_ton }} ton</td>
              <td>{{ quote.origem }}</td>
              <td>{{ quote.porto }}</td>
              <td>R$ {{ quote.liquido_rs_ton }}</td>
              <td>R$ {{ quote.total_rs }}</td>
              <td>
                <form method="post" action="{{ url_for('excluir_cotacao', quote_id=quote.id) }}" style="display:inline;">
                  <button type="submit" class="btn btn-ghost" onclick="return confirm('Tem certeza que deseja excluir esta cotação?');">Excluir</button>
                </form>
              </td>
            </tr>
          {% else %}
            <tr><td colspan="7">Nenhuma cotação registrada ainda.</td></tr>
          {% endfor %}
        </table>
      </div>
    </div>

    <div class="card">
      <h3>Meus Campos Agrícolas</h3>
      <form method="post">
        <input type="hidden" name="form_type" value="novo_campo">
        <input name="nome_campo" placeholder="Nome do Campo (ex: Fazenda A - Talhão 1)" required>
        <input name="cultura" placeholder="Cultura (ex: Soja, Milho)" required>
        <input type="number" step="0.01" name="area_ha" placeholder="Área (hectares)" required>
        <label>Data de Plantio</label>
        <input type="date" name="data_plantio" required>
        <label>Data de Colheita Prevista</label>
        <input type="date" name="data_colheita_prevista">
        <input type="number" step="0.01" name="produtividade_esperada_ton_ha" placeholder="Produtividade Esperada (ton/ha)">
        <textarea name="observacoes" placeholder="Observações"></textarea>
        <button class="btn btn-ok" type="submit">Registrar Campo</button>
      </form>

      <table style="margin-top:20px;">
        <tr><th>Campo</th><th>Cultura</th><th>Área (ha)</th><th>Plantio</th><th>Colheita Prev.</th><th>Prod. Esp. (ton/ha)</th><th>Status</th><th>Ações</th></tr>
        {% for field in campos %}
          <tr>
            <td>{{ field.nome_campo }}</td>
            <td>{{ field.cultura }}</td>
            <td>{{ field.area_ha }}</td>
            <td>{{ field.data_plantio.strftime('%d/%m/%Y') }}</td>
            <td>{{ field.data_colheita_prevista.strftime('%d/%m/%Y') if field.data_colheita_prevista else '-' }}</td>
            <td>{{ field.produtividade_esperada_ton_ha }}</td>
            <td>{{ field.status|capitalize }}</td>
            <td>
              <a class="btn btn-ghost" href="{{ url_for('detalhes_campo_agricola', field_id=field.id) }}">Detalhes</a>
              <form method="post" action="{{ url_for('excluir_campo_agricola', field_id=field.id) }}" style="display:inline;">
                <button type="submit" class="btn btn-ghost" onclick="return confirm('Tem certeza que deseja excluir este campo e seus registros?');">Excluir</button>
              </form>
            </td>
          </tr>
        {% else %}
          <tr><td colspan="8">Nenhum campo agrícola registrado ainda.</td></tr>
        {% endfor %}
      </table>
    </div>
    """
    return page(html_content, title="AP360 | Agricultura", CBOT=CBOT, PORTOS=PORTOS, cotacoes=cotacoes, campos=campos)


@app.route("/agricultura/excluir_cotacao/<int:quote_id>", methods=["POST"])
@login_required
def excluir_cotacao(quote_id):
    quote = AgricultureQuote.query.filter_by(id=quote_id, user_id=current_user.id).first_or_404()
    db.session.delete(quote)
    db.session.commit()
    flash("Cotação excluída com sucesso!")
    return redirect(url_for("agricultura"))


@app.route("/agricultura/campo/<int:field_id>", methods=["GET", "POST"])
@login_required
def detalhes_campo_agricola(field_id):
    field = AgricultureField.query.filter_by(id=field_id, user_id=current_user.id).first_or_404()
    daily_records = AgricultureDailyRecord.query.filter_by(field_id=field.id).order_by(AgricultureDailyRecord.data_registro.asc()).all()

    if request.method == "POST":
        form_type = request.form.get("form_type")
        if form_type == "novo_registro_diario":
            try:
                data_registro = datetime.strptime(request.form.get("data_registro"), "%Y-%m-%d").date()
                if AgricultureDailyRecord.query.filter_by(field_id=field.id, data_registro=data_registro).first():
                    flash("Já existe um registro para esta data neste campo.")
                    return redirect(url_for("detalhes_campo_agricola", field_id=field.id))

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
            return redirect(url_for("detalhes_campo_agricola", field_id=field.id))

    # Preparar dados para gráficos
    chart_labels = [r.data_registro.strftime('%d/%m') for r in daily_records]
    chart_chuva = [r.chuva_mm for r in daily_records]
    chart_temperatura = [r.temperatura_c for r in daily_records]
    chart_produtividade_parcial = [r.produtividade_parcial_ton_ha for r in daily_records]

    # Simulação Agrosim (exemplo simplificado)
    # Esta é uma simulação interna. Em um sistema real, você integraria com APIs de modelos agronômicos.
    # Aqui, vamos apenas projetar um crescimento linear simples para fins de demonstração.
    dias_desde_plantio = (datetime.now().date() - field.data_plantio).days
    projecao_produtividade = 0.0
    if dias_desde_plantio > 0 and field.data_colheita_prevista:
        dias_totais_ciclo = (field.data_colheita_prevista - field.data_plantio).days
        if dias_totais_ciclo > 0:
            progresso_pct = min(1.0, dias_desde_plantio / dias_totais_ciclo)
            projecao_produtividade = round(field.produtividade_esperada_ton_ha * progresso_pct, 2)

    html_content = """
    <h2>Detalhes do Campo: {{ field.nome_campo }}</h2>
    <div class="card">
      <p><b>Cultura:</b> {{ field.cultura }}</p>
      <p><b>Área:</b> {{ field.area_ha }} ha</p>
      <p><b>Plantio:</b> {{ field.data_plantio.strftime('%d/%m/%Y') }}</p>
      <p><b>Colheita Prevista:</b> {{ field.data_colheita_prevista.strftime('%d/%m/%Y') if field.data_colheita_prevista else '-' }}</p>
      <p><b>Produtividade Esperada:</b> {{ field.produtividade_esperada_ton_ha }} ton/ha</p>
      <p><b>Status:</b> {{ field.status|capitalize }}</p>
      <p><b>Observações:</b> {{ field.observacoes or '-' }}</p>
      <a class="btn btn-ghost" href="{{ url_for('agricultura') }}">Voltar para Agricultura</a>
    </div>

    <div class="grid">
      <div class="card">
        <h3>Agrosim - Projeção de Produtividade</h3>
        <p>Dias desde o plantio: <b>{{ dias_desde_plantio }}</b></p>
        <p>Produtividade Projetada Atual: <b>{{ projecao_produtividade }} ton/ha</b></p>
        <p class="muted">
          *Esta é uma simulação simplificada. Em um sistema real, consideraria mais fatores
          climáticos, de solo e de manejo.
        </p>
      </div>
      <div class="card">
        <h3>Adicionar Registro Diário</h3>
        <form method="post">
          <input type="hidden" name="form_type" value="novo_registro_diario">
          <label>Data do Registro</label>
          <input type="date" name="data_registro" required>
          <input type="number" step="0.01" name="chuva_mm" placeholder="Chuva (mm)">
          <input type="number" step="0.01" name="temperatura_c" placeholder="Temperatura (°C)">
          <input name="insumo_aplicado" placeholder="Insumo Aplicado (ex: Ureia)">
          <input type="number" step="0.01" name="quantidade_insumo" placeholder="Quantidade Insumo (kg/ha)">
          <input type="number" step="0.01" name="produtividade_parcial_ton_ha" placeholder="Produtividade Parcial (ton/ha)">
          <textarea name="observacoes" placeholder="Observações"></textarea>
          <button class="btn btn-ok" type="submit">Salvar Registro</button>
        </form>
      </div>
    </div>

    <div class="card">
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
          <canvas id="produtividadeChart" height="150"></canvas>
        </div>
        {% else %}
          <p class="muted">Adicione registros diários para ver os gráficos.</p>
        {% endif %}
      </div>
      <script>
        const chartLabels = {{ chart_labels | tojson }};
        const chartChuva = {{ chart_chuva | tojson }};
        const chartTemperatura = {{ chart_temperatura | tojson }};
        const chartProdutividade = {{ chart_produtividade_parcial | tojson }};

        if (chartLabels.length > 0) {
          new Chart(document.getElementById("chuvaChart"), {
            type: "bar",
            data: { labels: chartLabels, datasets: [{ label: "Chuva (mm)", data: chartChuva, backgroundColor: 'rgba(59, 185, 255, 0.5)' }] },
            options: { responsive: true, scales: { y: { beginAtZero: true } } }
          });
          new Chart(document.getElementById("temperaturaChart"), {
            type: "line",
            data: { labels: chartLabels, datasets: [{ label: "Temperatura (°C)", data: chartTemperatura, borderColor: 'rgba(255, 159, 64, 1)', tension: 0.2 }] },
            options: { responsive: true, scales: { y: { beginAtZero: false } } }
          });
          new Chart(document.getElementById("produtividadeChart"), {
            type: "line",
            data: { labels: chartLabels, datasets: [{ label: "Produtividade Parcial (ton/ha)", data: chartProdutividade, borderColor: 'rgba(70, 221, 152, 1)', tension: 0.2 }] },
            options: { responsive: true, scales: { y: { beginAtZero: true } } }
          });
        }
      </script>
    </div>

    <div class="card">
      <h3>Histórico de Registros Diários</h3>
      <table>
        <tr><th>Data</th><th>Chuva (mm)</th><th>Temp. (°C)</th><th>Insumo</th><th>Qtd. Insumo</th><th>Prod. Parcial (ton/ha)</th><th>Observações</th><th>Ações</th></tr>
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
              <form method="post" action="{{ url_for('excluir_registro_diario_agricola', record_id=record.id) }}" style="display:inline;">
                <button type="submit" class="btn btn-ghost" onclick="return confirm('Tem certeza que deseja excluir este registro?');">Excluir</button>
              </form>
            </td>
          </tr>
        {% else %}
          <tr><td colspan="8">Nenhum registro diário para este campo ainda.</td></tr>
        {% endfor %}
      </table>
    </div>
    """
    return page(html_content, title=f"AP360 | Campo {field.nome_campo}", field=field,
                daily_records=daily_records, chart_labels=chart_labels, chart_chuva=chart_chuva,
                chart_temperatura=chart_temperatura, chart_produtividade_parcial=chart_produtividade_parcial,
                dias_desde_plantio=dias_desde_plantio, projecao_produtividade=projecao_produtividade)


@app.route("/agricultura/campo/excluir/<int:field_id>", methods=["POST"])
@login_required
def excluir_campo_agricola(field_id):
    field = AgricultureField.query.filter_by(id=field_id, user_id=current_user.id).first_or_404()
    AgricultureDailyRecord.query.filter_by(field_id=field.id).delete() # Exclui registros diários
    db.session.delete(field)
    db.session.commit()
    flash("Campo agrícola e seus registros excluídos com sucesso!")
    return redirect(url_for("agricultura"))


@app.route("/agricultura/registro_diario/excluir/<int:record_id>", methods=["POST"])
@login_required
def excluir_registro_diario_agricola(record_id):
    record = AgricultureDailyRecord.query.get_or_404(record_id)
    field_id = record.field_id
    field = AgricultureField.query.filter_by(id=field_id, user_id=current_user.id).first_or_404() # Garante que o usuário é o dono do campo
    db.session.delete(record)
    db.session.commit()
    flash("Registro diário excluído com sucesso!")
    return redirect(url_for("detalhes_campo_agricola", field_id=field_id))


# =========================================================
# AVICULTURA / SUINOCULTURA (MÓDULO DE LOTES)
# =========================================================
@app.route("/<string:cadeia>", methods=["GET", "POST"])
@login_required
def modulo_lotes(cadeia):
    if cadeia not in ["avicultura", "suinocultura"]:
        abort(404)
    if current_user.segmento != cadeia:
        flash(f"Seu perfil não tem acesso ao módulo de {cadeia}.")
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        try:
            # Converte as datas e horas para objetos datetime no fuso de Brasília
            data_alojamento_str = request.form.get("data_alojamento")
            hora_alojamento_str = request.form.get("hora_alojamento")
            data_carregamento_str = request.form.get("data_carregamento")
            hora_carregamento_str = request.form.get("hora_carregamento")

            dt_alojamento_naive = datetime.strptime(f"{data_alojamento_str} {hora_alojamento_str}", "%Y-%m-%d %H:%M")
            dt_carregamento_naive = datetime.strptime(f"{data_carregamento_str} {hora_carregamento_str}", "%Y-%m-%d %H:%M")

            # Localiza as datas/horas para Brasília
            dt_alojamento = BRASILIA_TZ.localize(dt_alojamento_naive)
            dt_carregamento = BRASILIA_TZ.localize(dt_carregamento_naive)

            # Calcula a diferença em dias com vírgula
            diferenca = dt_carregamento - dt_alojamento
            dias_com_virgula = round(diferenca.total_seconds() / (24 * 3600), 2) # Dias com 2 casas decimais

            batch = Batch(
                user_id=current_user.id,
                cadeia=cadeia,
                estrutura=request.form.get("estrutura", "").strip(),
                lote=request.form.get("lote", "").strip(),
                peso_inicial=float(request.form.get("peso_inicial", 0) or 0), # Peso MÉDIO por animal
                peso_final=float(request.form.get("peso_final", 0) or 0),     # Peso MÉDIO por animal
                data_alojamento=dt_alojamento,
                data_carregamento=dt_carregamento,
                dias=dias_com_virgula,
                racao_total_kg=float(request.form.get("racao_total_kg", 0) or 0), # Ração TOTAL do lote
                animais_iniciais=int(request.form.get("animais_iniciais", 0) or 0),
                animais_final=int(request.form.get("animais_final", 0) or 0)
            )

            # --- CÁLCULOS ---
            batch.gpd = calc_gpd(batch.peso_inicial, batch.peso_final, batch.dias)
            batch.ca = calc_ca(batch.racao_total_kg, batch.peso_inicial, batch.peso_final, batch.animais_final)
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
                batch.idade_meta_coop = float(request.form.get("idade_meta_coop", 0) or 0) # Agora float
                batch.fator_peso_caa = float(request.form.get("fator_peso_caa", 0.30) or 0.30)
                batch.fator_idade_caa = float(request.form.get("fator_idade_caa", 0.01) or 0.01)

                # DEBUG PRINTS para CAA
                print(f"DEBUG CAA (NEW): ca_observada={batch.ca}")
                print(f"DEBUG CAA (NEW): peso_real={batch.peso_final}") # Corrigido para usar batch.peso_final
                print(f"DEBUG CAA (NEW): idade_real={batch.dias}")
                print(f"DEBUG CAA (NEW): peso_meta={batch.peso_meta_coop}")
                print(f"DEBUG CAA (NEW): idade_meta={batch.idade_meta_coop}")
                print(f"DEBUG CAA (NEW): fator_peso={batch.fator_peso_caa}")
                print(f"DEBUG CAA (NEW): fator_idade={batch.fator_idade_caa}")

                if batch.peso_meta_coop > 0 and batch.idade_meta_coop > 0: # Apenas ajusta se tiver metas definidas
                    batch.ca_ajustada = calc_ca_ajustada_avicultura(
                        ca_observada=batch.ca,
                        peso_real=batch.peso_final, # Passa o peso final médio do lote
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
                bonus_tipificacao = calc_bonus_tipificacao(batch.carne_magra_pct, batch.rendimento_carcaca_pct)

            bon = calc_bonificacao(batch.gpd, batch.ca_ajustada, meta_gpd, meta_ca, bonus_base)
            batch.bonificacao = round(bon + bonus_tipificacao, 2)
            batch.coop_media_gpd = meta_gpd
            batch.coop_media_ca = meta_ca

            db.session.add(batch)
            db.session.commit()
            flash(f"Lote de {cadeia} registrado com sucesso!")
            return redirect(url_for(cadeia))
        except ValueError as e:
            flash(f"Erro nos dados de entrada: {e}. Verifique se todos os campos numéricos e de data/hora estão corretos.")
        except Exception as e:
            flash(f"Ocorreu um erro inesperado: {e}")

    hist = Batch.query.filter_by(user_id=current_user.id, cadeia=cadeia).order_by(Batch.criado_em.desc()).all()

    # Lógica para comparação de lotes (se houver)
    c1 = request.args.get("c1", type=int)
    c2 = request.args.get("c2", type=int)
    compare_data = None
    if c1 and c2:
        batch1 = Batch.query.get(c1)
        batch2 = Batch.query.get(c2)
        if batch1 and batch2 and batch1.user_id == current_user.id and batch2.user_id == current_user.id:
            compare_data = {
                "labels": ["GPD", "CA", "CAA", "Viabilidade%", "Mortalidade%", "IEP/Índice", "Bonificação"],
                "a_name": f"{batch1.estrutura} - {batch1.lote}",
                "a_vals": [batch1.gpd, batch1.ca, batch1.ca_ajustada, batch1.viabilidade_pct, batch1.mortalidade_pct, batch1.iep if cadeia == 'avicultura' else batch1.indice_lote, batch1.bonificacao],
                "b_name": f"{batch2.estrutura} - {batch2.lote}",
                "b_vals": [batch2.gpd, batch2.ca, batch2.ca_ajustada, batch2.viabilidade_pct, batch2.mortalidade_pct, batch2.iep if cadeia == 'avicultura' else batch2.indice_lote, batch2.bonificacao],
            }

    # Resultado do último lote para exibição
    resultado = hist[0] if hist else None

    html_content = """
    <h2>{{ cadeia|capitalize }}</h2>

    <div class="card">
      <h3>Novo Lote</h3>
      <form method="post">
        <label>Estrutura</label>
        <input name="estrutura" placeholder="Nome da estrutura (ex: Galpão 1)" required>
        <label>Lote</label>
        <input name="lote" placeholder="Identificação do lote (ex: Lote 2024-06)" required>

        <label>Data e Hora de Alojamento</label>
        <input type="date" name="data_alojamento" value="{{ datetime.now(BRASILIA_TZ).strftime('%Y-%m-%d') }}" required>
        <input type="time" name="hora_alojamento" value="{{ datetime.now(BRASILIA_TZ).strftime('%H:%M') }}" required>

        <label>Data e Hora de Carregamento/Abate</label>
        <input type="date" name="data_carregamento" value="{{ datetime.now(BRASILIA_TZ).strftime('%Y-%m-%d') }}" required>
        <input type="time" name="hora_carregamento" value="{{ datetime.now(BRASILIA_TZ).strftime('%H:%M') }}" required>

        <label>Peso inicial médio por animal (kg)</label>
        <input type="number" step="0.0001" name="peso_inicial" placeholder="Ex: 0.04 (pintinho) ou 30.0 (leitão)" required>
        <label>Peso final médio por animal (kg)</label>
        <input type="number" step="0.0001" name="peso_final" placeholder="Ex: 2.85 (frango) ou 120.0 (suíno)" required>

        <label>Ração TOTAL consumida pelo lote (kg)</label>
        <input type="number" step="0.0001" name="racao_total_kg" placeholder="Ex: 4800.0 (para 1000 aves)" required>
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
          <h4>CA Ajustada (Avicultura)</h4>
          <label>Peso meta coop (kg)</label>
          <input type="number" step="0.0001" name="peso_meta_coop" placeholder="Peso meta coop (kg), ex: 2.90">
          <label>Idade meta coop (dias)</label>
          <input type="number" step="0.01" name="idade_meta_coop" placeholder="Idade meta coop (dias), ex: 42.5">
          <label>Fator peso CAA</label>
          <input type="number" step="0.0001" name="fator_peso_caa" value="0.30" placeholder="Fator peso CAA, ex: 0.30">
          <label>Fator idade CAA</label>
          <input type="number" step="0.0001" name="fator_idade_caa" value="0.01" placeholder="Fator idade CAA, ex: 0.01">
        {% endif %}

        {% if cadeia == 'suinocultura' %}
          <h4>Carcaça e tipificação (Suínos)</h4>
          <label>Peso vivo médio (kg/cab)</label>
          <input type="number" step="0.01" name="peso_vivo_medio" placeholder="Peso vivo médio (kg/cab)" required>
          <label>Peso carcaça médio (kg/cab)</label>
          <input type="number" step="0.01" name="peso_carcaca_medio" placeholder="Peso carcaça médio (kg/cab)" required>
          <label>% carne magra</label>
          <input type="number" step="0.01" name="carne_magra_pct" placeholder="% carne magra" required>
        {% endif %}

        <button class="btn btn-ok" type="submit">Calcular e Salvar Lote</button>
      </form>
    </div>

    {% if resultado %}
    <div class="card">
      <h3>Resultado do último lote ({{ resultado.estrutura }} - {{ resultado.lote }})</h3>
      <div class="grid3">
        <div>GPD<br><span class="kpi">{{ resultado.gpd }}</span></div>
        <div>CA<br><span class="kpi">{{ resultado.ca }}</span></div>
        <div>CA Ajustada<br><span class="kpi">{{ resultado.ca_ajustada }}</span></div>
        <div>Viabilidade<br><span class="kpi">{{ resultado.viabilidade_pct }}%</span></div>
        <div>Mortalidade<br><span class="kpi">{{ resultado.mortalidade_pct }}%</span></div>
        <div>IEP/EPEF<br><span class="kpi">{% if cadeia == 'avicultura' %}{{ resultado.iep }}{% else %}{{ resultado.indice_lote }}{% endif %}</span></div>
        <div>Peso meta<br><span class="kpi">{{ resultado.peso_meta_coop }}</span></div>
        <div>Idade meta<br><span class="kpi">{{ resultado.idade_meta_coop }}</span></div>
        <div>Bônus total<br><span class="kpi">R$ {{ resultado.bonificacao }}</span></div>
      </div>
      <a class="btn btn-pri" href="{{ url_for('detalhes_lote_ao_vivo', cadeia=cadeia, batch_id=resultado.id) }}" style="margin-top:10px;">Acompanhamento ao Vivo</a>
    </div>
    {% endif %}

    {% if compare_data %}
      <div class="card">
        <h3>Comparativo de Lotes</h3>
        <canvas id="cmpChart" height="90"></canvas>
      </div>
      <script>
        const cmp = {{ compare_data | tojson }};
        new Chart(document.getElementById("cmpChart"), {
          type: "bar",
          data: {
            labels: cmp.labels,
            datasets: [
              { label: cmp.a_name, data: cmp.a_vals, backgroundColor: 'rgba(59, 185, 255, 0.7)' },
              { label: cmp.b_name, data: cmp.b_vals, backgroundColor: 'rgba(70, 221, 152, 0.7)' }
            ]
          },
          options: { responsive: true, scales: { y: { beginAtZero: true } } }
        });
      </script>
    {% endif %}

    <div class="card">
      <h3>Histórico de Lotes</h3>
      <p class="muted">Selecione dois lotes para comparar:</p>
      <form method="get" action="{{ url_for('modulo_lotes', cadeia=cadeia) }}" style="display:flex;gap:10px;margin-bottom:10px;">
        <select name="c1">
          <option value="">Comparar Lote 1</option>
          {% for b in hist %}<option value="{{ b.id }}" {% if c1 == b.id %}selected{% endif %}>{{ b.estrutura }} - {{ b.lote }}</option>{% endfor %}
        </select>
        <select name="c2">
          <option value="">Comparar Lote 2</option>
          {% for b in hist %}<option value="{{ b.id }}" {% if c2 == b.id %}selected{% endif %}>{{ b.estrutura }} - {{ b.lote }}</option>{% endfor %}
        </select>
        <button type="submit" class="btn btn-ghost">Comparar</button>
      </form>

      <table>
        <tr>
          <th>Data Criação</th><th>Estrutura</th><th>Lote</th><th>Alojamento</th><th>Carregamento</th><th>Dias</th><th>GPD</th><th>CA</th><th>CAA</th>
          <th>Viab%</th><th>Mort%</th><th>IEP/Índice</th><th>Rend. Carcaça%</th><th>Bônus</th>
          <th>Ações</th>
        </tr>
        {% for h in hist %}
        <tr>
          <td>{{ h.criado_em.strftime("%d/%m %H:%M") }}</td>
          <td>{{ h.estrutura }}</td>
          <td>{{ h.lote }}</td>
          <td>{{ h.data_alojamento.astimezone(BRASILIA_TZ).strftime("%d/%m %H:%M") }}</td>
          <td>{{ h.data_carregamento.astimezone(BRASILIA_TZ).strftime("%d/%m %H:%M") }}</td>
          <td>{{ h.dias }}</td>
          <td>{{ h.gpd }}</td>
          <td>{{ h.ca }}</td>
          <td>{{ h.ca_ajustada }}</td>
          <td>{{ h.viabilidade_pct }}</td>
          <td>{{ h.mortalidade_pct }}</td>
          <td>{% if cadeia == 'avicultura' %}{{ h.iep }}{% else %}{{ h.indice_lote }}{% endif %}</td>
          <td>{{ h.rendimento_carcaca_pct }}</td>
          <td>R$ {{ h.bonificacao }}</td>
          <td>
            <a class="btn btn-ghost" href="{{ url_for('detalhes_lote_ao_vivo', cadeia=cadeia, batch_id=h.id) }}">Acomp.</a>
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
                datetime=datetime, BRASILIA_TZ=BRASILIA_TZ) # Passa datetime e BRASILIA_TZ para o template


@app.route("/<string:cadeia>/editar/<int:batch_id>", methods=["GET", "POST"])
@login_required
def editar_lote(cadeia, batch_id):
    batch = Batch.query.filter_by(id=batch_id, user_id=current_user.id).first_or_404()

    if request.method == "POST":
        try:
            # Converte as datas e horas para objetos datetime no fuso de Brasília
            data_alojamento_str = request.form.get("data_alojamento")
            hora_alojamento_str = request.form.get("hora_alojamento")
            data_carregamento_str = request.form.get("data_carregamento")
            hora_carregamento_str = request.form.get("hora_carregamento")

            dt_alojamento_naive = datetime.strptime(f"{data_alojamento_str} {hora_alojamento_str}", "%Y-%m-%d %H:%M")
            dt_carregamento_naive = datetime.strptime(f"{data_carregamento_str} {hora_carregamento_str}", "%Y-%m-%d %H:%M")

            # Localiza as datas/horas para Brasília
            dt_alojamento = BRASILIA_TZ.localize(dt_alojamento_naive)
            dt_carregamento = BRASILIA_TZ.localize(dt_carregamento_naive)

            # Calcula a diferença em dias com vírgula
            diferenca = dt_carregamento - dt_alojamento
            dias_com_virgula = round(diferenca.total_seconds() / (24 * 3600), 2) # Dias com 2 casas decimais

            batch.estrutura = request.form.get("estrutura", "").strip()
            batch.lote = request.form.get("lote", "").strip()
            batch.peso_inicial = float(request.form.get("peso_inicial", 0) or 0) # Peso MÉDIO por animal
            batch.peso_final = float(request.form.get("peso_final", 0) or 0)     # Peso MÉDIO por animal
            batch.data_alojamento = dt_alojamento
            batch.data_carregamento = dt_carregamento
            batch.dias = dias_com_virgula
            batch.racao_total_kg = float(request.form.get("racao_total_kg", 0) or 0) # Ração TOTAL do lote
            batch.animais_iniciais = int(request.form.get("animais_iniciais", 0) or 0)
            batch.animais_final = int(request.form.get("animais_final", 0) or 0)

            # --- CÁLCULOS ---
            batch.gpd = calc_gpd(batch.peso_inicial, batch.peso_final, batch.dias)
            batch.ca = calc_ca(batch.racao_total_kg, batch.peso_inicial, batch.peso_final, batch.animais_final)
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
                batch.idade_meta_coop = float(request.form.get("idade_meta_coop", 0) or 0) # Agora float
                batch.fator_peso_caa = float(request.form.get("fator_peso_caa", 0.30) or 0.30)
                batch.fator_idade_caa = float(request.form.get("fator_idade_caa", 0.01) or 0.01)

                # DEBUG PRINTS para CAA
                print(f"DEBUG CAA (EDIT): ca_observada={batch.ca}")
                print(f"DEBUG CAA (EDIT): peso_real={batch.peso_final}") # Corrigido para usar batch.peso_final
                print(f"DEBUG CAA (EDIT): idade_real={batch.dias}")
                print(f"DEBUG CAA (EDIT): peso_meta={batch.peso_meta_coop}")
                print(f"DEBUG CAA (EDIT): idade_meta={batch.idade_meta_coop}")
                print(f"DEBUG CAA (EDIT): fator_peso={batch.fator_peso_caa}")
                print(f"DEBUG CAA (EDIT): fator_idade={batch.fator_idade_caa}")

                if batch.peso_meta_coop > 0 and batch.idade_meta_coop > 0: # Apenas ajusta se tiver metas definidas
                    batch.ca_ajustada = calc_ca_ajustada_avicultura(
                        ca_observada=batch.ca,
                        peso_real=batch.peso_final, # Passa o peso final médio do lote
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
                bonus_tipificacao = calc_bonus_tipificacao(batch.carne_magra_pct, batch.rendimento_carcaca_pct)

            bon = calc_bonificacao(batch.gpd, batch.ca_ajustada, meta_gpd, meta_ca, bonus_base)
            batch.bonificacao = round(bon + bonus_tipificacao, 2)
            batch.coop_media_gpd = meta_gpd
            batch.coop_media_ca = meta_ca

            db.session.commit()
            flash(f"Lote de {cadeia} atualizado com sucesso!")
            return redirect(url_for(cadeia))
        except ValueError as e:
            flash(f"Erro nos dados de entrada: {e}. Verifique se todos os campos numéricos e de data/hora estão corretos.")
        except Exception as e:
            flash(f"Ocorreu um erro inesperado: {e}")

    html_content = """
    <h2>Editar Lote de {{ cadeia|capitalize }}</h2>
    <div class="card" style="max-width:700px;margin:0 auto">
      <form method="post">
        <label>Estrutura</label>
        <input name="estrutura" value="{{ batch.estrutura }}" required>
        <label>Lote</label>
        <input name="lote" value="{{ batch.lote }}" required>

        <label>Data e Hora de Alojamento</label>
        <input type="date" name="data_alojamento" value="{{ batch.data_alojamento.astimezone(BRASILIA_TZ).strftime('%Y-%m-%d') }}" required>
        <input type="time" name="hora_alojamento" value="{{ batch.data_alojamento.astimezone(BRASILIA_TZ).strftime('%H:%M') }}" required>

        <label>Data e Hora de Carregamento/Abate</label>
        <input type="date" name="data_carregamento" value="{{ batch.data_carregamento.astimezone(BRASILIA_TZ).strftime('%Y-%m-%d') }}" required>
        <input type="time" name="hora_carregamento" value="{{ batch.data_carregamento.astimezone(BRASILIA_TZ).strftime('%H:%M') }}" required>

        <label>Peso inicial médio por animal (kg)</label>
        <input type="number" step="0.0001" name="peso_inicial" value="{{ batch.peso_inicial }}" required>
        <label>Peso final médio por animal (kg)</label>
        <input type="number" step="0.0001" name="peso_final" value="{{ batch.peso_final }}" required>

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
          <input type="number" step="0.0001" name="peso_meta_coop" value="{{ batch.peso_meta_coop }}" placeholder="Peso meta coop (kg), ex: 2.90">
          <label>Idade meta coop (dias)</label>
          <input type="number" step="0.01" name="idade_meta_coop" value="{{ batch.idade_meta_coop }}" placeholder="Idade meta coop (dias), ex: 42.5">
          <label>Fator peso CAA</label>
          <input type="number" step="0.0001" name="fator_peso_caa" value="{{ batch.fator_peso_caa }}" placeholder="Fator peso CAA, ex: 0.30">
          <label>Fator idade CAA</label>
          <input type="number" step="0.0001" name="fator_idade_caa" value="{{ batch.fator_idade_caa }}" placeholder="Fator idade CAA, ex: 0.01">
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
                datetime=datetime, BRASILIA_TZ=BRASILIA_TZ) # Passa datetime e BRASILIA_TZ para o template


@app.route("/<string:cadeia>/excluir/<int:batch_id>", methods=["POST"])
@login_required
def excluir_lote(cadeia, batch_id):
    batch = Batch.query.filter_by(id=batch_id, user_id=current_user.id, cadeia=cadeia).first_or_404()
    BatchDailyRecord.query.filter_by(batch_id=batch.id).delete() # Exclui registros diários do lote
    db.session.delete(batch)
    db.session.commit()
    flash(f"Lote de {cadeia} excluído com sucesso!")
    return redirect(url_for(cadeia))


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

    html_content = """
    <h2>Acompanhamento ao Vivo: {{ batch.estrutura }} - {{ batch.lote }} ({{ cadeia|capitalize }})</h2>
    <div class="card">
      <p><b>Alojamento:</b> {{ batch.data_alojamento.astimezone(BRASILIA_TZ).strftime('%d/%m/%Y %H:%M') }}</p>
      <p><b>Carregamento:</b> {{ batch.data_carregamento.astimezone(BRASILIA_TZ).strftime('%d/%m/%Y %H:%M') }}</p>
      <p><b>Dias de Alojamento:</b> {{ batch.dias }}</p>
      <p><b>Peso Inicial Médio:</b> {{ batch.peso_inicial }} kg</p>
      <p><b>Peso Final Médio:</b> {{ batch.peso_final }} kg</p>
      <p><b>Ração Total:</b> {{ batch.racao_total_kg }} kg</p>
      <p><b>Animais Iniciais:</b> {{ batch.animais_iniciais }}</p>
      <p><b>Animais Finais:</b> {{ batch.animais_final }}</p>
      <p><b>GPD:</b> {{ batch.gpd }}</p>
      <p><b>CA:</b> {{ batch.ca }}</p>
      <p><b>CA Ajustada:</b> {{ batch.ca_ajustada }}</p>
      <p><b>Viabilidade:</b> {{ batch.viabilidade_pct }}%</p>
      <p><b>Mortalidade:</b> {{ batch.mortalidade_pct }}%</p>
      <p><b>IEP/Índice:</b> {% if cadeia == 'avicultura' %}{{ batch.iep }}{% else %}{{ batch.indice_lote }}{% endif %}</p>
      <p><b>Bonificação:</b> R$ {{ batch.bonificacao }}</p>
      <a class="btn btn-ghost" href="{{ url_for('modulo_lotes', cadeia=cadeia) }}">Voltar para Lotes</a>
    </div>

    <div class="grid">
      <div class="card">
        <h3>Adicionar Registro Diário</h3>
        <form method="post">
          <input type="hidden" name="form_type" value="novo_registro_diario_lote">
          <label>Data do Registro</label>
          <input type="date" name="data_registro" required>
          <input type="number" step="0.01" name="peso_medio" placeholder="Peso Médio (kg/animal)">
          <input type="number" step="0.01" name="consumo_racao_dia" placeholder="Consumo Ração (kg/dia do lote)">
          <input type="number" name="mortalidade_dia" placeholder="Mortalidade (nº de animais no dia)">
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
              <form method="post" action="{{ url_for('excluir_registro_diario_lote', record_id=record.id) }}" style="display:inline;">
                <button type="submit" class="btn btn-ghost" onclick="return confirm('Tem certeza que deseja excluir este registro?');">Excluir</button>
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