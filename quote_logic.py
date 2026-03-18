import html as html_lib
import json
import math
import os
import re
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Optional, Tuple

import requests
from weasyprint import HTML

TWOPLACES = Decimal("0.01")

PRODUCT_TABLE = {
    "Tapume 0,55x2,00m": {"peso": Decimal("3.0"), "volume": Decimal("0.011"), "valor": Decimal("20.95")},
    "Tapume 0,55x2,20m": {"peso": Decimal("3.3"), "volume": Decimal("0.012"), "valor": Decimal("22.95")},
    "Tapume 0,55x2,44m": {"peso": Decimal("4.0"), "volume": Decimal("0.013"), "valor": Decimal("29.95")},
    "Telha 0,55x2,00m": {"peso": Decimal("3.2"), "volume": Decimal("0.011"), "valor": Decimal("22.95")},
    "Telha 0,55x2,20m": {"peso": Decimal("3.4"), "volume": Decimal("0.012"), "valor": Decimal("24.95")},
    "Telha 0,55x2,44m": {"peso": Decimal("4.4"), "volume": Decimal("0.013"), "valor": Decimal("29.95")},
}

SIZE_MAP = {
    "2": "2,00",
    "2,0": "2,00",
    "2,00": "2,00",
    "2.00": "2,00",
    "2,20": "2,20",
    "2.20": "2,20",
    "2,2": "2,20",
    "2.2": "2,20",
    "2,44": "2,44",
    "2.44": "2,44",
}

CNPJ_ENDPOINTS = [
    "https://brasilapi.com.br/api/cnpj/v1/{cnpj}",
    "https://www.receitaws.com.br/v1/cnpj/{cnpj}",
    "https://publica.cnpj.ws/cnpj/{cnpj}",
]

ADDRESS_KEYWORDS = [
    "rua", "r.", "avenida", "av.", "estrada", "rodovia", "travessa", "tv.",
    "alameda", "praça", "praca", "bairro", "cep", "km", "rod.", "br-"
]

DELIVERY_KEYWORDS = [
    "endereço entrega", "endereco entrega", "endereço de entrega",
    "endereco de entrega", "entrega:"
]

lock = Lock()


