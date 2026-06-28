from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from weasyprint import HTML

from app.config import TEMPLATES_DIR
from app.schemas import InvoiceTemplateLanguage

template_environment = Environment(
    loader=FileSystemLoader(TEMPLATES_DIR),
    autoescape=select_autoescape(["html", "xml"]),
)

INVOICE_TEMPLATE_FILES: dict[InvoiceTemplateLanguage, str] = {
    "ru": "invoice.html",
    "en": "invoice_en.html",
}


def generate_invoice_pdf(
    template_context: dict,
    destination: Path,
    template_language: InvoiceTemplateLanguage = "ru",
) -> None:
    template_file = INVOICE_TEMPLATE_FILES[template_language]
    template = template_environment.get_template(template_file)
    rendered_html = template.render(**template_context)
    destination.parent.mkdir(parents=True, exist_ok=True)
    HTML(string=rendered_html, base_url=str(TEMPLATES_DIR)).write_pdf(destination)
