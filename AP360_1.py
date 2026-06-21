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
          <a href="{{ url_for('agricultura_modulo') }}">Agricultura</a> {# Rota específica para agricultura #}
          <a href="{{ url_for('modulo_lotes', cadeia='avicultura') }}">Avicultura</a> {# Corrigido #}
          <a href="{{ url_for('modulo_lotes', cadeia='suinocultura') }}">Suinocultura</a> {# Corrigido #}
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

    {{ content }}

  </div>
</div>
</body>
</html>
"""

def page(content: str, **kwargs):
    ctx = {
        "current_user": current_user,
        "get_flashed_messages": flash,
        **kwargs
    }
    processed_content = render_template_string(content, **ctx)
    return render_template_string(BASE_HTML, content=processed_content, **ctx)


# =========================================================
# ROUTES
# =========================================================
@app.route("/")
def index():
    html_content = """
    <div class="hero">
      <h1>Bem-vindo ao AP<b>360</b></h1>
      <p>Sua plataforma completa para gestão agrícola e pecuária. Monitore seus lotes, analise cotações, simule cenários e tome decisões mais inteligentes.</p>
      <p>Com o AP360, você tem o controle total da sua produção, desde o campo até o abate, com dados precisos e análises que realmente importam.</p>
      <p>Nossa IA te ajuda a otimizar resultados, e nossos módulos de agricultura, avicultura, suinocultura e bovinocultura oferecem ferramentas específicas para cada segmento.</p>
      {% if not current_user.is_authenticated %}
        <a class="btn btn-pri" href="{{ url_for('signup_request') }}">Solicitar Acesso</a>
        <a class="btn btn-ghost" href="{{ url_for('login') }}">Já tenho acesso</a>
      {% else %}
        <p>Olá, <span class="welcome-name">{{ current_user.nome }}</span>! Explore seu <a href="{{ url_for('dashboard') }}">Dashboard</a>.</p>
      {% endif %}
      <p style="margin-top: 20px;">Precisa de ajuda ou quer saber mais? Fale conosco pelo WhatsApp:</p>
      <a href="https://wa.me/5545999999999" target="_blank" class="whatsapp-link">
        <img src="https://upload.wikimedia.org/wikipedia/commons/6/6b/WhatsApp.svg" alt="WhatsApp" style="width: 20px; vertical-align: middle; margin-right: 5px;">
        (45) 99999-9999
      </a>
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
        <label>Email</label>
        <input type="email" name="email" required>
        <label>Senha</label>
        <input type="password" name="password" required>
        <button class="btn btn-pri" type="submit">Entrar</button>
      </form>
      <p class="muted" style="text-align:center;margin-top:15px">
        Não tem acesso? <a href="{{ url_for('signup_request') }}">Solicite aqui</a>
      </p>
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
        nome = request.form.get("nome", "").strip()
        cpf = request.form.get("cpf", "").strip()
        telefone = request.form.get("telefone", "").strip()
        email = request.form.get("email", "").strip().lower()
        segmento = request.form.get("segmento", "").strip()
        cooperativa = request.form.get("cooperativa", "").strip()

        if User.query.filter_by(email=email).first() or AccessRequest.query.filter_by(email=email).first():
            flash("Este email já possui uma solicitação ou conta.")
            return redirect(url_for("signup_request"))

        new_request = AccessRequest(
            nome=nome,
            cpf=cpf,
            telefone=telefone,
            email=email,
            segmento=segmento,
            cooperativa=cooperativa,
            status="pendente"
        )
        db.session.add(new_request)
        db.session.commit()
        flash("Sua solicitação de acesso foi enviada e será revisada em breve.")
        return redirect(url_for("index"))

    html_content = """
    <h2>Solicitar Acesso</h2>
    <div class="card" style="max-width:500px;margin:0 auto">
      <form method="post">
        <label>Nome Completo</label>
        <input name="nome" required>
        <label>CPF</label>
        <input name="cpf" placeholder="Opcional">
        <label>Telefone</label>
        <input name="telefone" placeholder="Opcional">
        <label>Email</label>
        <input type="email" name="email" required>
        <label>Segmento Principal</label>
        <select name="segmento" required>
          <option value="agricultura">Agricultura</option>
          <option value="avicultura">Avicultura</option>
          <option value="suinocultura">Suinocultura</option>
          <option value="bovinocultura">Bovinocultura</option>
        </select>
        <label>Cooperativa (se aplicável)</label>
        <input name="cooperativa" placeholder="Opcional">
        <button class="btn btn-pri" type="submit">Enviar Solicitação</button>
      </form>
    </div>
    """
    return page(html_content, title="AP360 | Solicitar Acesso")


@app.route("/activate/<token>")
def activate_account(token):
    invite = AccessInvite.query.filter_by(token=token, status="convidado").first()
    if not invite:
        flash("Token de ativação inválido ou já utilizado.")
        return redirect(url_for("login"))

    if request.method == "POST":
        password = request.form.get("password", "").strip()
        if len(password) < 6:
            flash("A senha deve ter pelo menos 6 caracteres.")
            return redirect(url_for("activate_account", token=token))

        user = User.query.filter_by(email=invite.email).first()
        if not user:
            # Isso não deveria acontecer se o convite foi gerado corretamente
            flash("Erro interno: usuário não encontrado para este convite.")
            return redirect(url_for("login"))

        user.set_password(password)
        user.status = "ativo"
        invite.status = "ativado"
        invite.ativado_em = datetime.utcnow()
        db.session.commit()
        flash("Sua conta foi ativada com sucesso! Faça login.")
        return redirect(url_for("login"))

    html_content = f"""
    <h2>Ativar Conta</h2>
    <div class="card" style="max-width:400px;margin:0 auto">
      <p>Olá, <strong>{invite.email}</strong>! Crie sua senha para ativar sua conta.</p>
      <form method="post">
        <label>Nova Senha</label>
        <input type="password" name="password" required minlength="6">
        <button class="btn btn-pri" type="submit">Ativar Conta</button>
      </form>
    </div>
    """
    return page(html_content, title="AP360 | Ativar Conta")


@app.route("/dashboard")
@login_required
def dashboard():
    html_content = f"""
    <h2>Dashboard de {current_user.nome}</h2>
    <div class="card">
      <p>Bem-vindo ao seu painel de controle, {current_user.nome}! Aqui você terá uma visão geral das suas operações.</p>
      <p>Seu perfil: <strong>{current_user.perfil.capitalize()}</strong></p>
      <p>Seu segmento: <strong>{current_user.segmento.capitalize() if current_user.segmento else 'Não definido'}</strong></p>
      <p>Sua cooperativa: <strong>{current_user.cooperativa if current_user.cooperativa else 'Não informada'}</strong></p>

      <h3>Atalhos Rápidos</h3>
      <div class="grid3">
        <a class="btn btn-ghost" href="{{ url_for('agricultura_modulo') }}">Módulo Agricultura</a>
        <a class="btn btn-ghost" href="{{ url_for('modulo_lotes', cadeia='avicultura') }}">Módulo Avicultura</a>
        <a class="btn btn-ghost" href="{{ url_for('modulo_lotes', cadeia='suinocultura') }}">Módulo Suinocultura</a>
        <a class="btn btn-ghost" href="{{ url_for('bovinocultura') }}">Módulo Bovinocultura</a>
        <a class="btn btn-ghost" href="{{ url_for('ia_page') }}">Assistente IA</a>
        {% if current_user.perfil == "admin" %}
          <a class="btn btn-ghost" href="{{ url_for('admin_panel') }}">Painel Admin</a>
        {% endif %}
      </div>
    </div>
    """
    return page(html_content, title="AP360 | Dashboard")


@app.route("/admin")
@admin_required
def admin_panel():
    requests = AccessRequest.query.order_by(AccessRequest.criado_em.desc()).all()
    invites = AccessInvite.query.order_by(AccessInvite.criado_em.desc()).all()
    users = User.query.order_by(User.criado_em.desc()).all()

    html_content = """
    <h2>Painel Administrativo</h2>

    <div class="card">
      <h3>Solicitações de Acesso</h3>
      <table>
        <tr><th>Data</th><th>Nome</th><th>Email</th><th>Segmento</th><th>Status</th><th>Ação</th></tr>
        {% for req in requests %}
          <tr>
            <td>{{ req.criado_em.strftime('%d/%m %H:%M') }}</td>
            <td>{{ req.nome }}</td>
            <td>{{ req.email }}</td>
            <td>{{ req.segmento|capitalize }}</td>
            <td>
              {% if req.status == 'pendente' %}
                <span style="color:orange">{{ req.status|capitalize }}</span>
              {% elif req.status == 'liberado' %}
                <span style="color:var(--ok)">{{ req.status|capitalize }}</span>
              {% else %}
                <span style="color:var(--danger)">{{ req.status|capitalize }}</span>
              {% endif %}
            </td>
            <td>
              {% if req.status == 'pendente' %}
                <form method="post" action="{{ url_for('admin_approve_request', request_id=req.id) }}" style="display:inline;">
                  <button type="submit" class="btn btn-ok">Aprovar</button>
                </form>
                <form method="post" action="{{ url_for('admin_deny_request', request_id=req.id) }}" style="display:inline;">
                  <button type="submit" class="btn btn-danger">Negar</button>
                </form>
              {% elif req.status == 'liberado' %}
                <span style="color:var(--ok)">Liberado</span>
                <form method="post" action="{{ url_for('admin_revoke_invite_from_request', request_id=req.id) }}" style="display:inline;">
                  <button type="submit" class="btn btn-danger" onclick="return confirm('Tem certeza que deseja revogar o convite associado a esta solicitação?');">Revogar Convite</button>
                </form>
              {% else %} {# status == 'negado' #}
                <span style="color:var(--danger)">Negado</span>
                <form method="post" action="{{ url_for('admin_remove_denied_request', request_id=req.id) }}" style="display:inline;">
                  <button type="submit" class="btn btn-ghost" onclick="return confirm('Tem certeza que deseja remover esta solicitação negada?');">Remover</button>
                </form>
              {% endif %}
            </td>
          </tr>
        {% else %}
          <tr><td colspan="6">Nenhuma solicitação de acesso pendente.</td></tr>
        {% endfor %}
      </table>
    </div>

    <div class="card">
      <h3>Convites Enviados</h3>
      <table>
        <tr><th>Data</th><th>Email</th><th>Status</th><th>Token</th><th>Ação</th></tr>
        {% for inv in invites %}
          <tr>
            <td>{{ inv.criado_em.strftime('%d/%m %H:%M') }}</td>
            <td>{{ inv.email }}</td>
            <td>
              {% if inv.status == 'convidado' %}
                <span style="color:orange">{{ inv.status|capitalize }}</span>
              {% elif inv.status == 'ativado' %}
                <span style="color:var(--ok)">{{ inv.status|capitalize }}</span>
              {% else %}
                <span style="color:var(--danger)">{{ inv.status|capitalize }}</span>
              {% endif %}
            </td>
            <td>{{ inv.token }}</td>
            <td>
              {% if inv.status == 'convidado' %}
                <form method="post" action="{{ url_for('admin_revoke_invite', invite_id=inv.id) }}" style="display:inline;">
                  <button type="submit" class="btn btn-danger" onclick="return confirm('Tem certeza que deseja revogar este convite?');">Revogar</button>
                </form>
              {% else %}
                <span class="muted">N/A</span>
              {% endif %}
            </td>
          </tr>
        {% else %}
          <tr><td colspan="5">Nenhum convite enviado.</td></tr>
        {% endfor %}
      </table>
    </div>

    <div class="card">
      <h3>Usuários Cadastrados</h3>
      <table>
        <tr><th>Nome</th><th>Email</th><th>Perfil</th><th>Status</th><th>Ação</th></tr>
        {% for user in users %}
          <tr>
            <td>{{ user.nome }}</td>
            <td>{{ user.email }}</td>
            <td>{{ user.perfil|capitalize }}</td>
            <td>
              {% if user.status == 'ativo' %}
                <span style="color:var(--ok)">{{ user.status|capitalize }}</span>
              {% else %}
                <span style="color:var(--danger)">{{ user.status|capitalize }}</span>
              {% endif %}
            </td>
            <td>
              {% if user.perfil != 'admin' %} {# Não permite bloquear o próprio admin #}
                {% if user.status == 'ativo' %}
                  <form method="post" action="{{ url_for('admin_block_user', user_id=user.id) }}" style="display:inline;">
                    <button type="submit" class="btn btn-danger">Bloquear</button>
                  </form>
                {% else %}
                  <form method="post" action="{{ url_for('admin_unblock_user', user_id=user.id) }}" style="display:inline;">
                    <button type="submit" class="btn btn-ok">Desbloquear</button>
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
    """
    return page(html_content, title="AP360 | Admin", requests=requests, invites=invites, users=users)


@app.route("/admin/approve_request/<int:request_id>", methods=["POST"])
@admin_required
def admin_approve_request(request_id):
    req = AccessRequest.query.get_or_404(request_id)
    if req.status == "pendente":
        # 1. Cria o usuário
        new_user = User(
            nome=req.nome,
            email=req.email,
            cpf=req.cpf,
            telefone=req.telefone,
            segmento=req.segmento,
            cooperativa=req.cooperativa,
            perfil="produtor", # Todos os aprovados são produtores por padrão
            status="inativo" # Inativo até que o usuário defina a senha
        )
        # Senha temporária, será substituída na ativação
        new_user.set_password(secrets.token_urlsafe(16))
        db.session.add(new_user)

        # 2. Cria o convite
        token = secrets.token_urlsafe(32)
        new_invite = AccessInvite(
            email=req.email,
            token=token,
            status="convidado",
            request_id=req.id
        )
        db.session.add(new_invite)

        # 3. Atualiza a solicitação
        req.status = "liberado"
        db.session.commit()

        # TODO: Enviar email com o link de ativação (ex: url_for('activate_account', token=token, _external=True))
        flash(f"Solicitação de {req.email} aprovada. Convite gerado e usuário criado (status inativo). Link de ativação: {url_for('activate_account', token=token, _external=True)}")
    else:
        flash("Solicitação já processada.")
    return redirect(url_for("admin_panel"))


@app.route("/admin/deny_request/<int:request_id>", methods=["POST"])
@admin_required
def admin_deny_request(request_id):
    req = AccessRequest.query.get_or_404(request_id)
    if req.status == "pendente":
        req.status = "negado"
        db.session.commit()
        flash(f"Solicitação de {req.email} negada.")
    else:
        flash("Solicitação já processada.")
    return redirect(url_for("admin_panel"))


@app.route("/admin/remove_denied_request/<int:request_id>", methods=["POST"])
@admin_required
def admin_remove_denied_request(request_id):
    req = AccessRequest.query.get_or_404(request_id)
    if req.status == "negado":
        db.session.delete(req)
        db.session.commit()
        flash(f"Solicitação negada de {req.email} removida.")
    else:
        flash("Apenas solicitações negadas podem ser removidas desta forma.")
    return redirect(url_for("admin_panel"))


@app.route("/admin/revoke_invite/<int:invite_id>", methods=["POST"])
@admin_required
def admin_revoke_invite(invite_id):
    inv = AccessInvite.query.get_or_404(invite_id)
    if inv.status == "convidado":
        inv.status = "revogado"
        # Se houver um usuário associado que ainda não ativou, podemos bloqueá-lo também
        user = User.query.filter_by(email=inv.email, status="inativo").first()
        if user:
            user.status = "bloqueado"

        # Se a solicitação original estava "liberado", voltamos para "negado"
        if inv.request_id:
            req = AccessRequest.query.get(inv.request_id)
            if req and req.status == "liberado":
                req.status = "negado"

        db.session.commit()
        flash(f"Convite para {inv.email} revogado.")
    else:
        flash("Este convite não pode ser revogado (já ativado ou revogado).")
    return redirect(url_for("admin_panel"))


@app.route("/admin/revoke_invite_from_request/<int:request_id>", methods=["POST"])
@admin_required
def admin_revoke_invite_from_request(request_id):
    req = AccessRequest.query.get_or_404(request_id)
    if req.status == "liberado":
        inv = AccessInvite.query.filter_by(request_id=req.id, status="convidado").first()
        if inv:
            inv.status = "revogado"
            user = User.query.filter_by(email=inv.email, status="inativo").first()
            if user:
                user.status = "bloqueado"
            req.status = "negado" # Volta a solicitação para negado
            db.session.commit()
            flash(f"Convite associado à solicitação de {req.email} revogado e solicitação marcada como negada.")
        else:
            flash("Nenhum convite ativo encontrado para esta solicitação.")
    else:
        flash("Esta solicitação não tem um convite ativo para ser revogado.")
    return redirect(url_for("admin_panel"))


@app.route("/admin/block_user/<int:user_id>", methods=["POST"])
@admin_required
def admin_block_user(user_id):
    user = User.query.get_or_404(user_id)
    if user.perfil != "admin":
        user.status = "bloqueado"
        db.session.commit()
        flash(f"Usuário {user.email} bloqueado.")
    else:
        flash("Não é possível bloquear um administrador.")
    return redirect(url_for("admin_panel"))


@app.route("/admin/unblock_user/<int:user_id>", methods=["POST"])
@admin_required
def admin_unblock_user(user_id):
    user = User.query.get_or_404(user_id)
    user.status = "ativo"
    db.session.commit()
    flash(f"Usuário {user.email} desbloqueado.")
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

    fields = AgricultureField.query.filter_by(user_id=current_user.id).order_by(AgricultureField.criado_em.desc()).all()

    html_content = """
    <h2>Módulo de Agricultura</h2>
    <div class="grid">
      <div class="card">
        <h3>Novo Campo de Produção</h3>
        <form method="post" action="{{ url_for('add_field') }}">
          <label>Nome do Campo</label>
          <input name="nome_campo" placeholder="Ex: Fazenda A - Talhão 1" required>
          <label>Cultura</label>
          <input name="cultura" placeholder="Ex: Soja, Milho" required>
          <label>Área (hectares)</label>
          <input type="number" step="0.01" name="area_ha" placeholder="Ex: 50.5" required>
          <label>Data de Plantio</label>
          <input type="date" name="data_plantio" required>
          <label>Data de Colheita Prevista</label>
          <input type="date" name="data_colheita_prevista">
          <label>Produtividade Esperada (ton/ha)</label>
          <input type="number" step="0.01" name="produtividade_esperada_ton_ha" placeholder="Ex: 3.5">
          <label>Observações</label>
          <textarea name="observacoes" placeholder="Informações adicionais sobre o campo"></textarea>
          <button class="btn btn-ok" type="submit">Adicionar Campo</button>
        </form>
      </div>

      <div class="card">
        <h3>Meus Campos de Produção</h3>
        <table>
          <tr><th>Nome do Campo</th><th>Cultura</th><th>Área (ha)</th><th>Plantio</th><th>Status</th><th>Ações</th></tr>
          {% for field in fields %}
            <tr>
              <td>{{ field.nome_campo }}</td>
              <td>{{ field.cultura }}</td>
              <td>{{ "%.2f"|format(field.area_ha) }}</td>
              <td>{{ field.data_plantio.strftime('%d/%m/%Y') }}</td>
              <td>{{ field.status|capitalize }}</td>
              <td>
                <a class="btn btn-ghost" href="{{ url_for('detalhes_campo_ao_vivo', field_id=field.id) }}">Detalhes</a>
                <a class="btn btn-ghost" href="{{ url_for('editar_field', field_id=field.id) }}">Editar</a>
                <form method="post" action="{{ url_for('excluir_field', field_id=field.id) }}" style="display:inline;">
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
def add_field():
    if current_user.segmento != "agricultura":
        flash("Seu perfil não tem acesso ao módulo de agricultura.")
        return redirect(url_for("dashboard"))
    try:
        nome_campo = request.form.get("nome_campo").strip()
        if AgricultureField.query.filter_by(user_id=current_user.id, nome_campo=nome_campo).first():
            flash("Já existe um campo com este nome.")
            return redirect(url_for("agricultura_modulo"))

        new_field = AgricultureField(
            user_id=current_user.id,
            nome_campo=nome_campo,
            cultura=request.form.get("cultura").strip(),
            area_ha=float(request.form.get("area_ha")),
            data_plantio=datetime.strptime(request.form.get("data_plantio"), "%Y-%m-%d").date(),
            data_colheita_prevista=datetime.strptime(request.form.get("data_colheita_prevista"), "%Y-%m-%d").date() if request.form.get("data_colheita_prevista") else None,
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


@app.route("/agricultura/editar/<int:field_id>", methods=["GET", "POST"])
@login_required
def editar_field(field_id):
    if current_user.segmento != "agricultura":
        flash("Seu perfil não tem acesso ao módulo de agricultura.")
        return redirect(url_for("dashboard"))

    field = AgricultureField.query.filter_by(id=field_id, user_id=current_user.id).first_or_404()

    if request.method == "POST":
        try:
            new_nome_campo = request.form.get("nome_campo").strip()
            if AgricultureField.query.filter(AgricultureField.user_id == current_user.id, AgricultureField.nome_campo == new_nome_campo, AgricultureField.id != field_id).first():
                flash("Já existe outro campo com este nome.")
                return redirect(url_for("editar_field", field_id=field.id))

            field.nome_campo = new_nome_campo
            field.cultura = request.form.get("cultura").strip()
            field.area_ha = float(request.form.get("area_ha"))
            field.data_plantio = datetime.strptime(request.form.get("data_plantio"), "%Y-%m-%d").date()
            field.data_colheita_prevista = datetime.strptime(request.form.get("data_colheita_prevista"), "%Y-%m-%d").date() if request.form.get("data_colheita_prevista") else None
            field.produtividade_esperada_ton_ha = float(request.form.get("produtividade_esperada_ton_ha", 0) or 0)
            field.observacoes = request.form.get("observacoes", "").strip()
            field.status = request.form.get("status", "plantado")

            db.session.commit()
            flash("Campo de produção atualizado com sucesso!")
            return redirect(url_for("agricultura_modulo"))
        except ValueError as e:
            flash(f"Erro nos dados de entrada: {e}. Verifique se todos os campos numéricos e de data estão corretos.")
        except Exception as e:
            flash(f"Ocorreu um erro inesperado: {e}")

    html_content = """
    <h2>Editar Campo de Produção</h2>
    <div class="card" style="max-width:700px;margin:0 auto">
      <form method="post">
        <label>Nome do Campo</label>
        <input name="nome_campo" value="{{ field.nome_campo }}" required>
        <label>Cultura</label>
        <input name="cultura" value="{{ field.cultura }}" required>
        <label>Área (hectares)</label>
        <input type="number" step="0.01" name="area_ha" value="{{ field.area_ha }}" required>
        <label>Data de Plantio</label>
        <input type="date" name="data_plantio" value="{{ field.data_plantio.strftime('%Y-%m-%d') }}" required>
        <label>Data de Colheita Prevista</label>
        <input type="date" name="data_colheita_prevista" value="{{ field.data_colheita_prevista.strftime('%Y-%m-%d') if field.data_colheita_prevista else '' }}">
        <label>Produtividade Esperada (ton/ha)</label>
        <input type="number" step="0.01" name="produtividade_esperada_ton_ha" value="{{ field.produtividade_esperada_ton_ha }}">
        <label>Status</label>
        <select name="status">
          <option value="plantado" {% if field.status == 'plantado' %}selected{% endif %}>Plantado</option>
          <option value="crescendo" {% if field.status == 'crescendo' %}selected{% endif %}>Crescendo</option>
          <option value="colhendo" {% if field.status == 'colhendo' %}selected{% endif %}>Colhendo</option>
          <option value="colhido" {% if field.status == 'colhido' %}selected{% endif %}>Colhido</option>
        </select>
        <label>Observações</label>
        <textarea name="observacoes">{{ field.observacoes or '' }}</textarea>
        <button class="btn btn-ok" type="submit">Salvar Alterações</button>
        <a class="btn btn-ghost" href="{{ url_for('agricultura_modulo') }}">Cancelar</a>
      </form>
    </div>
    """
    return page(html_content, title="AP360 | Editar Campo", field=field)


@app.route("/agricultura/excluir/<int:field_id>", methods=["POST"])
@login_required
def excluir_field(field_id):
    field = AgricultureField.query.filter_by(id=field_id, user_id=current_user.id).first_or_404()
    AgricultureDailyRecord.query.filter_by(field_id=field.id).delete() # Exclui registros diários do campo
    db.session.delete(field)
    db.session.commit()
    flash("Campo de produção excluído com sucesso!")
    return redirect(url_for("agricultura_modulo"))


@app.route("/agricultura/campo/<int:field_id>", methods=["GET", "POST"])
@login_required
def detalhes_campo_ao_vivo(field_id):
    if current_user.segmento != "agricultura":
        flash("Seu perfil não tem acesso ao módulo de agricultura.")
        return redirect(url_for("dashboard"))

    field = AgricultureField.query.filter_by(id=field_id, user_id=current_user.id).first_or_404()
    daily_records = AgricultureDailyRecord.query.filter_by(field_id=field.id).order_by(AgricultureDailyRecord.data_registro.asc()).all()

    if request.method == "POST":
        form_type = request.form.get("form_type")
        if form_type == "novo_registro_diario_campo":
            try:
                data_registro = datetime.strptime(request.form.get("data_registro"), "%Y-%m-%d").date()
                if AgricultureDailyRecord.query.filter_by(field_id=field.id, data_registro=data_registro).first():
                    flash("Já existe um registro para esta data neste campo.")
                    return redirect(url_for("detalhes_campo_ao_vivo", field_id=field.id))

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
            return redirect(url_for("detalhes_campo_ao_vivo", field_id=field.id))

    # Preparar dados para gráficos
    chart_labels = [r.data_registro.strftime('%d/%m') for r in daily_records]
    chart_produtividade_parcial = [r.produtividade_parcial_ton_ha for r in daily_records]
    chart_chuva = [r.chuva_mm for r in daily_records]
    chart_temperatura = [r.temperatura_c for r in daily_records]

    # Simulação Agrosim (exemplo simples)
    agrosim_data = []
    if field.data_plantio and field.data_colheita_prevista:
        dias_plantio_colheita = (field.data_colheita_prevista - field.data_plantio).days
        if dias_plantio_colheita > 0:
            for i in range(dias_plantio_colheita + 1):
                data_sim = field.data_plantio + timedelta(days=i)
                # Simulação linear simples de crescimento
                produtividade_sim = (field.produtividade_esperada_ton_ha / dias_plantio_colheita) * i
                agrosim_data.append({"data": data_sim.strftime('%d/%m'), "produtividade": round(produtividade_sim, 2)})

    agrosim_labels = [d["data"] for d in agrosim_data]
    agrosim_produtividade = [d["produtividade"] for d in agrosim_data]


    html_content = """
    <h2>Detalhes do Campo: {{ field.nome_campo }} ({{ field.cultura|capitalize }})</h2>
    <div class="card">
      <p><b>Área:</b> {{ "%.2f"|format(field.area_ha) }} ha</p>
      <p><b>Data de Plantio:</b> {{ field.data_plantio.strftime('%d/%m/%Y') }}</p>
      <p><b>Colheita Prevista:</b> {{ field.data_colheita_prevista.strftime('%d/%m/%Y') if field.data_colheita_prevista else 'N/A' }}</p>
      <p><b>Produtividade Esperada:</b> {{ "%.2f"|format(field.produtividade_esperada_ton_ha) }} ton/ha</p>
      <p><b>Status:</b> {{ field.status|capitalize }}</p>
      <p><b>Observações:</b> {{ field.observacoes or '-' }}</p>
      <a class="btn btn-ghost" href="{{ url_for('agricultura_modulo') }}">Voltar para Agricultura</a>
    </div>

    <div class="grid">
      <div class="card">
        <h3>Adicionar Registro Diário</h3>
        <form method="post">
          <input type="hidden" name="form_type" value="novo_registro_diario_campo">
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
        <h3>Gráficos de Acompanhamento</h3>
        <div class="grid">
          {% if chart_labels %}
          <div>
            <h4>Produtividade Parcial (ton/ha)</h4>
            <canvas id="produtividadeParcialChart" height="150"></canvas>
          </div>
          <div>
            <h4>Chuva (mm)</h4>
            <canvas id="chuvaChart" height="150"></canvas>
          </div>
          <div>
            <h4>Temperatura (°C)</h4>
            <canvas id="temperaturaChart" height="150"></canvas>
          </div>
          {% else %}
            <p class="muted">Adicione registros diários para ver os gráficos.</p>
          {% endif %}
        </div>
        <script>
          const chartLabels = {{ chart_labels | tojson }};
          const chartProdutividadeParcial = {{ chart_produtividade_parcial | tojson }};
          const chartChuva = {{ chart_chuva | tojson }};
          const chartTemperatura = {{ chart_temperatura | tojson }};

          if (chartLabels.length > 0) {
            new Chart(document.getElementById("produtividadeParcialChart"), {
              type: "line",
              data: { labels: chartLabels, datasets: [{ label: "Produtividade Parcial (ton/ha)", data: chartProdutividadeParcial, borderColor: 'rgba(59, 185, 255, 1)', tension: 0.2 }] },
              options: { responsive: true, scales: { y: { beginAtZero: true } } }
            });
            new Chart(document.getElementById("chuvaChart"), {
              type: "bar",
              data: { labels: chartLabels, datasets: [{ label: "Chuva (mm)", data: chartChuva, backgroundColor: 'rgba(70, 221, 152, 0.5)' }] },
              options: { responsive: true, scales: { y: { beginAtZero: true } } }
            });
            new Chart(document.getElementById("temperaturaChart"), {
              type: "line",
              data: { labels: chartLabels, datasets: [{ label: "Temperatura (°C)", data: chartTemperatura, borderColor: 'rgba(255, 99, 132, 1)', tension: 0.2 }] },
              options: { responsive: true, scales: { y: { beginAtZero: true } } }
            });
          }
        </script>
      </div>
    </div>

    <div class="card">
      <h3>Agrosim - Simulação de Produtividade</h3>
      {% if agrosim_labels %}
      <canvas id="agrosimChart" height="100"></canvas>
      <script>
        const agrosimLabels = {{ agrosim_labels | tojson }};
        const agrosimProdutividade = {{ agrosim_produtividade | tojson }};
        new Chart(document.getElementById("agrosimChart"), {
          type: "line",
          data: { labels: agrosimLabels, datasets: [{ label: "Produtividade Simulada (ton/ha)", data: agrosimProdutividade, borderColor: 'rgba(255, 206, 86, 1)', tension: 0.2 }] },
          options: { responsive: true, scales: { y: { beginAtZero: true } } }
        });
      </script>
      {% else %}
        <p class="muted">Preencha as datas de plantio e colheita prevista para ver a simulação.</p>
      {% endif %}
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
              <form method="post" action="{{ url_for('excluir_registro_diario_campo', record_id=record.id) }}" style="display:inline;">
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
    return page(html_content, title="AP360 | Detalhes Campo", field=field,
                chart_labels=chart_labels, chart_produtividade_parcial=chart_produtividade_parcial,
                chart_chuva=chart_chuva, chart_temperatura=chart_temperatura,
                agrosim_labels=agrosim_labels, agrosim_produtividade=agrosim_produtividade)


@app.route("/agricultura/campo/registro_diario/excluir/<int:record_id>", methods=["POST"])
@login_required
def excluir_registro_diario_campo(record_id):
    record = AgricultureDailyRecord.query.get_or_404(record_id)
    field_id = record.field_id
    field = AgricultureField.query.filter_by(id=field_id, user_id=current_user.id).first_or_404() # Garante que o usuário é o dono do campo
    db.session.delete(record)
    db.session.commit()
    flash("Registro diário excluído com sucesso!")
    return redirect(url_for("detalhes_campo_ao_vivo", field_id=field_id))


# =========================================================
# AVICULTURA / SUINOCULTURA (Módulo de Lotes Genérico)
# =========================================================
@app.route("/<string:cadeia>")
@login_required
def modulo_lotes(cadeia):
    if cadeia not in ["avicultura", "suinocultura"]:
        abort(404) # Retorna 404 se a cadeia não for reconhecida

    if current_user.segmento != cadeia:
        flash(f"Seu perfil não tem acesso ao módulo de {cadeia}.")
        return redirect(url_for("dashboard"))

    batches = Batch.query.filter_by(user_id=current_user.id, cadeia=cadeia).order_by(Batch.criado_em.desc()).all()

    html_content = f"""
    <h2>Módulo de {cadeia.capitalize()}</h2>
    <div class="grid">
      <div class="card">
        <h3>Novo Lote de {cadeia.capitalize()}</h3>
        <form method="post" action="{{ url_for('add_lote', cadeia=cadeia) }}">
          <label>Estrutura</label>
          <input name="estrutura" placeholder="Ex: Galpão 1, Baia 5" required>
          <label>Lote</label>
          <input name="lote" placeholder="Ex: Lote 2024-01, Lote de Verão" required>

          <label>Data e Hora de Alojamento</label>
          <input type="date" name="data_alojamento" value="{{ datetime.now(BRASILIA_TZ).strftime('%Y-%m-%d') }}" required>
          <input type="time" name="hora_alojamento" value="{{ datetime.now(BRASILIA_TZ).strftime('%H:%M') }}" required>

          <label>Data e Hora de Carregamento/Abate</label>
          <input type="date" name="data_carregamento" value="{{ datetime.now(BRASILIA_TZ).strftime('%Y-%m-%d') }}" required>
          <input type="time" name="hora_carregamento" value="{{ datetime.now(BRASILIA_TZ).strftime('%H:%M') }}" required>

          <label>Peso inicial médio por animal (kg)</label>
          <input type="number" step="0.0001" name="peso_inicial" placeholder="Ex: 0.04 (pintinho), 15.0 (leitão)" required>
          <label>Peso final médio por animal (kg)</label>
          <input type="number" step="0.0001" name="peso_final" placeholder="Ex: 2.85 (frango), 120.0 (suíno)" required>

          <label>Ração TOTAL consumida pelo lote (kg)</label>
          <input type="number" step="0.0001" name="racao_total_kg" placeholder="Ex: 4800.0 (para 1000 aves), 24000.0 (para 200 suínos)" required>
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
            <input type="number" step="0.01" name="peso_vivo_medio" placeholder="Ex: 120.0" required>
            <label>Peso carcaça médio (kg/cab)</label>
            <input type="number" step="0.01" name="peso_carcaca_medio" placeholder="Ex: 90.0" required>
            <label>% carne magra</label>
            <input type="number" step="0.01" name="carne_magra_pct" placeholder="Ex: 56.5" required>
          {% endif %}

          <button class="btn btn-ok" type="submit">Adicionar Lote</button>
        </form>
      </div>

      <div class="card">
        <h3>Meus Lotes de {{ cadeia|capitalize }}</h3>
        <table>
          <tr><th>Estrutura</th><th>Lote</th><th>Dias</th><th>GPD</th><th>CA</th><th>CAA</th><th>IEP/Índice</th><th>Bonificação</th><th>Ações</th></tr>
          {% for batch in batches %}
            <tr>
              <td>{{ batch.estrutura }}</td>
              <td>{{ batch.lote }}</td>
              <td>{{ "%.2f"|format(batch.dias) }}</td>
              <td>{{ "%.4f"|format(batch.gpd) }}</td>
              <td>{{ "%.4f"|format(batch.ca) }}</td>
              <td>{{ "%.4f"|format(batch.ca_ajustada) }}</td>
              <td>{% if cadeia == 'avicultura' %}{{ "%.2f"|format(batch.iep) }}{% else %}{{ "%.2f"|format(batch.indice_lote) }}{% endif %}</td>
              <td>R$ {{ "%.2f"|format(batch.bonificacao) }}</td>
              <td>
                <a class="btn btn-ghost" href="{{ url_for('detalhes_lote_ao_vivo', cadeia=cadeia, batch_id=batch.id) }}">Acompanhar</a>
                <a class="btn btn-ghost" href="{{ url_for('editar_lote', cadeia=cadeia, batch_id=batch.id) }}">Editar</a>
                <form method="post" action="{{ url_for('excluir_lote', cadeia=cadeia, batch_id=batch.id) }}" style="display:inline;">
                  <button type="submit" class="btn btn-danger" onclick="return confirm('Tem certeza que deseja excluir este lote e todos os seus registros?');">Excluir</button>
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
    return page(html_content, title=f"AP360 | {cadeia.capitalize()}", batches=batches, cadeia=cadeia, datetime=datetime, BRASILIA_TZ=BRASILIA_TZ)


@app.route("/<string:cadeia>/add_lote", methods=["POST"])
@login_required
def add_lote(cadeia):
    if cadeia not in ["avicultura", "suinocultura"]:
        abort(404)

    if current_user.segmento != cadeia:
        flash(f"Seu perfil não tem acesso ao módulo de {cadeia}.")
        return redirect(url_for("dashboard"))

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

        # Cálculos básicos
        gpd = calc_gpd(peso_inicial, peso_final, dias)
        ca = calc_ca(racao_total_kg, peso_inicial, peso_final, animais_final)
        viabilidade_pct = calc_viabilidade(animais_iniciais, animais_final)
        mortalidade_pct = calc_mortalidade(animais_iniciais, animais_final)

        # Benchmarks
        meta_gpd, meta_ca, bonus_base = get_effective_benchmark(cadeia, current_user.cooperativa, current_user)

        ca_ajustada = ca # Valor padrão, será ajustado se for avicultura
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
                    peso_real=peso_final, # Corrigido para usar peso_final
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
            gpd_coop_ref=float(request.form.get("gpd_coop_ref", 0) or 0),
            ca_coop_ref=float(request.form.get("ca_coop_ref", 0) or 0)
        )
        db.session.add(new_batch)
        db.session.commit()
        flash(f"Lote de {cadeia} adicionado com sucesso!")
    except ValueError as e:
        flash(f"Erro nos dados de entrada: {e}. Verifique se todos os campos numéricos e de data/hora estão corretos.")
    except Exception as e:
        flash(f"Ocorreu um erro inesperado: {e}")
    return redirect(url_for("modulo_lotes", cadeia=cadeia))


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
        <a class="btn btn-ghost" href="{{ url_for('modulo_lotes', cadeia=cadeia) }}">Cancelar</a>
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
    return redirect(url_for("modulo_lotes", cadeia=cadeia))


@app.route("/<string:cadeia>/lote/<int:batch_id>", methods=["GET", "POST"])
@login_required
def detalhes_lote_ao_vivo(cadeia, batch_id):
    if cadeia not in ["avicultura", "suinocultura"]:
        abort(404)

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
      <p><b>Dias de Alojamento:</b> {{ "%.2f"|format(batch.dias) }}</p>
      <p><b>Peso Inicial Médio:</b> {{ "%.4f"|format(batch.peso_inicial) }} kg</p>
      <p><b>Peso Final Médio:</b> {{ "%.4f"|format(batch.peso_final) }} kg</p>
      <p><b>Ração Total:</b> {{ "%.4f"|format(batch.racao_total_kg) }} kg</p>
      <p><b>Animais Iniciais:</b> {{ batch.animais_iniciais }}</p>
      <p><b>Animais Finais:</b> {{ batch.animais_final }}</p>
      <p><b>GPD:</b> {{ "%.4f"|format(batch.gpd) }}</p>
      <p><b>CA:</b> {{ "%.4f"|format(batch.ca) }}</p>
      <p><b>CA Ajustada:</b> {{ "%.4f"|format(batch.ca_ajustada) }}</p>
      <p><b>Viabilidade:</b> {{ "%.2f"|format(batch.viabilidade_pct) }}%</p>
      <p><b>Mortalidade:</b> {{ "%.2f"|format(batch.mortalidade_pct) }}%</p>
      <p><b>IEP/Índice:</b> {% if cadeia == 'avicultura' %}{{ "%.2f"|format(batch.iep) }}{% else %}{{ "%.2f"|format(batch.indice_lote) }}{% endif %}</p>
      <p><b>Bonificação:</b> R$ {{ "%.2f"|format(batch.bonificacao) }}</p>
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
              <form method="post" action="{{ url_for('excluir_registro_diario_lote', cadeia=cadeia, record_id=record.id) }}" style="display:inline;">
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