def d(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if value is None or value == "":
        return Decimal("0")
    text = str(value).strip()
    text = text.replace("R$", "").replace(".", "").replace(" ", "").replace(",", ".")
    return Decimal(text)


def money(value: Decimal) -> str:
    value = value.quantize(TWOPLACES, rounding=ROUND_HALF_UP)
    s = f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {s}"


def fmt_decimal(value: Decimal, places: int = 3) -> str:
    q = Decimal("1") if places == 0 else Decimal("1." + ("0" * places))
    value = value.quantize(q, rounding=ROUND_HALF_UP)
    txt = f"{value:.{places}f}".replace(".", ",")
    return txt


def digits_only(text: str) -> str:
    return re.sub(r"\D", "", text or "")


def format_cnpj(cnpj: str) -> str:
    digits = digits_only(cnpj)
    if len(digits) != 14:
        return cnpj or "não informado"
    return f"{digits[:2]}.{digits[2:5]}.{digits[5:8]}/{digits[8:12]}-{digits[12:]}"


def format_cpf(cpf: str) -> str:
    digits = digits_only(cpf)
    if len(digits) != 11:
        return cpf or "não informado"
    return f"{digits[:3]}.{digits[3:6]}.{digits[6:9]}-{digits[9:]}"


def format_doc(doc: str) -> str:
    digits = digits_only(doc)
    if len(digits) == 14:
        return format_cnpj(doc)
    if len(digits) == 11:
        return format_cpf(doc)
    return doc or "não informado"


def format_cep(cep: str) -> str:
    digits = digits_only(cep)
    if len(digits) != 8:
        return cep or ""
    return f"{digits[:5]}-{digits[5:]}"


def br_date(dt: date) -> str:
    return dt.strftime("%d/%m/%Y")


def sanitize_html(value: str) -> str:
    return html_lib.escape(value or "").replace("\n", "<br>")


def extract_cnpj(text: str) -> str:
    match = re.search(r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b", text or "")
    return format_cnpj(match.group(0)) if match else "não informado"


def normalize_product(raw_name: str, raw_size: str) -> str:
    name = (raw_name or "").strip().lower()
    size = SIZE_MAP.get(
        (raw_size or "").replace("m", "").strip(),
        (raw_size or "").replace("m", "").strip().replace(".", ","),
    )
    if "tap" in name:
        base = "Tapume"
    elif "telh" in name:
        base = "Telha"
    else:
        return raw_name.strip() or "Produto não informado"
    return f"{base} 0,55x{size}m"


def join_address(parts: List[str]) -> str:
    cleaned = [
        p.strip(" ,-/")
        for p in parts
        if p and str(p).strip() and str(p).strip().lower() not in {"s/n", "sn"}
    ]
    return ", ".join(cleaned) if cleaned else "não informado"


def normalize_spaces(text: str) -> str:
    text = (text or "").replace("\u00a0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\s+\n", "\n", text)
    return text.strip()


def preprocess_text(raw_text: str) -> List[str]:
    text = normalize_spaces(raw_text)
    lines = [line.strip(" -\t") for line in text.splitlines()]
    return [line for line in lines if line.strip()]


def strip_known_label(text: str) -> str:
    t = (text or "").strip()

    patterns = [
        r"^(nome)\s*:\s*",
        r"^(cliente)\s*:\s*",
        r"^(cpf\/cnpj)\s*:\s*",
        r"^(cpf)\s*:\s*",
        r"^(cnpj)\s*:\s*",
        r"^(endereço de entrega)\s*:\s*",
        r"^(endereco de entrega)\s*:\s*",
        r"^(endereço entrega)\s*:\s*",
        r"^(endereco entrega)\s*:\s*",
        r"^(endereço)\s*:\s*",
        r"^(endereco)\s*:\s*",
        r"^(frete)\s*:\s*",
        r"^(valor negociado)\s*:\s*",
        r"^(número da cotação)\s*:\s*",
        r"^(numero da cotação)\s*:\s*",
        r"^(numero da cotacao)\s*:\s*",
        r"^(cotação)\s*:\s*",
        r"^(cotacao)\s*:\s*",
        r"^(prazo de entrega)\s*:\s*",
        r"^(observações)\s*:\s*",
        r"^(observacoes)\s*:\s*",
    ]

    for pattern in patterns:
        t = re.sub(pattern, "", t, flags=re.IGNORECASE).strip()

    return t


def extract_document_from_text(text: str) -> Tuple[Optional[str], str]:
    original = (text or "").strip()

    m = re.search(r"\b\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}\b", original)
    if m:
        doc = format_cnpj(m.group(0))
        cleaned = (original[:m.start()] + " " + original[m.end():]).strip(" -,")
        return doc, normalize_spaces(cleaned)

    m = re.search(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b", original)
    if m:
        doc = format_cpf(m.group(0))
        cleaned = (original[:m.start()] + " " + original[m.end():]).strip(" -,")
        return doc, normalize_spaces(cleaned)

    m = re.search(r"(?<!\d)\d{14}(?!\d)", original)
    if m:
        doc = format_cnpj(m.group(0))
        cleaned = (original[:m.start()] + " " + original[m.end():]).strip(" -,")
        return doc, normalize_spaces(cleaned)

    m = re.search(r"(?<!\d)\d{11}(?!\d)", original)
    if m:
        doc = format_cpf(m.group(0))
        cleaned = (original[:m.start()] + " " + original[m.end():]).strip(" -,")
        return doc, normalize_spaces(cleaned)

    return None, original


def looks_like_address(text: str) -> bool:
    t = (text or "").lower()
    return any(keyword in t for keyword in ADDRESS_KEYWORDS)


def looks_like_delivery_address(text: str) -> bool:
    t = (text or "").lower()
    return any(keyword in t for keyword in DELIVERY_KEYWORDS)


def looks_like_product_line(text: str) -> bool:
    t = (text or "").lower()
    return bool(re.search(r"\b\d+\s+(tapume|tapumes|telha|telhas)\b", t))


def classify_customer_line(text: str) -> Dict[str, str]:
    raw = (text or "").strip()
    no_label = strip_known_label(raw)

    found_doc, remainder = extract_document_from_text(no_label)

    result: Dict[str, str] = {}

    if found_doc:
        result["cliente_doc"] = found_doc

    remainder = remainder.strip(" -,")

    if remainder and not looks_like_address(remainder) and not looks_like_product_line(remainder):
        remainder = re.sub(r"\s*-\s*(cpf|cnpj)\b.*$", "", remainder, flags=re.IGNORECASE).strip()
        result["cliente_nome"] = normalize_spaces(remainder)

    return result


def classify_address_line(text: str) -> Dict[str, str]:
    raw = (text or "").strip()
    lower = raw.lower()
    value = strip_known_label(raw)

    if looks_like_delivery_address(lower):
        return {"cliente_endereco_entrega": normalize_spaces(value)}

    if looks_like_address(value):
        return {"cliente_endereco": normalize_spaces(value)}

    return {}


class QuoteBuilder:
    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir)
        self.generated_dir = self.base_dir / "generated"
        self.data_dir = self.base_dir / "data"
        self.counter_file = Path(os.getenv("COUNTER_FILE", self.data_dir / "last_number.txt"))
        self.initial_number = int(os.getenv("INITIAL_QUOTE_NUMBER", "1500"))

    def next_number(self) -> int:
        self.counter_file.parent.mkdir(parents=True, exist_ok=True)
        with lock:
            if self.counter_file.exists():
                current = int(self.counter_file.read_text(encoding="utf-8").strip() or self.initial_number)
            else:
                current = self.initial_number
            nxt = current + 1
            self.counter_file.write_text(str(nxt), encoding="utf-8")
        return nxt

    def fetch_cnpj_data(self, cnpj: str) -> Dict[str, str]:
        digits = digits_only(cnpj)
        if len(digits) != 14:
            return {}
        headers = {"User-Agent": "Mozilla/5.0"}
        for url in CNPJ_ENDPOINTS:
            try:
                response = requests.get(url.format(cnpj=digits), headers=headers, timeout=15)
                if response.status_code != 200:
                    continue
                data = response.json()
                normalized = self.normalize_cnpj_payload(data)
                if normalized:
                    return normalized
            except Exception:
                continue
        return {}

    def normalize_cnpj_payload(self, data: Dict[str, Any]) -> Dict[str, str]:
        if not isinstance(data, dict):
            return {}

        nome = (
            data.get("razao_social")
            or data.get("nome")
            or data.get("company", {}).get("name")
            or data.get("estabelecimento", {}).get("nome_fantasia")
            or ""
        )
        endereco_data = data.get("estabelecimento", data)

        logradouro = (
            endereco_data.get("tipo_logradouro", "").strip() + " " + endereco_data.get("logradouro", "").strip()
        ).strip() or endereco_data.get("logradouro") or ""
        numero = endereco_data.get("numero") or ""
        complemento = endereco_data.get("complemento") or ""
        bairro = endereco_data.get("bairro") or ""
        municipio = (
            endereco_data.get("cidade")
            or endereco_data.get("municipio")
            or endereco_data.get("cidade_exterior")
            or ""
        )
        uf = endereco_data.get("estado") or endereco_data.get("uf") or ""
        cep = endereco_data.get("cep") or ""

        endereco = join_address([
            logradouro,
            numero,
            complemento,
            bairro,
            f"{municipio}/{uf}" if municipio and uf else municipio or uf,
            format_cep(str(cep)),
        ])

        return {
            "nome": nome or "",
            "logradouro": logradouro or "",
            "numero": numero or "",
            "complemento": complemento or "",
            "bairro": bairro or "",
            "municipio": municipio or "",
            "uf": uf or "",
            "cep": format_cep(str(cep)),
            "endereco": endereco,
        }

    def parse_text(self, text: str) -> Dict[str, Any]:
        lines = preprocess_text(text)
        cnpj_extraido = extract_cnpj(text)

        data: Dict[str, Any] = {
            "cliente_nome": "",
            "cliente_doc": "",
            "cliente_endereco": "",
            "cliente_endereco_entrega": "não informado",
            "frete": Decimal("0"),
            "valor_negociado": None,
            "prazo_entrega": None,
            "numero_cotacao": None,
            "items": [],
            "observacoes_adicionais": [],
            "texto_original": text,
        }

        item_pattern = re.compile(
            r"(?P<qtd>\d+)\s+(?P<produto>tapumes?|telhas?)\s+(?P<medida>\d+[.,]?\d*)m?\b",
            re.I,
        )

        for line in lines:
            line_lower = line.lower()

            m = item_pattern.search(line)
            if m:
                qtd = int(m.group("qtd"))
                produto = normalize_product(m.group("produto"), m.group("medida"))
                data["items"].append({
                    "produto": produto,
                    "quantidade": qtd,
                })
                continue

            address_info = classify_address_line(line)
            if address_info:
                for k, v in address_info.items():
                    if v:
                        data[k] = v
                continue

            if "frete" in line_lower:
                nums = re.findall(r"[\d\.,]+", line)
                if nums:
                    data["frete"] = d(nums[-1])
                continue

            if "valor negociado" in line_lower:
                nums = re.findall(r"[\d\.,]+", line)
                if nums:
                    data["valor_negociado"] = d(nums[-1])
                continue

            if "prazo de entrega" in line_lower:
                data["prazo_entrega"] = strip_known_label(line) or None
                continue

            if (
                "número da cotação" in line_lower
                or "numero da cotação" in line_lower
                or "numero da cotacao" in line_lower
            ):
                data["numero_cotacao"] = strip_known_label(line) or None
                continue

            customer_info = classify_customer_line(line)
            if customer_info:
                if customer_info.get("cliente_nome") and not data["cliente_nome"]:
                    data["cliente_nome"] = customer_info["cliente_nome"]

                if customer_info.get("cliente_doc") and not data["cliente_doc"]:
                    data["cliente_doc"] = customer_info["cliente_doc"]
                continue

            data["observacoes_adicionais"].append(line)

        if not data["cliente_doc"] and cnpj_extraido != "não informado":
            data["cliente_doc"] = cnpj_extraido

        if not data["cliente_nome"] and lines:
            first_line = strip_known_label(lines[0])
            _, first_line_clean = extract_document_from_text(first_line)
            first_line_clean = re.sub(
                r"\s*-\s*(cpf|cnpj)\b.*$",
                "",
                first_line_clean,
                flags=re.IGNORECASE,
            ).strip()
            if first_line_clean and not looks_like_product_line(first_line_clean):
                data["cliente_nome"] = normalize_spaces(first_line_clean)

        data["cliente_nome"] = data["cliente_nome"] or "não informado"
        data["cliente_doc"] = data["cliente_doc"] or "não informado"
        data["cliente_endereco"] = data["cliente_endereco"] or "não informado"
        data["cliente_endereco_entrega"] = data["cliente_endereco_entrega"] or "não informado"

        return data

    def build(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        text = (payload.get("texto") or payload.get("mensagem") or "").strip()
        extracted = self.parse_text(text) if text else {}

        cnpj = payload.get("cliente_doc") or extracted.get("cliente_doc") or "não informado"
        cnpj_data = self.fetch_cnpj_data(cnpj) if len(digits_only(cnpj)) == 14 else {}

        cliente_nome = (
            payload.get("cliente_nome")
            or cnpj_data.get("nome")
            or extracted.get("cliente_nome")
            or "não informado"
        )
        cliente_endereco = normalize_spaces(
            payload.get("cliente_endereco")
            or extracted.get("cliente_endereco")
            or cnpj_data.get("endereco")
            or "não informado"
        )

        entrega = (
            payload.get("cliente_endereco_entrega")
            or extracted.get("cliente_endereco_entrega")
            or "não informado"
        )
        frete = d(payload.get("frete") or extracted.get("frete") or "0")
        valor_negociado = payload.get("valor_negociado")
        if valor_negociado is None:
            valor_negociado = extracted.get("valor_negociado")
        valor_negociado = d(valor_negociado) if valor_negociado is not None else None

        items = payload.get("items") or extracted.get("items") or []
        linhas = []
        subtotal = Decimal("0")
        desconto = Decimal("0")

        for item in items:
            produto = item.get("produto", "Produto não informado")
            quantidade = int(item.get("quantidade", 0))
            oficial = PRODUCT_TABLE.get(produto)
            if not oficial:
                raise ValueError(f"Produto não cadastrado: {produto}")

            unit = oficial["valor"]
            peso_total = oficial["peso"] * quantidade
            volume_total = oficial["volume"] * quantidade
            total = unit * quantidade
            subtotal += total

            if valor_negociado is not None and valor_negociado < unit:
                desconto += (unit - valor_negociado) * quantidade

            linhas.append({
                "produto": produto,
                "quantidade": quantidade,
                "unitario": unit,
                "peso_total": peso_total,
                "volume_total": volume_total,
                "total": total,
            })

        total_geral = subtotal - desconto + frete
        numero_orcamento = self.next_number()

        observacoes = []
        if entrega and entrega != "não informado":
            observacoes.append(
                "<strong>⚠ ENDEREÇO DE ENTREGA:</strong><br>"
                f"<strong>{sanitize_html(entrega)}</strong>"
            )
        if extracted.get("numero_cotacao"):
            observacoes.append(
                f"<strong>Número da cotação:</strong> {sanitize_html(extracted['numero_cotacao'])}"
            )
        if extracted.get("prazo_entrega"):
            observacoes.append(
                f"<strong>Prazo de entrega:</strong> {sanitize_html(extracted['prazo_entrega'])}"
            )
        for line in extracted.get("observacoes_adicionais", []):
            observacoes.append(sanitize_html(line))

        doc_formatado = format_doc(cnpj)

        return {
            "numero_orcamento": numero_orcamento,
            "data": br_date(date.today()),
            "validade": br_date(date.today() + timedelta(days=7)),
            "cliente": {
                "nome": cliente_nome or "não informado",
                "doc": doc_formatado,
                "endereco": cliente_endereco or "não informado",
                "endereco_entrega": entrega or "não informado",
            },
            "itens": linhas,
            "observacoes_html": "<br><br>".join(observacoes) if observacoes else "não informado",
            "resumo": {
                "subtotal": subtotal,
                "frete": frete,
                "desconto": desconto,
                "total_geral": total_geral,
            },
        }

    def make_rows_html(self, linhas: List[Dict[str, Any]]) -> str:
        rows = []
        for item in linhas:
            rows.append(
                f"""
                <tr>
                    <td class="col-prod">{sanitize_html(item['produto'])}</td>
                    <td class="num col-qtd">{item['quantidade']}</td>
                    <td class="num col-unit">{money(item['unitario'])}</td>
                    <td class="num col-peso">{fmt_decimal(item['peso_total'], 1)} kg</td>
                    <td class="num col-m3">{fmt_decimal(item['volume_total'], 3)} m³</td>
                    <td class="num col-total">{money(item['total'])}</td>
                </tr>
                """
            )
        return "\n".join(rows)

    def render_official_html(self, template_html: str, quote: Dict[str, Any]) -> str:
        replacements = {
            "{{numero_orcamento}}": str(quote["numero_orcamento"]),
            "{{data}}": quote["data"],
            "{{cliente_nome}}": sanitize_html(quote["cliente"]["nome"]),
            "{{cliente_doc}}": sanitize_html(quote["cliente"]["doc"]),
            "{{cliente_endereco}}": sanitize_html(quote["cliente"]["endereco"]),
            "{{validade}}": quote["validade"],
            "{{linhas_itens}}": self.make_rows_html(quote["itens"]),
            "{{subtotal}}": money(quote["resumo"]["subtotal"]),
            "{{frete}}": money(quote["resumo"]["frete"]),
            "{{desconto}}": money(quote["resumo"]["desconto"]),
            "{{total_geral}}": money(quote["resumo"]["total_geral"]),
            "{{observacoes_dinamicas}}": quote["observacoes_html"],
        }

        html_out = template_html
        asset_root = self.base_dir / "assets"
        html_out = html_out.replace("logo_ecotap.png", str((asset_root / "logo_ecotap.png").as_uri()))
        html_out = html_out.replace("logo_GreenWall.png", str((asset_root / "logo_GreenWall.png").as_uri()))
        html_out = html_out.replace("qr_pix.png", str((asset_root / "qr_pix.png").as_uri()))

        for old, new in replacements.items():
            html_out = html_out.replace(old, str(new))
        return html_out

    def write_outputs(self, html: str, numero_orcamento: int):
        self.generated_dir.mkdir(parents=True, exist_ok=True)
        html_path = self.generated_dir / f"orcamento_{numero_orcamento}.html"
        pdf_path = self.generated_dir / f"orcamento_{numero_orcamento}.pdf"
        html_path.write_text(html, encoding="utf-8")
        HTML(string=html, base_url=str(self.base_dir)).write_pdf(str(pdf_path))
        return html_path, pdf_path
