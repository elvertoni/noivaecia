"""
Normalize customer city names: fix abbreviations, typos, encoding artifacts,
and compound garbage values (phone numbers, RG numbers, names mixed into city field).

Usage:
    python manage.py normalize_cities             # apply all normalizations
    python manage.py normalize_cities --dry-run   # preview without saving
"""

import re
import unicodedata

from django.core.management.base import BaseCommand
from django.db import transaction

from customers.models import Customer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _strip_diacritics(s: str) -> str:
    """'Ribeirão' → 'Ribeirao', 'Andirá' → 'Andira'."""
    return ''.join(
        c for c in unicodedata.normalize('NFD', s)
        if unicodedata.category(c) != 'Mn'
    )


def _fix_encoding(s: str) -> str:
    """Fix Latin-1/UTF-8 confusion from legacy Access import.

    Example: 'RibeirÃ£o' → 'Ribeirão' (UTF-8 bytes mis-decoded as Latin-1).
    """
    try:
        return s.encode('latin-1').decode('utf-8')
    except (UnicodeEncodeError, UnicodeDecodeError):
        return s


def _key(raw: str) -> str:
    """Normalize for rule matching: encoding fix → strip diacritics → strip → uppercase."""
    return _strip_diacritics(_fix_encoding(raw.strip())).upper()


# ---------------------------------------------------------------------------
# Rules: list of (compiled_regex, canonical_city_name)
#
# The regex is matched against _key(city) using re.match (anchored at start).
# First match wins. Rules ordered from most specific to most general.
# ---------------------------------------------------------------------------
def _r(pattern: str) -> re.Pattern:
    return re.compile(pattern)


RULES: list[tuple[re.Pattern, str]] = [

    # ── Bandeirantes ──────────────────────────────────────────────────────────
    # Full name variants and typos
    (_r(r'^BANDEIRANTES($|[\s,./0-9PR-])'), 'Bandeirantes'),
    (_r(r'^BANDEIRANTESS'), 'Bandeirantes'),
    (_r(r'^BANDEIRANTRES'), 'Bandeirantes'),
    (_r(r'^BANDEIRANRTES'), 'Bandeirantes'),
    (_r(r'^BANDEIRANETS'), 'Bandeirantes'),
    (_r(r'^BANDEIARNTES'), 'Bandeirantes'),
    (_r(r'^BANDEITANTES'), 'Bandeirantes'),
    (_r(r'^BANDEIRANES$'), 'Bandeirantes'),
    (_r(r'^BANDEIRANTE$'), 'Bandeirantes'),
    (_r(r'^BANDEIRATES'), 'Bandeirantes'),
    (_r(r'^BANDEIRNTES'), 'Bandeirantes'),
    (_r(r'^BANDERANTES'), 'Bandeirantes'),
    (_r(r'^BANDIRANTES'), 'Bandeirantes'),
    (_r(r'^BAMDEIRANTES'), 'Bandeirantes'),
    (_r(r'^BNDEIRANTES'), 'Bandeirantes'),
    (_r(r'^BNDEIRANT'), 'Bandeirantes'),
    (_r(r'^BANDIERANTES'), 'Bandeirantes'),
    (_r(r'^BANDEIREANTES'), 'Bandeirantes'),
    (_r(r'^BANDEIRASTES'), 'Bandeirantes'),
    (_r(r'^BANDEIRANTES0'), 'Bandeirantes'),
    (_r(r'^BANDIERANTE'), 'Bandeirantes'),
    (_r(r'^BAND$'), 'Bandeirantes'),
    # BTES family — prefix: anything starting with these is Bandeirantes
    (_r(r'^BTES'), 'Bandeirantes'),
    (_r(r'^BTS\b'), 'Bandeirantes'),
    (_r(r'^BTRES'), 'Bandeirantes'),
    (_r(r'^BTTES'), 'Bandeirantes'),
    (_r(r'^BTTS'), 'Bandeirantes'),
    (_r(r'^BTRS'), 'Bandeirantes'),
    (_r(r'^BTAS$'), 'Bandeirantes'),
    (_r(r'^BRTES'), 'Bandeirantes'),
    (_r(r'^BRTS$'), 'Bandeirantes'),
    (_r(r'^BETS$'), 'Bandeirantes'),
    (_r(r'^BYES$'), 'Bandeirantes'),
    (_r(r'^BRES$'), 'Bandeirantes'),
    (_r(r'^BANTES'), 'Bandeirantes'),
    (_r(r'^BANTS$'), 'Bandeirantes'),
    (_r(r'^BNTES'), 'Bandeirantes'),
    (_r(r'^BNTS$'), 'Bandeirantes'),
    (_r(r'^BEST$'), 'Bandeirantes'),
    (_r(r'^BTETS'), 'Bandeirantes'),
    (_r(r'^BTEA'), 'Bandeirantes'),
    (_r(r'^BTSS'), 'Bandeirantes'),
    (_r(r'^BTESS'), 'Bandeirantes'),
    (_r(r'^VTES$'), 'Bandeirantes'),
    (_r(r'^NTES$'), 'Bandeirantes'),
    (_r(r'^TBES$'), 'Bandeirantes'),
    (_r(r'^NBTES'), 'Bandeirantes'),
    (_r(r'^2BTES'), 'Bandeirantes'),
    (_r(r'^\d+BTES'), 'Bandeirantes'),
    (_r(r'^\.BTES'), 'Bandeirantes'),
    (_r(r'^0TES($|\s)'), 'Bandeirantes'),
    (_r(r'^BTESD'), 'Bandeirantes'),
    (_r(r'^BTESW'), 'Bandeirantes'),
    (_r(r'^BTE\s+(CELU|RG\d)'), 'Bandeirantes'),
    (_r(r'^BTE$'), 'Bandeirantes'),
    (_r(r'^BES$'), 'Bandeirantes'),
    (_r(r'^BTEA'), 'Bandeirantes'),
    (_r(r'^BTS\d'), 'Bandeirantes'),
    (_r(r'^TES$'), 'Bandeirantes'),

    # ── Itambaracá ────────────────────────────────────────────────────────────
    (_r(r'^ITCA'), 'Itambaracá'),
    (_r(r'^ITMCA'), 'Itambaracá'),
    (_r(r'^ITAMBARACA'), 'Itambaracá'),
    (_r(r'^ITANBARACA'), 'Itambaracá'),
    (_r(r'^ITANBARA$'), 'Itambaracá'),
    (_r(r'^ATANBARACA'), 'Itambaracá'),
    (_r(r'^ITABARACA'), 'Itambaracá'),
    (_r(r'^ITBACA$'), 'Itambaracá'),
    (_r(r'^ITBCA'), 'Itambaracá'),
    (_r(r'^ITBANCA'), 'Itambaracá'),
    (_r(r'^ITBRACA'), 'Itambaracá'),
    (_r(r'^ITMBCA'), 'Itambaracá'),
    (_r(r'^ITMBARACA'), 'Itambaracá'),
    (_r(r'^ITMBC'), 'Itambaracá'),
    (_r(r'^ITAMCA'), 'Itambaracá'),
    (_r(r'^ITANCA'), 'Itambaracá'),
    (_r(r'^ITBC$'), 'Itambaracá'),
    (_r(r'^ITC$'), 'Itambaracá'),
    (_r(r'^ITB$'), 'Itambaracá'),
    (_r(r'^INTCA'), 'Itambaracá'),
    (_r(r'^UTCA$'), 'Itambaracá'),
    (_r(r'^OTCA$'), 'Itambaracá'),
    (_r(r'^RTCA$'), 'Itambaracá'),
    (_r(r'^ITAC$'), 'Itambaracá'),
    (_r(r'^ITACS$'), 'Itambaracá'),
    (_r(r'^ITCS$'), 'Itambaracá'),
    (_r(r'^ITEN$'), 'Itambaracá'),
    (_r(r'^1TAMBARACA'), 'Itambaracá'),
    (_r(r'^\.ITCA'), 'Itambaracá'),
    (_r(r'^ITACA$'), 'Itambaracá'),
    (_r(r'^TCA$'), 'Itambaracá'),
    (_r(r'^ITCAA'), 'Itambaracá'),

    # ── Andirá ────────────────────────────────────────────────────────────────
    (_r(r'^ANDIRA'), 'Andirá'),
    (_r(r'^ANDIR$'), 'Andirá'),
    (_r(r'^ANDITA$'), 'Andirá'),
    (_r(r'^ADIRA$'), 'Andirá'),
    (_r(r'^NDIRA$'), 'Andirá'),

    # ── Abatiá ────────────────────────────────────────────────────────────────
    (_r(r'^ABATIA'), 'Abatiá'),
    (_r(r'^ABTIA$'), 'Abatiá'),

    # ── Santa Amélia ──────────────────────────────────────────────────────────
    (_r(r'^SANTA AMELIA'), 'Santa Amélia'),
    (_r(r'^SANTA  AMELIA'), 'Santa Amélia'),
    (_r(r'^STA AMELIA'), 'Santa Amélia'),
    (_r(r'^S AMELIA$'), 'Santa Amélia'),
    (_r(r'^SANTA AMEMELIA'), 'Santa Amélia'),
    (_r(r'^SANTA MELIA$'), 'Santa Amélia'),
    (_r(r'^SNTA AMELIA'), 'Santa Amélia'),
    (_r(r'^ANTONIO SANTA AMELIA'), 'Santa Amélia'),

    # ── Santa Mariana ─────────────────────────────────────────────────────────
    (_r(r'^SANTA MARIANA'), 'Santa Mariana'),
    (_r(r'^SANTA  MARIANA'), 'Santa Mariana'),
    (_r(r'^STA MARIANA'), 'Santa Mariana'),
    (_r(r'^SNTA MARIANA'), 'Santa Mariana'),
    (_r(r'^SAMTA MARIANA'), 'Santa Mariana'),
    (_r(r'^SANTA MARIAN$'), 'Santa Mariana'),
    (_r(r'^DANTA MARIANA'), 'Santa Mariana'),
    (_r(r'^D SANTA MARIANA'), 'Santa Mariana'),
    (_r(r'^DISTRITO DE SANTA MA'), 'Santa Mariana'),
    (_r(r'^DISTRITO PANEMA'), 'Santa Mariana'),
    (_r(r'^DIST PANAEMA'), 'Santa Mariana'),
    (_r(r'^PANEMA'), 'Santa Mariana'),   # Panema = district of Santa Mariana

    # ── Cornélio Procópio ─────────────────────────────────────────────────────
    (_r(r'^CORNELIO'), 'Cornélio Procópio'),
    (_r(r'^CONELIO'), 'Cornélio Procópio'),
    (_r(r'^CORNELIUO'), 'Cornélio Procópio'),
    (_r(r'^COR PROCOPIO'), 'Cornélio Procópio'),
    (_r(r'^C PROCOPIO'), 'Cornélio Procópio'),
    (_r(r'^RIO\s+CORNELIO'), 'Cornélio Procópio'),

    # ── Cambará ───────────────────────────────────────────────────────────────
    (_r(r'^CAMBARA'), 'Cambará'),
    (_r(r'^CAMBARRA'), 'Cambará'),
    (_r(r'^CANBARA'), 'Cambará'),

    # ── Ribeirão do Pinhal ────────────────────────────────────────────────────
    (_r(r'^RIBEIRAO DO PINHAL'), 'Ribeirão do Pinhal'),
    (_r(r'^RIBEIRAO DO PINHA$'), 'Ribeirão do Pinhal'),
    (_r(r'^RIBEIRAO DE PINHAL'), 'Ribeirão do Pinhal'),
    (_r(r'^RIBEIRAO DO PINAL$'), 'Ribeirão do Pinhal'),
    (_r(r'^RIBEIRAO PINHAL'), 'Ribeirão do Pinhal'),
    (_r(r'^RIBAIRAO DO PINHAL'), 'Ribeirão do Pinhal'),
    (_r(r'^RIBAIRAO PINHAL'), 'Ribeirão do Pinhal'),
    (_r(r'^RIBERAO DO PINHAL'), 'Ribeirão do Pinhal'),
    (_r(r'^RIBEIRA DO PINHAL'), 'Ribeirão do Pinhal'),
    (_r(r'^RIEBIRAO DO PINHAL'), 'Ribeirão do Pinhal'),
    (_r(r'^RIBEIRAL DO PINHAL'), 'Ribeirão do Pinhal'),
    (_r(r'^RIBEIAO DO PINHAL'), 'Ribeirão do Pinhal'),
    (_r(r'^BIBEIRAO PINHAL'), 'Ribeirão do Pinhal'),
    (_r(r'^RIB DO PINHAL'), 'Ribeirão do Pinhal'),
    (_r(r'^RI DO PINHAL'), 'Ribeirão do Pinhal'),
    (_r(r'^R DO PINHAL'), 'Ribeirão do Pinhal'),
    (_r(r'^R DI PINHAL'), 'Ribeirão do Pinhal'),
    (_r(r'^R DE PINHAL'), 'Ribeirão do Pinhal'),
    (_r(r'^RIB PINHAL'), 'Ribeirão do Pinhal'),
    (_r(r'^R PINHAL$'), 'Ribeirão do Pinhal'),
    (_r(r'^REIRAO PINHAL'), 'Ribeirão do Pinhal'),
    (_r(r'^R DO PINHAO$'), 'Ribeirão do Pinhal'),

    # ── Barra do Jacaré ───────────────────────────────────────────────────────
    (_r(r'^BARRA DO JACARE'), 'Barra do Jacaré'),
    (_r(r'^BARRA DO JACARRE'), 'Barra do Jacaré'),
    (_r(r'^BARRA DO JAC$'), 'Barra do Jacaré'),
    (_r(r'^BARRA DI JACARE'), 'Barra do Jacaré'),
    (_r(r'^B DO JACARA$'), 'Barra do Jacaré'),
    (_r(r'^BARRA$'), 'Barra do Jacaré'),

    # ── Jundiaí do Sul ────────────────────────────────────────────────────────
    (_r(r'^JUNDIAI DO SUL'), 'Jundiaí do Sul'),
    (_r(r'^JUNDIAI DO SU$'), 'Jundiaí do Sul'),
    (_r(r'^JUDIAI DO SUL'), 'Jundiaí do Sul'),

    # ── Nova Fátima ───────────────────────────────────────────────────────────
    (_r(r'^NOVA FATIMA'), 'Nova Fátima'),
    (_r(r'^N FATIMA$'), 'Nova Fátima'),

    # ── Santo Antônio da Platina ──────────────────────────────────────────────
    (_r(r'^STO ANTONIO DA PLATINA'), 'Santo Antônio da Platina'),
    (_r(r'^SANTO ANTONIO\s+DA PLATINA'), 'Santo Antônio da Platina'),
    (_r(r'^SANTO ANTONIO DA PLATINA'), 'Santo Antônio da Platina'),
    (_r(r'^SANTO  ANTONIO DA PLATINA'), 'Santo Antônio da Platina'),
    (_r(r'^SANTO ANTONIA DA PLATINA'), 'Santo Antônio da Platina'),
    (_r(r'^SANTO ANTONIO PLATINA'), 'Santo Antônio da Platina'),
    (_r(r'^SANTO ANTONIO PLATIN$'), 'Santo Antônio da Platina'),
    (_r(r'^SANTO ANTONIO P$'), 'Santo Antônio da Platina'),
    (_r(r'^SANTO A PLATINA'), 'Santo Antônio da Platina'),
    (_r(r'^SANTO AN$'), 'Santo Antônio da Platina'),
    (_r(r'^SANTO ANTONIO$'), 'Santo Antônio da Platina'),
    # Note: 'SANTO ANTONIO PARAIS' kept separate — may be Santo Antônio do Paraíso (different city)

    # ── Londrina ──────────────────────────────────────────────────────────────
    (_r(r'^LONDRINA'), 'Londrina'),
    (_r(r'^LOMDRINA'), 'Londrina'),

    # ── Leópolis ──────────────────────────────────────────────────────────────
    (_r(r'^LEOPOLIS'), 'Leópolis'),

    # ── Wenceslau Braz ────────────────────────────────────────────────────────
    (_r(r'^WENCESLAU BRAZ'), 'Wenceslau Braz'),

    # ── Congonhinhas ──────────────────────────────────────────────────────────
    (_r(r'^CONGONHINHAS'), 'Congonhinhas'),
    (_r(r'^GONGOINHAS'), 'Congonhinhas'),

    # ── Siqueira Campos ───────────────────────────────────────────────────────
    (_r(r'^SIQUEIRA CAMPOS'), 'Siqueira Campos'),
    (_r(r'^SIQUERA CAMPOS'), 'Siqueira Campos'),

    # ── Carlópolis ────────────────────────────────────────────────────────────
    (_r(r'^CARLOPOLIS'), 'Carlópolis'),
    (_r(r'^CARLOPOLES'), 'Carlópolis'),

    # ── Joaquim Távora ────────────────────────────────────────────────────────
    (_r(r'^JOAQUIM TAVORA'), 'Joaquim Távora'),
    (_r(r'^JOAQUIN TAVORA'), 'Joaquim Távora'),

    # ── Jacarezinho ───────────────────────────────────────────────────────────
    (_r(r'^JACAREZINHO'), 'Jacarezinho'),
    (_r(r'^JACAREZIMHO'), 'Jacarezinho'),

    # ── Ibaiti ────────────────────────────────────────────────────────────────
    (_r(r'^IBAITI'), 'Ibaiti'),
    (_r(r'^IBITI$'), 'Ibaiti'),
    (_r(r'^BAITI$'), 'Ibaiti'),

    # ── Rancho Alegre ────────────────────────────────────────────────────────
    (_r(r'^RANCHO ALEFRE$'), 'Rancho Alegre'),

    # ── São Jerônimo da Serra ────────────────────────────────────────────────
    (_r(r'^SAO GERONIMO DA SERA$'), 'São Jerônimo da Serra'),
    (_r(r'^SAO GERONIMO$'), 'São Jerônimo da Serra'),

    # ── São Sebastião da Amoreira ────────────────────────────────────────────
    (_r(r'^SAO SEBASTIAO DA MOREIRA$'), 'São Sebastião da Amoreira'),
    (_r(r'^SAO SEBASTIAO AMOREIRA$'), 'São Sebastião da Amoreira'),

    # ── Porecatu ─────────────────────────────────────────────────────────────
    (_r(r'^PORECATU\s+PARANA$'), 'Porecatu'),

    # ── Piraquara ────────────────────────────────────────────────────────────
    (_r(r'^PIRAGUARA PR$'), 'Piraquara'),

    # ── São José dos Pinhais ─────────────────────────────────────────────────
    (_r(r'^SAO JOSE DOS PINHAIS$'), 'São José dos Pinhais'),

    # ── Remaining Paraná cities ───────────────────────────────────────────────
    (_r(r'^JAPIRA'), 'Japira'),
    (_r(r'^JAPIRA P R$'), 'Japira'),
    (_r(r'^QUATIGUA$'), 'Quatiguá'),
    (_r(r'^QUATIGA$'), 'Quatiguá'),
    (_r(r'^PRIMEIRO DE MAIO'), 'Primeiro de Maio'),
    (_r(r'^PRIMEIRO MAIO'), 'Primeiro de Maio'),
    (_r(r'^URAI$'), 'Uraí'),
    (_r(r'^IBIPORA'), 'Ibiporã'),
    (_r(r'^ARAPOTI'), 'Arapoti'),
    (_r(r'^ASSAI$'), 'Assaí'),
    (_r(r'^FIGUEIRA'), 'Figueira'),
    (_r(r'^TOMAZINA'), 'Tomazina'),
    (_r(r'^JANDAIA DO SUL'), 'Jandaia do Sul'),
    (_r(r'^PALMITAL'), 'Palmital'),
    (_r(r'^CURITIBA'), 'Curitiba'),
    (_r(r'^FLORESTOPOLIS'), 'Florestópolis'),
    (_r(r'^CAMBE'), 'Cambé'),
    (_r(r'^SERTANEJA'), 'Sertaneja'),
    (_r(r'^CERTANEJA'), 'Sertaneja'),
    (_r(r'^SERTANOPOLIS'), 'Sertanópolis'),
    (_r(r'^SERTANOPOLES'), 'Sertanópolis'),
    (_r(r'^RIBEIRAO CLARO'), 'Ribeirão Claro'),
    (_r(r'^RIBEIRAO PRETO'), 'Ribeirão Preto'),
    (_r(r'^RIBERAO PRETO'), 'Ribeirão Preto'),
    (_r(r'^SANTA CRUZ DO RIO PARDO'), 'Santa Cruz do Rio Pardo'),
    (_r(r'^SANTA CRUZ RIO PARDO'), 'Santa Cruz do Rio Pardo'),
    (_r(r'^SANTA CECILIA DO PAVAO'), 'Santa Cecília do Pavão'),
    (_r(r'^SANTA CELILIA DO PAVAO'), 'Santa Cecília do Pavão'),
    (_r(r'^SALTO DO ITARARE'), 'Salto do Itararé'),
    (_r(r'^SANTANA ITARARE'), 'Santana de Itararé'),
    (_r(r'^GUAPIRAMA'), 'Guapirama'),
    (_r(r'^RESERVA'), 'Reserva'),
    (_r(r'^PIRAI DO SUL'), 'Piraí do Sul'),
    (_r(r'^TAMARANA'), 'Tamarana'),
    (_r(r'^LOANDA'), 'Loanda'),
    (_r(r'^JATAIZINHO'), 'Jataizinho'),
    (_r(r'^GUARACI'), 'Guaraci'),
    (_r(r'^GUARAPUAVA'), 'Guarapuava'),
    (_r(r'^APUCARANA'), 'Apucarana'),
    (_r(r'^ASTORGA'), 'Astorga'),
    (_r(r'^COLORADO$'), 'Colorado'),
    (_r(r'^COLOMBO'), 'Colombo'),
    (_r(r'^CAMPO LARGO'), 'Campo Largo'),
    (_r(r'^JAGUARIAIVA'), 'Jaguariaíva'),
    (_r(r'^IRATI'), 'Irati'),
    (_r(r'^PINHALAO'), 'Pinhalão'),
    (_r(r'^SAPOPEMA'), 'Sapopema'),
    (_r(r'^ROLANDIA'), 'Rolândia'),
    (_r(r'^MARINGA'), 'Maringá'),
    (_r(r'^UMUARAMA'), 'Umuarama'),
    (_r(r'^UBIRATA'), 'Ubiratã'),
    (_r(r'^PARANAVA'), 'Paranavaí'),
    (_r(r'^MAMBORE'), 'Mamborê'),
    (_r(r'^JURANDA'), 'Juranda'),
    (_r(r'^NOVA SANTA BARBARA'), 'Nova Santa Bárbara'),
    (_r(r'^NOVA AMERICA$'), 'Nova América'),
    (_r(r'^NOVA AURORA$'), 'Nova Aurora'),
    (_r(r'^LEOPOLIS'), 'Leópolis'),
    (_r(r'^CONCELHEIRO MAIRINK'), 'Conselheiro Mairinck'),

    # ── São Paulo state cities ─────────────────────────────────────────────────
    # ── Araçatuba ─────────────────────────────────────────────────────────────
    (_r(r'^ARACATUBA'), 'Araçatuba'),
    # ── Cerquilho ─────────────────────────────────────────────────────────────
    (_r(r'^CERQUIILHO SP$'), 'Cerquilho'),
    # ── Ilha Comprida ─────────────────────────────────────────────────────────
    (_r(r'^ILHA CUMPRIDA$'), 'Ilha Comprida'),
    # ── Jandira ───────────────────────────────────────────────────────────────
    (_r(r'^JANDIRA\s+SPX$'), 'Jandira'),
    # ── Matão ─────────────────────────────────────────────────────────────────
    (_r(r'^MATAO/SP$'), 'Matão'),
    (_r(r'^SAO PAULO'), 'São Paulo'),
    (_r(r'^CAMPINAS'), 'Campinas'),
    (_r(r'^BAURU'), 'Bauru'),
    (_r(r'^OURINHOS'), 'Ourinhos'),
    (_r(r'^PIRAJU'), 'Piraju'),
    (_r(r'^SERTAOZINHO'), 'Sertãozinho'),
    (_r(r'^ASSIS'), 'Assis'),
    (_r(r'^MARILIA'), 'Marília'),
    (_r(r'^MARILHA'), 'Marília'),
    (_r(r'^ITAPETININGA'), 'Itapetininga'),
    (_r(r'^AVARE'), 'Avaré'),
    (_r(r'^CANDIDO MOTA'), 'Cândido Mota'),
    (_r(r'^CERQUEIRA CESAR'), 'Cerqueira César'),
    (_r(r'^FARTURA'), 'Fartura'),
    (_r(r'^PIRAJU'), 'Piraju'),
    (_r(r'^PIRATININGA'), 'Piratininga'),
    (_r(r'^PILAR DO SUL'), 'Pilar do Sul'),
    (_r(r'^BOITUVA'), 'Boituva'),
    (_r(r'^HOLANBRA'), 'Holambra'),
    (_r(r'^RIO CLARO'), 'Rio Claro'),
    (_r(r'^RANCHARIA'), 'Rancharia'),
    (_r(r'^LIMEIRA'), 'Limeira'),
    (_r(r'^JAU$'), 'Jaú'),
    (_r(r'^PONGAI'), 'Pongaí'),
    (_r(r'^PINDORAMA'), 'Pindorama'),
    (_r(r'^PRESIDENTE PRUDENTE'), 'Presidente Prudente'),
    (_r(r'^IRACEMAPOLIS'), 'Iracemápolis'),
    (_r(r'^ITATINGA'), 'Itatinga'),
    (_r(r'^ITAPOA'), 'Itapoá'),
    (_r(r'^JOSE DOS CAMPOS'), 'São José dos Campos'),
    (_r(r'^SANTA CRUZ DO RIO PARDO'), 'Santa Cruz do Rio Pardo'),
    (_r(r'^PRAIA GRANDE'), 'Praia Grande'),
    (_r(r'^IPAUSSU'), 'Ipaussu'),
    (_r(r'^BARIRI'), 'Bariri'),
    (_r(r'^BARRETOS'), 'Barretos'),
    (_r(r'^CHAPECO'), 'Chapecó'),
    (_r(r'^ITU$'), 'Itu'),
    (_r(r'^TAGUAI'), 'Taguaí'),
    (_r(r'^TARUMA'), 'Tarumã'),
    (_r(r'^TAQUARITUBA'), 'Taquarituba'),
    (_r(r'^MINEIROS DO TIETE'), 'Mineiros do Tietê'),
    (_r(r'^VOTUPORANGA'), 'Votuporanga'),
    (_r(r'^SERTAOZINHO'), 'Sertãozinho'),
    (_r(r'^LENCOIS PAULISTA'), 'Lençóis Paulista'),
    # ── Pontal ────────────────────────────────────────────────────────────────
    (_r(r'^SAO JOAQUIM PONTAL$'), 'Pontal'),
    (_r(r'^S JOAQUIM PONTAL$'), 'Pontal'),
    (_r(r'^SAO JO POMTAL$'), 'Pontal'),
    (_r(r'^PONTAL'), 'Pontal'),
    # ── São Bernardo do Campo ────────────────────────────────────────────────
    (_r(r'^SAO B DO CAMPO S P$'), 'São Bernardo do Campo'),

    # ── Mato Grosso / other states ───────────────────────────────────────────
    # ── Luís Eduardo Magalhães ────────────────────────────────────────────────
    (_r(r'^LUIZ DUARDO MAGALHAES BAIA$'), 'Luís Eduardo Magalhães'),
    # ── Poços de Caldas ──────────────────────────────────────────────────────
    (_r(r'^POCOS DE CAUDAS MINAS$'), 'Poços de Caldas'),
    (_r(r'^POCOS CAUDA$'), 'Poços de Caldas'),
    (_r(r'^SINOP'), 'Sinop'),
    (_r(r'^NOVA MUTUM'), 'Nova Mutum'),
    (_r(r'^TANGARA DA SERRA'), 'Tangará da Serra'),
    (_r(r'^SORRISO'), 'Sorriso'),
    (_r(r'^ALTA FLORESTA'), 'Alta Floresta'),
    (_r(r'^CANARANA'), 'Canarana'),
    (_r(r'^CHAPADAO DO SUL'), 'Chapadão do Sul'),
    (_r(r'^CRISTALINA'), 'Cristalina'),
    (_r(r'^SAO GABRIEL DO OESTE'), 'São Gabriel do Oeste'),
    (_r(r'^NAVIRAI'), 'Naviraí'),
    (_r(r'^BOM JESUS'), 'Bom Jesus'),
    (_r(r'^CABIXI'), 'Cabixi'),
    (_r(r'^BURITIS'), 'Buritis'),

    # ── Suffix patterns: prefix_garbage + spaces + CITY ──────────────────────
    # Handles: 'HENRIQUE  BTES', 'PAULO   ITCA', 'ANA     ANDIRA', etc.
    (_r(r'^.+\s+BTES\.?$'), 'Bandeirantes'),
    (_r(r'^.+\s+BANDEIRANTES$'), 'Bandeirantes'),
    (_r(r'^.+\s+BTE$'), 'Bandeirantes'),
    (_r(r'^.+\s+ANDIRA$'), 'Andirá'),
    (_r(r'^.+\s+ITCA\.?$'), 'Itambaracá'),
    (_r(r'^.+\s+ITAMBARACA$'), 'Itambaracá'),
    (_r(r'^.+\s+CAMBARA$'), 'Cambará'),
    (_r(r'^.+\s+SANTA MARIANA$'), 'Santa Mariana'),
    (_r(r'^.+\s+SANTA AMELIA$'), 'Santa Amélia'),
    (_r(r'^.+\s+ABATIA$'), 'Abatiá'),
    (_r(r'^.+\s+CORNELIO$'), 'Cornélio Procópio'),
    (_r(r'^.+\s+BTES\b'), 'Bandeirantes'),   # catch remaining BTES anywhere
]


# ---------------------------------------------------------------------------
# Normalization function
# ---------------------------------------------------------------------------

def normalize(raw: str) -> str | None:
    """Return canonical city name for raw value, or None if no change needed."""
    if not raw or not raw.strip():
        return None

    k = _key(raw)
    if not k:
        return None

    for pattern, canonical in RULES:
        if pattern.match(k):
            stripped = raw.strip()
            # Avoid redundant updates (already the canonical value)
            if stripped == canonical:
                return None
            return canonical

    # Whitespace-only cleanup: if city is correct but has surrounding spaces
    stripped = raw.strip()
    if stripped != raw:
        return stripped if stripped else None

    return None


# ---------------------------------------------------------------------------
# Management command
# ---------------------------------------------------------------------------

class Command(BaseCommand):
    help = (
        'Normalize customer city names: fix abbreviations, typos, encoding '
        'artifacts, and compound garbage from the legacy Access import.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Preview changes without saving to the database.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']

        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN — no changes will be saved.\n'))

        # Collect all distinct city values
        distinct_cities = (
            Customer.objects.values_list('city', flat=True)
            .distinct()
            .order_by('city')
        )

        planned: dict[str, str] = {}   # old_value → new_value
        for raw in distinct_cities:
            new = normalize(raw)
            if new is not None:
                planned[raw] = new

        if not planned:
            self.stdout.write(self.style.SUCCESS('No city names need normalization.'))
            return

        # Group by canonical name for reporting
        by_canonical: dict[str, list[tuple[str, int]]] = {}
        for old, new in planned.items():
            count = Customer.objects.filter(city=old).count()
            by_canonical.setdefault(new, []).append((old, count))

        # Report
        total_records = 0
        total_values = 0
        self.stdout.write(f'\n{"Canonico":<30}  {"Registros":>9}  {"Valores originais"}\n')
        self.stdout.write('-' * 80)
        for canonical in sorted(by_canonical):
            entries = by_canonical[canonical]
            n_records = sum(c for _, c in entries)
            total_records += n_records
            total_values += len(entries)
            self.stdout.write(
                f'{canonical:<30}  {n_records:>9}  '
                + ', '.join(f'{repr(old)}({c})' for old, c in sorted(entries, key=lambda x: -x[1]))
            )

        self.stdout.write('-' * 80)
        self.stdout.write(
            f'\nTotal: {total_records} registros em {total_values} valores unicos '
            f'-> {len(by_canonical)} cidades canonicas.\n'
        )

        if dry_run:
            self.stdout.write(self.style.WARNING('\nDRY RUN -- execute sem --dry-run para aplicar.'))
            return

        # Apply changes
        with transaction.atomic():
            updated = 0
            for old, new in planned.items():
                n = Customer.objects.filter(city=old).update(city=new)
                updated += n
                if n > 0:
                    self.stdout.write(f'  {n:>6}  {repr(old)} -> {repr(new)}')

        self.stdout.write(
            self.style.SUCCESS(f'\nConcluido: {updated} registros atualizados.')
        )
